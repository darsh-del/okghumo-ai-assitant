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
import time
from collections import defaultdict, deque
from contextlib import closing

import httpx
from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from fastapi.responses import PlainTextResponse

from app import assistant, schedules
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


# ---- sending ----
def _graph(payload: dict) -> None:
    url = f"https://graph.facebook.com/{os.environ.get('WHATSAPP_API_VERSION', 'v23.0')}/{os.environ['WHATSAPP_PHONE_NUMBER_ID']}/messages"
    httpx.post(url, json={"messaging_product": "whatsapp", **payload}, timeout=15,
               headers={"Authorization": "Bearer " + os.environ["WHATSAPP_TOKEN"]}).raise_for_status()


def send(to: str, text: str) -> None:
    for i in range(0, max(len(text), 1), 4000):  # WhatsApp text limit is 4096
        _graph({"to": to, "type": "text", "text": {"body": text[i:i + 4000]}})


# ---- small in-memory state (see ponytail note) ----
_hits: dict[str, deque] = defaultdict(deque)
_hist: dict[str, tuple[float, list]] = {}  # phone -> (last activity, messages); keyed by phone = per vendor


def allow(key: str, n: int, per: int) -> bool:
    q, now = _hits[key], time.time()
    while q and q[0] < now - per:
        q.popleft()
    if len(q) >= n:
        return False
    q.append(now)
    return True


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
        return send(phone, "Too many link requests. Please try again in an hour.")
    send(phone, "Hi! To use the Okghumo assistant, link your account first. Open this link (valid 10 minutes), "
                "log in, then tap the button to come back here and press Send:\n" + start(phone))


def _redeem(phone: str, code: str) -> None:
    if not allow(f"redeem:{phone}", 5, 3600):
        return send(phone, "Too many attempts. Please try again in an hour.")
    try:
        t = redeem(phone, code)
    except BadCode:
        return send(phone, "That code is invalid or expired. Send *hi* to get a new link.")
    except LinkInvalid:
        return send(phone, "This account can't use the assistant.")
    if t.get("role") not in ROLES:
        revoke(phone)
        return send(phone, "This account type can't use the WhatsApp assistant.")
    send(phone, "Connected ✅ Ask me about your sales, bookings and collections. Send *STOP* to unlink.")


def _answer(phone: str, text: str, wamid: str) -> None:
    if not allow(f"msg:{phone}", 20, 3600):
        return send(phone, "You're sending a lot of messages. Please try again in a little while.")
    try:
        t = token(phone)  # OkGhumo decides who this phone is; fresh short-lived token every question
    except NotLinked:
        return _start_link(phone)
    except LinkInvalid:
        return send(phone, "This account can no longer use the assistant. Please contact OkGhumo support.")
    if t.get("role") not in ROLES:
        return send(phone, "This account type can't use the WhatsApp assistant.")
    if not t.get("mcpAccessToken"):  # fail closed: no token, no model call
        log.error("okghumo returned no mcp token")
        return send(phone, "The assistant isn't available right now. Please try again later.")
    try:
        _graph({"status": "read", "message_id": wamid, "typing_indicator": {"type": "text"}})
    except Exception:
        pass  # cosmetic
    vendor, msgs = "wa:" + phone, _messages(phone) + [{"role": "user", "content": text}]  # own namespace per phone
    done = schedules.intercept(vendor, t["role"], msgs, where="here on WhatsApp")  # YES/NO to OUR prompt: code, no model
    if done is not None:
        _remember(phone, msgs + [{"role": "assistant", "content": done}])
        return send(phone, done)
    if flush(phone) and len(text) <= 10:  # a ping that only opened the 24h window: reports are enough
        return
    sched = schedules.for_vendor(vendor, t["role"])
    out = assistant.ask(assistant.get_client(), t["role"], t["mcpAccessToken"], msgs,
                        SYSTEM + (schedules.SCHED_SYSTEM + schedules.WA_SCHED if sched else ""), extra=sched)
    reply = sched.finish(out["reply"]) if sched else out["reply"]
    reply = reply or "Sorry, I couldn't find an answer to that."
    _remember(phone, msgs + [{"role": "assistant", "content": reply}])
    send(phone, reply)


