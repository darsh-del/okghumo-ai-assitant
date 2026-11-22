"""In-memory sliding-window rate limiter (one process). Cheap protection for the model bill and the upstream APIs.

hit(key, n, per) -> 0 if allowed (and counted), else seconds until a slot frees (use it for Retry-After).
enforce_chat(vendor) raises 429 with a Retry-After header. Limits are env-tunable.

ponytail: state is per process and resets on restart; with more than one API process/host move this to Redis.
"""
import logging
import os
import time
from collections import defaultdict, deque

from fastapi import HTTPException

from app import logs

log = logging.getLogger("ratelimit")
_hits: dict[str, deque] = defaultdict(deque)


def hit(key: str, n: int, per: int) -> int:
    q, now = _hits[key], time.time()
    while q and q[0] <= now - per:
        q.popleft()
    if len(q) >= n:
        return max(1, int(q[0] + per - now) + 1)
    q.append(now)
    return 0


def _env(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


def enforce(scope: str, vendor: str, rules: list[tuple[str, int, int]]) -> None:
    """rules = [(label, max, seconds)]. Raises 429 + Retry-After on the first window that is full."""
    for label, n, per in rules:
        wait = hit(f"{scope}:{label}:{vendor}", n, per)
        if wait:
            logs.event(log, "rate_limited", logging.WARNING, scope=scope, window=label, vendor=logs.mask_id(vendor),
                       retry_after=wait)
            raise HTTPException(429, "You're sending messages too fast. Please wait a moment and try again.",
                                headers={"Retry-After": str(wait)})


def enforce_chat(vendor: str) -> None:
    """Per vendor (burst + hourly) and one global ceiling on /chat so a flood can't drain the Anthropic budget."""
    enforce("chat", vendor, [("min", _env("RATE_CHAT_PER_MIN", 10), 60), ("hour", _env("RATE_CHAT_PER_HOUR", 100), 3600)])
    enforce("chat", "all", [("min", _env("RATE_CHAT_GLOBAL_PER_MIN", 300), 60)])


def enforce_credentials(vendor: str) -> None:
    """PUT /credentials triggers a call to Okghumo's auth service: keep it from being used to hammer it."""
    enforce("cred", vendor, [("hour", _env("RATE_CRED_PER_HOUR", 10), 3600)])
