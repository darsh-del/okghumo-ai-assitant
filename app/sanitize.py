"""Client-facing clean-up of the model's reply. It changes ONLY presentation, never data:
digits, names, dates and currency are left exactly as the model wrote them.

  * raw 24-hex Mongo ObjectIds are removed (any case, even glued to Devanagari/Gujarati text: those letters are
    not hex, so the boundary check is on hex digits only). Known ceiling: an id glued to more hex letters/digits
    with no separator is one longer hex run and is NOT removed.
  * em/en dashes are replaced (a dash between two numbers becomes a hyphen, anything else a comma).
  * emoji: at most ONE per reply, and none when the previous assistant message already had one, when the reply
    is a question, or when it is any reply that contains a number (data replies). Result: only some replies get an emoji.
"""
import re

OBJECT_ID = re.compile(r"(?<![0-9A-Fa-f])[0-9A-Fa-f]{24}(?![0-9A-Fa-f])")
EMOJI = re.compile("[\U0001F300-\U0001FAFF☀-➿⭐⬆✅❌✨⌚-⏿]️?")
_EMPTY_BRACKETS = re.compile(r"\s*[\(\[]\s*[\)\]]")
_RANGE_DASH = re.compile(r"(?<=\d)\s*[–—]\s*(?=\d)")
_DASH = re.compile(r"\s*[–—]\s*")
_NUM = re.compile(r"\d[\d,]*")


def has_emoji(s: str) -> bool:
    return bool(EMOJI.search(s))


def _tidy(s: str) -> str:
    s = re.sub(r"[ \t]{2,}", " ", s)
    s = re.sub(r" +([,.;:!?])", r"\1", s)
    return re.sub(r"(?m)[ \t]+$", "", s).strip()


def strip_ids(s: str) -> str:
    return OBJECT_ID.sub("", s)


def clean_reply(text: str, history: list[dict] | None = None) -> str:
    """`history` = the conversation so far (client messages); used only to see if the last assistant turn had an emoji."""
    out = strip_ids(text)
    if out != text:
        out = _EMPTY_BRACKETS.sub("", out)
    out = _RANGE_DASH.sub("-", out)
    out = _DASH.sub(", ", out)
    prev = next((m["content"] for m in reversed(history or []) if m["role"] == "assistant"), "")
    allow_emoji = not has_emoji(prev) and not EMOJI.sub("", out).rstrip().endswith("?") and not re.search(r"\d", out)
    if allow_emoji:
        first = EMOJI.search(out)
        if first:  # keep the first, drop the rest
            out = out[:first.end()] + EMOJI.sub("", out[first.end():])
    else:
        out = EMOJI.sub("", out)
    return _tidy(out)
