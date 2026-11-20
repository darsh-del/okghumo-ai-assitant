# WhatsApp assistant: production workflow (draft v1, 2026-11-20)

Goal: a vendor links their WhatsApp number to their OkGhumo account once, then chats with the bot on WhatsApp.
**Status 2026-11-21: v1 is built** (`app/whatsapp.py`), using OkGhumo's real link API (`/api/users/whatsapp-link`, `/redeem`, `/token`, `/revoke`; OkGhumo owns the phone-to-vendor link, we keep no binding or tokens). Sections 2 and 7 below describe the original design; where they differ, the built flow is: bot sends `connectUrl` -> vendor logs in on OkGhumo's page -> page shows `LINK-<code>` + Open WhatsApp button -> vendor taps Send -> bot redeems with the webhook's sender phone. Still open: HTTPS domain, queue/Redis, scheduled WhatsApp reports. Builds on `INTEGRATION_PLAN.md` (isolation layers L1-L8 still apply).

## 1. The idea in one paragraph
WhatsApp tells us, and Meta signs it, **which phone number sent a message**. OkGhumo's login tells us **which
vendor logged in**. Linking = proving that the *same person* controls both, once. After that, every WhatsApp
message is mapped `phone -> vendor_id` in our own database, and the vendor id never comes from the message text.

## 2. Linking flow (do this once per vendor)
Two entry points, same finish. **Prefer B**; A is needed because "vendor texts the bot first" will happen.

**B. Start from the OkGhumo app / dashboard (safest)**
1. Vendor is logged in to OkGhumo and taps **Connect WhatsApp**.
2. OkGhumo calls us (server to server) -> we create a single-use nonce bound to that vendor id, valid 10 min.
3. App shows a button: `https://wa.me/<bot-number>?text=LINK-<nonce>` (opens WhatsApp with the text pre-filled).
4. Vendor presses Send. Our webhook receives the message plus the sender's phone number.
5. We find the nonce, bind `phone <-> vendor`, burn the nonce, reply "Connected to <business name>".

**A. Start from WhatsApp (vendor says "Hi" first)**
1. Unknown number: bot replies with a login link `https://app.okghumo.com/whatsapp/link?s=<nonce>`.
   The nonce is bound to *that phone number*, 10 min, single use.
2. Vendor opens the link and logs in with OkGhumo's normal login (OTP etc.). We do not handle passwords.
3. After login the page shows one button: **Continue to WhatsApp**. It opens
   `https://wa.me/<bot-number>?text=LINK-<new-token>` with the message already typed (no manual typing).
   The token is minted for the vendor who just logged in, single use, 10 min.
4. Vendor taps **Send** (WhatsApp never auto-sends; one tap is the minimum). We verify the token, take the
   sender phone from Meta's signed webhook, bind `phone <-> vendor`, and reply "Connected to <business>".

Why the token must travel *inside a message sent from the vendor's phone*: that single message proves both
facts at once (web login = vendor X, WhatsApp sender = phone Y). **Never bind on "link opened" or "login
succeeded" alone**, because the link can be forwarded to someone else's phone.
Reply also says "Not you? Reply NO" and unlinks at once.

**The token is for linking only, not for every message.** After linking, the phone number (signed by Meta)
is the identity; the vendor never sends a token again.

Rules for both: nonces are random 128-bit, stored hashed, single use, short life; max 5 attempts per number per
hour; the confirm message names the business so a wrong link is noticed; vendor gets a "WhatsApp linked"
notification in the app (and can unlink from there).

## 3. Everyday chat flow
```
Vendor WhatsApp -> Meta -> POST /whatsapp/webhook (our API)
  1 verify X-Hub-Signature-256 on the RAW body with the app secret (reject otherwise)
  2 store the message id (dedupe: Meta retries), return 200 immediately
  3 push to a queue; a worker does the rest (Claude calls take 1-2 min, webhooks must answer fast)
  4 worker: phone -> vendor_id lookup
        unknown -> send the linking message (section 2A)
        known   -> rate limit, then run the SAME pipeline as POST /chat with that vendor's identity
  5 send the reply through the WhatsApp Cloud API (split long answers, no markdown tables)
```
UX: send "typing"/read receipt right away; keep answers short with simple lists; support English, Hindi,
Hinglish, Gujarati, Marathi; for voice notes later, transcribe then follow the same path.

