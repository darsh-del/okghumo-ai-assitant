"""WhatsApp bot: Meta webhook -> OkGhumo phone link -> the same read-only assistant as /chat.

Identity = the sender phone number in Meta's SIGNED webhook (never message text). OkGhumo owns the
phone<->vendor link (contract: POST /api/users/whatsapp-link{,/redeem,/token,/revoke}); every question
asks OkGhumo for a short-lived token for that phone, so there is no local binding or token to go stale.
Never log message text, link codes, secrets or tokens.

ponytail: handlers run in-process (BackgroundTasks) and history/rate limits live in memory, so a restart
drops queued work and chat context. Move to a queue + Redis before more than one process/host.
"""
import hashlib
import hmac
import json
import logging
import os
import random
import re
import threading
import time
from contextlib import closing

import httpx
import jwt
from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from fastapi.responses import PlainTextResponse

from app import assistant, guard, lang, logs, ratelimit, reports, sanitize, schedules, voice
from app.db import connect, run

log = logging.getLogger("whatsapp")
router = APIRouter()

ROLES = {"vendor", "superVendor"}  # admin/superAdmin can link at OkGhumo but are refused here
SYSTEM = (
    assistant.SYSTEM + " You are replying on WhatsApp, read on a phone: be brief. Aim for 2-5 short lines "
    "(under ~60 words) and lead with the answer or key number. Plain text only: no markdown tables or "
    "headings; *bold* is fine; at most 3 short bullet lines. Friendly, simple wording; use the vendor's "
    "language. If a name matches several agents/activities, don't list them all: pick the best match if it's "
    "clear, otherwise offer the top 3 and ask one short question. Don't narrate what you checked or add "
    "extra offers. If you can't find something, say so in one line and suggest one next step. "
    "Never include customer phone numbers or emails."
)


# ---- OkGhumo phone-link client (token mint / link state only, never business data) ----
class NotLinked(Exception): ...
class LinkInvalid(Exception): ...  # account no longer allowed
class BadCode(Exception): ...


def _link(path: str, **body) -> dict:
    r = httpx.post(
        os.environ.get("OKGHUMO_AUTH_URL", "https://prod.okghumo.com") + "/api/users/whatsapp-link" + path,
        json=body, headers={"X-Whatsapp-Link-Secret": os.environ["WHATSAPP_LINK_SECRET"]}, timeout=15)
    if r.status_code == 401:
        msg = (r.json().get("message") or "").lower() if r.headers.get("content-type", "").startswith("application/json") else ""
        if "not linked" in msg: raise NotLinked
        if "no longer valid" in msg: raise LinkInvalid
        if "invalid or expired" in msg: raise BadCode
    r.raise_for_status()  # wrong secret (401 Unauthorized), 400, 503, 5xx -> generic failure
    return r.json()


def start(phone: str) -> str: return _link("", phone=phone)["connectUrl"]
def redeem(phone: str, code: str) -> dict: return _link("/redeem", phone=phone, code=code)
def token(phone: str) -> dict: return _link("/token", phone=phone)
def revoke(phone: str) -> None: _link("/revoke", phone=phone)


# ---- phone -> OkGhumo account binding ----
def account_id(mcp_token: str | None) -> str | None:
    """Who the minted MCP token belongs to (its `sub`). Read WITHOUT verifying: used only to notice that a number is now
    linked to a different account, never to grant anything. None = can't tell (then we change nothing)."""
    try:
        sub = jwt.decode(mcp_token or "", options={"verify_signature": False}).get("sub")
    except jwt.PyJWTError:
        return None
    return str(sub) if sub else None


def wipe(phone: str) -> None:
    """Forget everything we hold for this number: schedules, pending proposals, undelivered inbox rows, chat context."""
    _hist.pop(phone, None)
    for tbl in ("tasks", "proposals", "inbox"):
        run(f"DELETE FROM {tbl} WHERE vendor_id=?", ("wa:" + phone,))


def bind(phone: str, mcp_token: str | None) -> bool:
    """Call whenever OkGhumo has just minted a token for this phone. If the number is now linked to a DIFFERENT account
    than before (re-linked, recycled number), everything scheduled under the old account is wiped so the new owner never
    receives reports or reminders somebody else set up. Returns False when it had to wipe."""
    acct = account_id(mcp_token)
    if not acct:
        return True
    row = run("SELECT account FROM wa_account WHERE phone=?", (phone,))
    changed = bool(row) and row[0]["account"] != acct
    if changed:
        wipe(phone)
        logs.event(log, "account_changed", logging.WARNING, phone=logs.mask_phone(phone))
    run("INSERT OR REPLACE INTO wa_account(phone, account) VALUES(?,?)", (phone, acct))
    return not changed