# ---- scheduled delivery ----
def deliver(inbox_id: int, vendor: str, title: str, body: str) -> None:
    """Send one inbox row to its WhatsApp owner. Free text only works inside Meta's 24h window; outside it we send
    an approved template (WHATSAPP_REPORT_TEMPLATE, one text variable = the title) and keep the row unsent, so
    flush() delivers it when the vendor next writes. Never raises."""
    if not vendor.startswith("wa:"):
        return
    phone = vendor[3:]
    try:
        send(phone, f"*{title}*\n{body}")
        run("UPDATE inbox SET sent=1 WHERE id=? AND vendor_id=?", (inbox_id, vendor))
    except httpx.HTTPStatusError:  # window closed (or Meta refused)
        tpl, pn = os.environ.get("WHATSAPP_REPORT_TEMPLATE"), os.environ.get("WHATSAPP_TEMPLATE_PARAM")  # pn: named variable
        if tpl:
            try:
                _graph({"to": phone, "type": "template", "template": {
                    "name": tpl, "language": {"code": os.environ.get("WHATSAPP_TEMPLATE_LANG", "en")},
                    "components": [{"type": "body", "parameters": [{"type": "text", "text": " ".join(title.split())[:60],
                                                                **({"parameter_name": pn} if pn else {})}]}]}})
            except Exception as e:
                log.error("template send failed: %s", type(e).__name__)
    except Exception as e:
        log.error("scheduled delivery failed: %s", type(e).__name__)


def flush(phone: str) -> int:
    """Vendor just wrote (24h window open): send the latest undelivered scheduled items, drop older ones."""
    v, n = "wa:" + phone, 0
    rows = run("SELECT id,title,body FROM inbox WHERE vendor_id=? AND sent=0 AND body NOT LIKE '⏳%' "
               "ORDER BY id DESC LIMIT 3", (v,))
    for r in reversed(rows):
        try:
            send(phone, f"*{r['title']}*\n{r['body']}")
        except Exception:
            return n
        run("UPDATE inbox SET sent=1 WHERE id=?", (r["id"],))
        n += 1
    run("UPDATE inbox SET sent=1 WHERE vendor_id=? AND sent=0 AND body NOT LIKE '⏳%'", (v,))  # stale backlog
    return n


LOGOUT = {"stop", "unlink", "logout", "log out"}  # whole message only, optional leading "/"


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
    _hist.pop(phone, None)
    for tbl in ("tasks", "proposals", "inbox"):  # logged out = no more messages to this number, no report text kept
        run(f"DELETE FROM {tbl} WHERE vendor_id=?", ("wa:" + phone,))
    send(phone, ("You're already logged out. " if already else "Logged out ✅ ") + "Send *hi* any time to connect again.")


def handle(phone: str, text: str | None, wamid: str) -> None:
    try:
        t = (text or "").strip()
        if text is None:
            send(phone, "I can only read text messages for now.")
        elif " ".join(t.lower().lstrip("/").split()) in LOGOUT:
            _logout(phone)
        elif t.upper().startswith("LINK-") and " " not in t:
            _redeem(phone, t)  # phone comes from the signed webhook, never from the text
        else:
            _answer(phone, t, wamid)
    except Exception as e:  # never leak details to the user; type only in the log
        log.error("whatsapp handler failed: %s", type(e).__name__)
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
                        bg.add_task(handle, m["from"], m["text"]["body"] if m.get("type") == "text" else None, m["id"])
    except (ValueError, KeyError, AttributeError, TypeError):
        raise HTTPException(400)
    return {"ok": True}  # always 200 fast so Meta doesn't retry
