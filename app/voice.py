"""WhatsApp voice notes -> text. Meta media download + a speech-to-text call (OpenAI by default), nothing else.

The transcript is then handled like a typed message by whatsapp.py. Audio lives only in memory (never written
to disk, never logged); it is sent to the STT provider and dropped. Not an Okghumo/MCP call.
"""
import os

import httpx


class VoiceError(Exception):
    """kind: too_long | empty | failed"""
    def __init__(self, kind: str):
        super().__init__(kind)
        self.kind = kind


def enabled() -> bool:
    return os.environ.get("VOICE_ENABLED", "1") != "0" and bool(os.environ.get("STT_API_KEY"))


def ogg_seconds(data: bytes) -> float | None:
    """Length of an Ogg/Opus voice note: the last page's granule position counts 48 kHz samples. None if unreadable."""
    i = data.rfind(b"OggS")
    if i < 0 or len(data) < i + 14:
        return None
    g = int.from_bytes(data[i + 6:i + 14], "little")
    return None if g == 2**64 - 1 else g / 48000


def fetch(media_id: str) -> bytes:
    max_bytes = int(os.environ.get("VOICE_MAX_BYTES", 5_000_000))
    auth = {"Authorization": "Bearer " + os.environ["WHATSAPP_TOKEN"]}
    try:
        meta = httpx.get(f"https://graph.facebook.com/{os.environ.get('WHATSAPP_API_VERSION', 'v23.0')}/{media_id}",
                         headers=auth, timeout=20)
        meta.raise_for_status()
        info = meta.json()
        if int(info.get("file_size") or 0) > max_bytes:
            raise VoiceError("too_long")
        r = httpx.get(info["url"], headers=auth, timeout=30)  # URL is short-lived and needs the same token
        r.raise_for_status()
    except (httpx.HTTPError, KeyError, ValueError) as e:
        raise VoiceError("failed") from e
    if len(r.content) > max_bytes:
        raise VoiceError("too_long")
    return r.content


def transcribe(audio: bytes) -> str:
    try:
        r = httpx.post("https://api.openai.com/v1/audio/transcriptions", timeout=60,
                       headers={"Authorization": "Bearer " + os.environ["STT_API_KEY"]},
                       data={"model": os.environ.get("STT_MODEL", "whisper-1"), "response_format": "verbose_json"},
                       files={"file": ("voice.ogg", audio, "audio/ogg")})
        r.raise_for_status()
        j = r.json()
        segs = j.get("segments") or []
        if segs and all(x.get("no_speech_prob", 0) > 0.6 for x in segs):  # Whisper invents words ("you") for silence
            return ""
        return (j.get("text") or "").strip()
    except (httpx.HTTPError, ValueError) as e:
        raise VoiceError("failed") from e


def get_text(media_id: str) -> str:
    """Voice note id -> transcript. Raises VoiceError (too_long | empty | failed); never returns an empty string."""
    audio = fetch(media_id)
    secs = ogg_seconds(audio)
    if secs is not None and secs > float(os.environ.get("VOICE_MAX_SECONDS", 150)):  # rejected before we pay for it
        raise VoiceError("too_long")
    text = transcribe(audio)
    if not text:
        raise VoiceError("empty")
    return text