# ---- sending ----
def _graph(payload: dict) -> None:
    url = f"https://graph.facebook.com/{os.environ.get('WHATSAPP_API_VERSION', 'v23.0')}/{os.environ['WHATSAPP_PHONE_NUMBER_ID']}/messages"
    httpx.post(url, json={"messaging_product": "whatsapp", **payload}, timeout=15,
               headers={"Authorization": "Bearer " + os.environ["WHATSAPP_TOKEN"]}).raise_for_status()


def send(to: str, text: str) -> None:
    for i in range(0, max(len(text), 1), 4000):  # WhatsApp text limit is 4096
        _graph({"to": to, "type": "text", "text": {"body": text[i:i + 4000]}})


def react(to: str, wamid: str, emoji: str) -> None:
    _graph({"to": to, "type": "reaction", "reaction": {"message_id": wamid, "emoji": emoji}})


PDF_WORDS = re.compile(r"\bpdf\b", re.I)


def _maybe_react(phone: str, wamid: str, why: str, chance: float = 1 / 2) -> bool:
    """Cosmetic 👍 with the given chance (PDF messages 1/2, other messages 1/4), never required and never raises. The outcome is logged (never any text) so a
    missing reaction is explainable: skipped_dice = the roll said no, sent = Meta accepted it, failed = Meta refused."""
    if random.random() >= chance:
        logs.event(log, "reaction", phone=logs.mask_phone(phone), why=why, outcome="skipped_dice")
        return False
    try:
        react(phone, wamid, "👍")
        logs.event(log, "reaction", phone=logs.mask_phone(phone), why=why, outcome="sent")
        return True
    except Exception as e:
        logs.event(log, "reaction", logging.WARNING, phone=logs.mask_phone(phone), why=why, outcome="failed", error=type(e).__name__)
        return False


def send_pdf(to: str, pdf: bytes, title: str, caption: str) -> None:
    """Upload our own generated PDF to Meta, then send it as a document (caption max 1024). Never a vendor-supplied file."""
    base = f"https://graph.facebook.com/{os.environ.get('WHATSAPP_API_VERSION', 'v23.0')}/{os.environ['WHATSAPP_PHONE_NUMBER_ID']}"
    auth = {"Authorization": "Bearer " + os.environ["WHATSAPP_TOKEN"]}
    up = httpx.post(base + "/media", headers=auth, timeout=30, data={"messaging_product": "whatsapp", "type": "application/pdf"},
                    files={"file": ("report.pdf", pdf, "application/pdf")})
    up.raise_for_status()
    name = (re.sub(r"[^A-Za-z0-9 _-]", "", title).strip()[:40] or "report") + ".pdf"
    _graph({"to": to, "type": "document", "document": {"id": up.json()["id"], "filename": name, "caption": caption[:1000]}})


def _push(phone: str, title: str, body: str, pdf: bytes | None = None) -> None:
    """One scheduled item to the phone: a PDF report goes out as ONE document message (text in the caption)."""
    if pdf:
        send_pdf(phone, pdf, title, f"*{title}*\n{body}")
    else:
        send(phone, f"*{title}*\n{body}")


# ---- small in-memory state (see ponytail note) ----
_hits = ratelimit._hits  # one shared sliding-window store (tests clear it)
_hist: dict[str, tuple[float, list]] = {}  # phone -> (last activity, messages); keyed by phone = per vendor


def allow(key: str, n: int, per: int) -> bool:
    return ratelimit.hit(key, n, per) == 0


def _messages(phone: str) -> list:
    last, msgs = _hist.get(phone, (0, []))
    return msgs if time.time() - last < 1800 else []  # 30 min of context


def _remember(phone: str, msgs: list) -> None:
    msgs = msgs[-8:]
    while msgs and msgs[0]["role"] != "user":
        msgs = msgs[1:]
    _hist[phone] = (time.time(), msgs)


def _first_seen(wamid: str) -> bool:
    """Meta retries webhooks; process each message id once."""
    with closing(connect()) as c:
        c.execute("DELETE FROM wa_seen WHERE at < ?", (time.time() - 7 * 86400,))
        return c.execute("INSERT OR IGNORE INTO wa_seen(id, at) VALUES(?,?)", (wamid, time.time())).rowcount == 1