## 4. Production decisions (opinionated)
| Topic | Decision |
|---|---|
| Channel | WhatsApp Business **Cloud API** (Meta hosted). Business verification and a display name are needed; start early, it takes days |
| Identity | `wa_links(phone_e164 UNIQUE, vendor_id, linked_at, last_seen, revoked_at)`. One phone to one vendor. A staff member who needs two vendors uses a "switch business" command later, not now |
| Session expiry | Re-verify every 30 days or on SIM-change signal, since phone numbers get recycled. Unlinking in the app takes effect immediately |
| Data access | Token comes from OkGhumo after linking (an OkGhumo-issued, read-only, revocable credential for this vendor). Same per-vendor cache and `clientId` lock as today. No shared token |
| 24h window | Free-form replies only within 24h of the vendor's last message. Scheduled reports and reminders need **pre-approved templates** (link, then "Reply to see details" which reopens the window) |
| Opt-in | Linking is the explicit opt-in. "STOP" unlinks notifications. Keep proof of consent |
| Cost | Meta billing changed to per-message for templates in 2025; India rates sources disagree. Check Meta's current India rate card before budgeting. Replies inside the window are cheaper/free; marketing templates are not used |
| Safety | Read-only allowlist, L7 id-allowlist, tool output is untrusted, no "send message" tools. Booking text can be an injection, and WhatsApp makes it easy to forward replies, so answers carry no phone or email |
| Abuse | Rate limit per phone and per vendor, daily cost cap, ignore group messages and non-text types we do not support |
| Secrets | Meta system-user token and app secret in env/secret store, rotated; never logged |
| Logs | phone hash, vendor id, tool names, status. No message bodies by default, no tokens, no tool results |
| Ops | Queue with retry and dead-letter, Meta delivery-status webhooks, alert on 5xx or signature failures, health dashboards. Needs a domain and HTTPS (Meta requires an HTTPS webhook) |
| Scale | Move sqlite to Postgres and the scheduler/queue to a managed queue before more than one server |

## 5. Failure cases to design for
- Vendor changes SIM or number: unlink in app, relink; old number dead the moment it is unlinked.
- Number shared by two vendors: refuse the second link ("already linked"); support flow to release.
- Nonce expired or replayed: friendly "link expired, ask for a new one".
- Vendor deactivated in OkGhumo: credential stops working, bot replies "account inactive", link marked revoked.
- Meta retries, out-of-order delivery, duplicate messages: dedupe by message id.
- Model slow or down: send "Working on it, one moment", then the answer, or a clear apology.

## 6. Build order
1. Domain + HTTPS, Meta business verification, WhatsApp number and templates submitted (long lead time).
2. Webhook receiver (GET verify + signed POST), dedupe, queue, `wa_links` table.
3. Linking flow B, then A with code-back; the OkGhumo-side endpoints (section 7).
4. Chat worker reusing `/chat`'s pipeline; rate limits and caps.
5. Cross-vendor tests: A's phone cannot read B's data, replayed or expired nonces rejected, forged webhook
   rejected, unknown number cannot chat, two vendors one phone refused.
6. Pilot with a few vendors, then scheduled report templates (`SCHEDULING_ROADMAP.md`).

## 7. What OkGhumo must provide
1. An authenticated "Connect WhatsApp" action that calls us server to server with the vendor id (shared secret or
   signed request), and a hosted `/whatsapp/link` page behind their normal login.
2. A read-only delegated credential per vendor on link (see `INTEGRATION_PLAN.md` C-2) and a revoke hook.
3. A notification to the vendor when linked or unlinked.
4. A domain for our webhook and the bot's WhatsApp number (and who owns the Meta Business account).

## Sources
- [Meta: authentication best practices](https://developers.facebook.com/documentation/business-messaging/whatsapp/templates/authentication-templates/authentication-best-practices/)
- [Meta: pricing](https://developers.facebook.com/documentation/business-messaging/whatsapp/pricing)
- [Hookdeck: WhatsApp webhooks guide](https://hookdeck.com/webhooks/platforms/guide-to-whatsapp-webhooks-features-and-best-practices)
