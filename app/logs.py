"""Logging for the whole app: structured `event=name key=value` lines, safe against log injection and PII.

  * Every log line is neutralised: CR/LF and other control characters in the message are escaped (a custom
    LogRecord does it, so even a stray `log.info(user_text)` cannot forge a second line). CWE-117 / OWASP Logging.
  * Identifiers are masked before they reach a log: mask_id() for vendor/session ids, mask_phone() for numbers.
  * NEVER log: message text, report text, tokens, credentials, link codes, tool results. Log counts, names, types.
Env: LOG_LEVEL (default INFO).
"""
import json
import logging
import os
import re

_CTRL = re.compile(r"[\x00-\x1f\x7f  ]")
_ESC = {"\r": "\\r", "\n": "\\n", "\t": "\\t"}


def esc(s: str) -> str:
    """Make any string single-line and printable: CR/LF/TAB and other control characters become \\r \\n \\t \\xNN."""
    return _CTRL.sub(lambda m: _ESC.get(m.group(), f"\\x{ord(m.group()):02x}"), s)


class SafeRecord(logging.LogRecord):
    def getMessage(self) -> str:  # applies to every record, whoever created it
        return esc(super().getMessage())


def mask_id(v: str | None) -> str:
    """Vendor / session / task owner ids: keep just enough to correlate (first 2 + last 2), never the whole value."""
    v = str(v or "")
    if v.startswith("wa:"):
        return "wa:" + mask_phone(v[3:])
    return v if len(v) <= 4 else f"{v[:2]}***{v[-2:]}"


mask_session_id = mask_id  # same rule; the app's only "session" identity is the vendor id from the JWT


def mask_phone(p: str | None) -> str:
    """Last 4 digits only."""
    d = re.sub(r"\D", "", str(p or ""))
    return "***" + d[-4:] if d else "***"


def kv(**fields) -> str:
    """`a=1 b="two words"`: values escaped, strings with spaces/quotes JSON-quoted. None fields are dropped."""
    out = []
    for k, v in fields.items():
        if v is None:
            continue
        s = esc(str(v))
        out.append(f"{k}={json.dumps(s) if re.search(r'[\\s=\"]', s) else s}")
    return " ".join(out)


def event(log: logging.Logger, name: str, level: int = logging.INFO, **fields) -> None:
    log.log(level, "event=%s %s", name, kv(**fields))


def setup() -> None:
    """Idempotent. Call once at process start (API and scheduler)."""
    logging.setLogRecordFactory(SafeRecord)
    root = logging.getLogger()
    if not getattr(root, "_okghumo", False):
        logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO").upper(),
                            format="%(asctime)s %(levelname)s %(name)s %(message)s")
        root._okghumo = True
    for noisy in ("httpx", "httpcore"):  # their INFO lines contain full URLs (link codes, ids)
        logging.getLogger(noisy).setLevel(logging.WARNING)