# ---- the conversation ----
def _start_link(phone: str) -> None:
    if not allow(f"start:{phone}", 5, 3600):
        logs.event(log, "rate_limited", logging.WARNING, scope="wa_link", phone=logs.mask_phone(phone))
        return send(phone, "Too many link requests. Please try again in an hour.")
    logs.event(log, "link_started", phone=logs.mask_phone(phone))
    send(phone, "Hi! To use the Okghumo assistant, link your account first. Open this link (valid 10 minutes), "
                "log in, then tap the button to come back here and press Send:\n" + start(phone))


def _redeem(phone: str, code: str) -> None:
    if not allow(f"redeem:{phone}", 5, 3600):
        logs.event(log, "rate_limited", logging.WARNING, scope="wa_redeem", phone=logs.mask_phone(phone))
        return send(phone, "Too many attempts. Please try again in an hour.")
    try:
        t = redeem(phone, code)
    except BadCode:
        logs.event(log, "link_failed", logging.WARNING, reason="bad_code", phone=logs.mask_phone(phone))
        return send(phone, "That code is invalid or expired. Send *hi* to get a new link.")
    except LinkInvalid:
        logs.event(log, "link_failed", logging.WARNING, reason="account_invalid", phone=logs.mask_phone(phone))
        return send(phone, "This account can't use the assistant.")
    if t.get("role") not in ROLES:
        logs.event(log, "link_failed", logging.WARNING, reason="role_not_allowed", phone=logs.mask_phone(phone),
                   role=t.get("role"))
        revoke(phone)
        return send(phone, "This account type can't use the WhatsApp assistant.")
    logs.event(log, "link_redeemed", phone=logs.mask_phone(phone), role=t.get("role"))
    send(phone, "Connected ✅ Ask me about your sales, bookings and collections. Send *STOP* to unlink.")


