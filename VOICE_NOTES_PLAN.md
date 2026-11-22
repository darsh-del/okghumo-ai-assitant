# WhatsApp voice notes — Plan (draft v1, 2026-11-22)

Goal: a vendor can send a **voice note** to the WhatsApp bot instead of typing. The bot answers in text,
exactly like today. Today `whatsapp.py` replies "I can only read text messages for now." to anything that
is not text.

## 1. The idea in one paragraph
A voice note is just a question the vendor said instead of typed. So we turn it into text (speech-to-text)
and feed that text into the **same pipeline as a typed message** (rate limit, token mint, guard, model,
sanitize). Nothing downstream changes, so the read-only rules, scheduling and language handling all keep working.

## 2. Workflow (what happens, step by step)
```
Vendor records a voice note
  -> Meta webhook: message type "audio" (voice note), with a media id
  -> receive(): verify signature, dedupe, enqueue a VOICE item (media id, no text yet)   [fast 200 to Meta]
  -> process(): wait the usual 5s (coalescing), then for each voice item:
       1. rate-limit check (voice has its own, tighter limit)
       2. ask Meta for the media URL by id, download the audio (needs WHATSAPP_TOKEN; URL expires in minutes)
       3. reject if too long (> 150s) or too big -> friendly "please keep it under 2.5 minutes"
       4. send audio to the speech-to-text service -> transcript text
       5. delete the audio immediately (never stored on disk)
  -> reply "🎙 I heard: <transcript>"  (so the vendor can catch a mis-heard name or number)
  -> hand the transcript to handle()/_answer() as ordinary text
  -> normal text reply
```
Typed text and voice notes sent close together still merge into one question (existing coalescing).

## 3. Code changes (small, all in the WhatsApp layer)
| File | Change |
|---|---|
| `app/whatsapp.py` | In `receive`, for `type == "audio"` enqueue a voice marker (media id) instead of `None`. In `process`, transcribe voice items before building the batch. Replace the "only text" reply with the voice path. |
| `app/voice.py` (new, ~50 lines) | `fetch(media_id) -> bytes` (Graph API, two GETs) and `transcribe(bytes) -> str` (one HTTPS call to the chosen STT service). Nothing else. |
| `app/guard.py` | 150s of speech is roughly 2000-2500 characters, above `MAX_MSG_CHARS` (1000), which would wrongly reject long notes as "too long". Allow a higher limit (~2500) for voice transcripts only; typed text stays at 1000. |
| `app/lang.py` | Canned messages: voice too long, couldn't understand, voice temporarily unavailable (English/Hindi/Gujarati, Roman letters). |
| `.env.example` | `STT_API_KEY`, `VOICE_MAX_SECONDS=150`, `RATE_WA_VOICE_PER_HOUR`, `VOICE_ENABLED=1` (kill switch, like `SCHEDULING_VIA_CHAT`). |
| `tests/test_voice.py` (new) | Fakes for Meta media + STT. See section 6. |

Unchanged: `assistant.py`, `rbac.py`, `guard.py`, `schedules.py`, `mcp_auth.py`. No new model tool, no new MCP call.

## 4. Safety rules (must hold)
1. **Voice never confirms or commands.** A transcript is never treated as `STOP`, `LINK-…`, or the plain
   "yes" that `schedules.intercept()` needs. Speech-to-text can mishear, and those are the actions that change
   state. Reply: "For safety, please type YES (or STOP) as text." The vendor can still *ask* for a schedule by voice.
2. **Show what we heard** before answering, so a wrong number or agent name is visible.
3. **Identity is unchanged:** still the sender phone from Meta's signed webhook, never anything said in the audio.
4. **Transcript is untrusted input**, same as typed text: it goes through `guard.classify` (off-topic, injection,
   `MAX_MSG_CHARS`) like any message. Someone can *say* "ignore previous instructions" too.
5. **Privacy:** audio is deleted right after transcription; never log audio, transcript text, or media ids
   (log only `type=audio`, duration, and outcome). Voice is personal data: it leaves our server to the STT
   provider, so the owner must approve the provider (section 7).
6. **Read-only rule is untouched.** The STT call is to a speech service, not to Okghumo or the MCP server.
   Still, it is a *new outbound dependency*: needs the owner's written OK per CLAUDE.md spirit.
7. **Cost/abuse caps:** max duration, max file size, per-phone voice rate limit, kill switch. Transcription is
   billed per minute, so these also cap spend. No model call on a failed or empty transcription (fail closed).

## 5. Speech-to-text options (the one real decision)
Anthropic's API does not transcribe audio, so we need a separate STT service.
| Option | Good | Watch out |
|---|---|---|
| OpenAI Whisper API | Cheapest and simplest, one call, takes WhatsApp's ogg/opus directly (roughly $0.006/min, verify) | Gujarati accuracy is weaker than Hindi; audio goes to a US provider |
| Google Cloud Speech-to-Text | Explicit Hindi (`hi-IN`) and Gujarati (`gu-IN`) models | Needs conversion/config; more setup |
| An India-focused STT (e.g. Sarvam) | Built for Indian languages and code-mixing (Hinglish) | Newer vendor: check pricing, terms, data retention |
| Self-hosted `faster-whisper` | Audio never leaves our server | Needs a bigger server (CPU/GPU), more ops |

**Recommendation:** build behind a one-function interface, start with Whisper API, then run 20–30 real
vendor voice notes (Hindi, Gujarati, Hinglish, noisy shops) through it. If Gujarati is poor, swap the provider;
only `voice.transcribe()` changes.

## 6. Tests (one file, no real network)
- Voice note becomes the same reply as the typed equivalent; the "I heard" line is sent first.
- Too long / too large audio: friendly reply, no STT call, no model call.
- STT failure or empty transcript: friendly reply, **no model call**.
- Voice "yes" / "stop" / "LINK-…" are NOT executed; vendor is told to type it.
- Voice rate limit hit: 429-style friendly reply, no download.
- Audio and transcript never appear in logs; audio bytes not written to disk.
- Duplicate webhook (Meta retry) transcribes once (existing `wa_seen`).
- `VOICE_ENABLED=0` restores today's "text only" reply.

## 7. Build order
1. **Owner decisions:** pick STT provider and approve sending voice audio to it; max length is set to 150s; confirm hourly limits.
2. `voice.py` + env + kill switch; wire `receive`/`process`; canned replies; tests. (about 1 day)
3. Staging test on a real WhatsApp number with real Hindi/Gujarati notes; tune the provider and a "language hint".
4. Roll out: enable for a few vendors first, watch `wa_message type=audio` volume, cost and failure rate in logs.
5. Update `TESTER_GUIDE.md` and the CLAUDE.md layout line.

## 8. Not in v1 (add when asked)
- **Voice replies** (bot talks back): more cost, needs text-to-speech; text replies are easier to skim.
- **Other media** (images, documents, video): still get "I can only read text and voice notes".
- **Voice confirmations** for schedules: only if mis-hearing risk is accepted by the owner.
- **Queue + Redis**: already flagged in `whatsapp.py`; voice adds a few seconds of in-process work per note,
  which is fine on one process.

## 9. Open questions (owner)
1. Which STT provider, and is sending vendor voice audio to it acceptable?
2. Max voice length is decided: 150s. Hourly voice limit per vendor (proposal: 10)?
3. Should the "I heard: …" echo always show, or only when the transcript looks uncertain?
4. Meta side: nothing to enable for receiving audio on the existing number, but confirm in the Meta dashboard
   that the webhook already subscribes to `messages`.