def _answer(phone: str, text: str, wamid: str, voice: bool = False) -> None:
    if not allow(f"burst:{phone}", int(os.environ.get("RATE_WA_PER_MIN", 6)), 60):  # fast spam: no token mint, no model
        logs.event(log, "rate_limited", logging.WARNING, scope="wa_msg", window="min", phone=logs.mask_phone(phone))
        return send(phone, lang.reply("rate_limited", text))
    if not allow(f"msg:{phone}", int(os.environ.get("RATE_WA_PER_HOUR", 20)), 3600):
        logs.event(log, "rate_limited", logging.WARNING, scope="wa_msg", window="hour", phone=logs.mask_phone(phone))
        return send(phone, "You're sending a lot of messages. Please try again in a little while.")
    try:
        t = token(phone)  # OkGhumo decides who this phone is; fresh short-lived token every question
    except NotLinked:
        return _start_link(phone)
    except LinkInvalid:
        logs.event(log, "message_denied", logging.WARNING, reason="link_invalid", phone=logs.mask_phone(phone))
        return send(phone, "This account can no longer use the assistant. Please contact OkGhumo support.")
    if t.get("role") not in ROLES:
        return send(phone, "This account type can't use the WhatsApp assistant.")
    if not t.get("mcpAccessToken"):  # fail closed: no token, no model call
        logs.event(log, "message_denied", logging.ERROR, reason="no_mcp_token", phone=logs.mask_phone(phone))
        return send(phone, "The assistant isn't available right now. Please try again later.")
    bind(phone, t["mcpAccessToken"])  # before anything reads this phone's schedules/proposals
    try:
        _graph({"status": "read", "message_id": wamid, "typing_indicator": {"type": "text"}})
    except Exception:
        pass  # cosmetic
    asked_pdf = bool(PDF_WORDS.search(text))
    reacted = _maybe_react(phone, wamid, "pdf_word", 1 / 2) if asked_pdf else _maybe_react(phone, wamid, "chat", 1 / 4)
    vendor, msgs = "wa:" + phone, _messages(phone) + [{"role": "user", "content": text}]  # own namespace per phone
    if voice:  # a mis-heard word must never confirm or cancel anything: only TYPED yes/no acts on a proposal
        if len(msgs) > 1 and schedules.MARK in msgs[-2]["content"] and schedules._norm(text) in schedules.YES | schedules.NO:
            return send(phone, "For safety, please type YES or NO as text to confirm.")  # history untouched: typed reply still works
        done = None
    else:
        done = schedules.intercept(vendor, t["role"], msgs, where="here on WhatsApp")  # YES/NO to OUR prompt: code, no model
    if done is not None:
        logs.event(log, "wa_reply", phone=logs.mask_phone(phone), source="confirmation", model_called=False)
        _remember(phone, msgs + [{"role": "assistant", "content": done}])
        return send(phone, done)
    if flush(phone) and len(text) <= 10:  # a ping that only opened the 24h window: reports are enough
        return
    blocked = guard.classify(msgs, guard.VOICE_MAX_CHARS if voice else guard.MAX_MSG_CHARS)  # off-topic / injection / oversized / greeting: canned polite reply, no model call
    if blocked:
        logs.event(log, "wa_reply", phone=logs.mask_phone(phone), source="guard", reason=blocked[0], model_called=False)
        _remember(phone, msgs + [{"role": "assistant", "content": blocked[1]}])
        return send(phone, blocked[1])
    sched = schedules.for_vendor(vendor, t["role"])
    client = assistant.get_client()
    rep = reports.for_vendor(client, vendor, t["role"], t["mcpAccessToken"])  # on-demand PDF report tool
    out = assistant.ask(client, t["role"], t["mcpAccessToken"], msgs,
                        SYSTEM + (schedules.SCHED_SYSTEM + schedules.WA_SCHED if sched else "") + (reports.ASK_SYSTEM if rep else ""),
                        extra=reports.Combo(sched, rep) if (sched or rep) else None)
    clean = sanitize.clean_reply(out["reply"], msgs)  # presentation only: ids, dashes, emoji cap (digits untouched)
    reply = sched.finish(clean) if sched else clean
    reply = reply or "Sorry, I couldn't find an answer to that."
    logs.event(log, "wa_reply", phone=logs.mask_phone(phone), source="model", model_called=True, stop=out["stop_reason"],
               tools=",".join(out["tools_used"]) or None, reply_chars=len(reply))
    _remember(phone, msgs + [{"role": "assistant", "content": reply}])
    send(phone, reply)
    if rep and rep.pdf:  # the report the vendor chose: pushed as a document (never stored for WhatsApp vendors)
        try:
            send_pdf(phone, rep.pdf[1], rep.pdf[0], "*" + rep.pdf[0] + "*")
        except Exception as e:
            logs.event(log, "delivery_failed", logging.ERROR, phone=logs.mask_phone(phone), via="pdf", error=type(e).__name__)
            send(phone, "Sorry, I couldn't attach the PDF just now. Please ask me again in a minute, or I can share the key figures here.")
        else:
            if not asked_pdf and not reacted:  # no "pdf" in the message (e.g. "and another of the months sales"), but one was sent
                _maybe_react(phone, wamid, "pdf_sent", 1 / 2)


# ---- scheduled delivery ----
def deliver(inbox_id: int, vendor: str, title: str, body: str, pdf: bytes | None = None) -> None:
    """Send one inbox row to its WhatsApp owner. Free text only works inside Meta's 24h window; outside it we send
    an approved template (WHATSAPP_REPORT_TEMPLATE, one text variable = the title) and keep the row unsent, so
    flush() delivers it when the vendor next writes. Never raises."""
    if not vendor.startswith("wa:"):
        return
    phone = vendor[3:]
    try:
        _push(phone, title, body, pdf)
        run("UPDATE inbox SET sent=1 WHERE id=? AND vendor_id=?", (inbox_id, vendor))
        logs.event(log, "delivered", phone=logs.mask_phone(phone), inbox=inbox_id, via="text")
    except httpx.HTTPStatusError:  # window closed (or Meta refused)
        tpl, pn = os.environ.get("WHATSAPP_REPORT_TEMPLATE"), os.environ.get("WHATSAPP_TEMPLATE_PARAM")  # pn: named variable
        if tpl:
            try:
                _graph({"to": phone, "type": "template", "template": {
                    "name": tpl, "language": {"code": os.environ.get("WHATSAPP_TEMPLATE_LANG", "en")},
                    "components": [{"type": "body", "parameters": [{"type": "text", "text": " ".join(title.split())[:60],
                                                                **({"parameter_name": pn} if pn else {})}]}]}})
                logs.event(log, "delivered", phone=logs.mask_phone(phone), inbox=inbox_id, via="template")
            except Exception as e:
                logs.event(log, "delivery_failed", logging.ERROR, phone=logs.mask_phone(phone), via="template",
                           error=type(e).__name__)
        else:
            logs.event(log, "delivery_held", logging.WARNING, phone=logs.mask_phone(phone), inbox=inbox_id,
                       reason="window_closed_no_template")
    except Exception as e:
        logs.event(log, "delivery_failed", logging.ERROR, phone=logs.mask_phone(phone), via="text", error=type(e).__name__)


def flush(phone: str) -> int:
    """Vendor just wrote (24h window open): send the latest undelivered scheduled items, drop older ones."""
    v, n = "wa:" + phone, 0
    rows = run("SELECT id,title,body,pdf FROM inbox WHERE vendor_id=? AND sent=0 AND body NOT LIKE '⏳%' "
               "ORDER BY id DESC LIMIT 3", (v,))
    for r in reversed(rows):
        try:
            _push(phone, r["title"], r["body"], r["pdf"])
        except Exception:
            return n
        run("UPDATE inbox SET sent=1 WHERE id=?", (r["id"],))
        n += 1
    run("UPDATE inbox SET sent=1 WHERE vendor_id=? AND sent=0 AND body NOT LIKE '⏳%'", (v,))  # stale backlog
    return n


LOGOUT = {w for c in ("stop", "unlink", "logout", "log out") for w in (c, "/" + c)}  # the exact message, any case, nothing else


def is_logout(t: str | None) -> bool:
    return (t or "").strip().lower() in LOGOUT


def _logout(phone: str) -> None:
    """Revoke the link at OkGhumo FIRST (if that fails nothing local changes and the vendor is told), then wipe
    everything we hold for this number: schedules, pending proposals, undelivered inbox rows, chat history."""
    already = False
    try:
        revoke(phone)
    except NotLinked:
        already = True  # nothing to revoke, still clean up below
    except LinkInvalid:
        pass  # account no longer valid at OkGhumo: the link is dead anyway
    except httpx.HTTPError:
        return send(phone, "I couldn't log you out just now. Please try again in a minute.")
    logs.event(log, "logout", phone=logs.mask_phone(phone), already=already or None)
    wipe(phone)  # logged out = no more messages to this number, no report text kept
    run("DELETE FROM wa_account WHERE phone=?", (phone,))
    send(phone, ("You're already logged out. " if already else "Logged out ✅ ") + "Send *hi* any time to connect again.")


# ---- coalescing: a thought sent as several quick messages is ONE question ----
_pending: dict[str, list[tuple[str | None, str]]] = {}  # phone -> [(text, wamid)] not yet answered
_plock = threading.Lock()
_locks: dict[str, threading.Lock] = {}  # one worker at a time per phone: replies and history stay in order


def _is_command(t: str | None) -> bool:
    t = (t or "").strip()
    return is_logout(t) or (t.upper().startswith("LINK-") and " " not in t)


_voice: dict[str, str] = {}  # wamid -> Meta media id of a voice note waiting to be transcribed


def _transcribe(phone: str, media_id: str) -> str | None:
    """Voice note -> text (echoed back so a mis-heard name or number is visible). None = vendor was told why not."""
    try:
        day = int(os.environ.get("RATE_WA_VOICE_PER_DAY", 15))
        if not allow(f"voice:{phone}", day, 86400):  # per vendor (= phone), rolling 24 h; resets on restart (ratelimit.py)
            logs.event(log, "rate_limited", logging.WARNING, scope="wa_voice", phone=logs.mask_phone(phone))
            send(phone, f"You've reached today's limit of {day} voice notes. Please type your question; "
                        "voice notes will work again within 24 hours.")
            return None
        text = voice.get_text(media_id)
    except voice.VoiceError as e:
        logs.event(log, "voice", logging.WARNING, phone=logs.mask_phone(phone), outcome=e.kind)
        msg = {"too_long": "That voice note is too long for me. Please keep it under 2 minutes, or type your question.",
               "empty": "I couldn't hear anything in that voice note. Please try again or type your question."}.get(
                   e.kind, "Sorry, I couldn't process that voice note. Please try again or type your question.")
        send(phone, msg)
        return None
    except Exception as e:
        logs.event(log, "voice", logging.ERROR, phone=logs.mask_phone(phone), outcome="error", error=type(e).__name__)
        send(phone, "Sorry, I couldn't process that voice note. Please try again or type your question.")
        return None
    logs.event(log, "voice", phone=logs.mask_phone(phone), outcome="ok", chars=len(text))  # never the text itself
    send(phone, "🎙 I heard: " + text)
    return text


def enqueue(phone: str, text: str | None, wamid: str) -> None:
    with _plock:
        _pending.setdefault(phone, []).append((text, wamid))


def process(phone: str) -> None:
    """Wait a moment for the vendor to finish typing, then answer everything they sent as one message.
    ponytail: in-process wait (WA_DEBOUNCE_S, default 5s); a worker restart drops it like the rest of the queue."""
    with _plock:
        lock = _locks.setdefault(phone, threading.Lock())
    with lock:  # a second task for the same phone waits here, then finds nothing left to do
        with _plock:
            has_voice = any(w in _voice for _, w in _pending.get(phone, []))
        time.sleep(0 if has_voice else float(os.environ.get("WA_DEBOUNCE_S", 5)))  # a voice note is a whole thought: no wait
        with _plock:
            batch = _pending.pop(phone, [])
        if not batch:
            return
        if len(batch) > 1:
            logs.event(log, "wa_coalesced", phone=logs.mask_phone(phone), count=len(batch))
        items = []  # (text, wamid, came_from_voice)
        for t, w in batch:
            if w in _voice:
                t = _transcribe(phone, _voice.pop(w))
                if t is None:
                    continue  # the vendor was already told what went wrong
                items.append((t, w, True))
            else:
                items.append((t, w, False))
        if not items:
            return
        if any(_is_command(t) for t, _, v in items if not v):  # STOP / LINK-code must stay whole TYPED messages
            for t, w, v in items:
                handle(phone, t, w, v)
            return
        texts = [t for t, _, _ in items if t is not None]
        handle(phone, "\n".join(texts) if texts else None, items[-1][1], any(v for _, _, v in items))


def handle(phone: str, text: str | None, wamid: str, voice: bool = False) -> None:
    try:
        t = (text or "").strip()
        if text is None:
            send(phone, "I can only read text messages for now.")
        elif is_logout(t) and not voice:  # commands are typed only: speech-to-text can mishear
            _logout(phone)
        elif t.upper().startswith("LINK-") and " " not in t and not voice:
            _redeem(phone, t)  # phone comes from the signed webhook, never from the text
        else:
            _answer(phone, t, wamid, voice)
    except Exception as e:  # never leak details to the user; type only in the log
        logs.event(log, "handler_failed", logging.ERROR, phone=logs.mask_phone(phone), error=type(e).__name__)
        try:
            send(phone, "Sorry, something went wrong. Please try again in a minute.")
        except Exception:
            pass


# ---- Meta webhook ----
@router.get("/whatsapp/webhook")
def verify(request: Request):
    q, want = request.query_params, os.environ.get("WHATSAPP_VERIFY_TOKEN", "")
    if want and q.get("hub.mode") == "subscribe" and hmac.compare_digest(q.get("hub.verify_token", "").encode(), want.encode()):
        return PlainTextResponse(q.get("hub.challenge", ""))
    raise HTTPException(403)


@router.post("/whatsapp/webhook")
async def receive(request: Request, bg: BackgroundTasks):
    secret = os.environ.get("WHATSAPP_APP_SECRET")
    if not secret:
        raise HTTPException(503)
    raw = await request.body()  # signature is over the RAW bytes
    want = "sha256=" + hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(request.headers.get("X-Hub-Signature-256", "").encode(), want.encode()):
        logs.event(log, "webhook_rejected", logging.WARNING, reason="bad_signature")
        raise HTTPException(403)
    try:
        body = json.loads(raw)
        for entry in body.get("entry", []):
            for ch in entry.get("changes", []):
                v = ch.get("value", {})
                if v.get("metadata", {}).get("phone_number_id") != os.environ.get("WHATSAPP_PHONE_NUMBER_ID"):
                    continue  # not our number
                for m in v.get("messages", []):
                    if _first_seen(m["id"]):
                        logs.event(log, "wa_message", phone=logs.mask_phone(m["from"]), type=m.get("type"))
                        if m.get("type") == "audio" and voice.enabled():
                            _voice[m["id"]] = m["audio"]["id"]
                        enqueue(m["from"], m["text"]["body"] if m.get("type") == "text" else None, m["id"])
                        bg.add_task(process, m["from"])
    except (ValueError, KeyError, AttributeError, TypeError):
        logs.event(log, "webhook_rejected", logging.WARNING, reason="bad_payload")
        raise HTTPException(400)
    return {"ok": True}  # always 200 fast so Meta doesn't retry
