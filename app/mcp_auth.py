"""Exchange a vendor's OkGhumo login token for the 15-minute MCP access token (cached PER VENDOR).

The ONLY non-read-tool network call this app makes: a token mint at the auth service, never a data write.
The cache is keyed by vendor_id so one vendor can never be served another's token.
"""
import os
import time

import httpx

from app import credentials

_cache: dict[str, tuple[str, float]] = {}  # vendor_id -> (mcp token, expiry epoch)


def _exchange(login_token: str) -> tuple[str, float]:
    r = httpx.post(
        os.environ.get("OKGHUMO_AUTH_URL", "https://prod.okghumo.com") + "/api/auth/mcp/exchange",
        headers={"Authorization": "Bearer " + login_token},
        timeout=15,
    )
    r.raise_for_status()
    j = r.json()
    return j["accessToken"], time.time() + j.get("expiresIn", 900)


def mcp_token(vendor_id: str) -> str:
    """Raises credentials.NoCredential (no stored credential) or httpx.HTTPError (exchange rejected)."""
    hit = _cache.get(vendor_id)
    if hit and time.time() < hit[1] - 60:
        return hit[0]
    _cache[vendor_id] = _exchange(credentials.get(vendor_id))
    return _cache[vendor_id][0]


def register(vendor_id: str, login_token: str) -> None:
    """Validate by exchanging first (bad token -> httpx.HTTPError, nothing stored), then store + prime cache."""
    _cache[vendor_id] = _exchange(login_token)
    credentials.put(vendor_id, login_token)


def forget(vendor_id: str) -> None:
    _cache.pop(vendor_id, None)
    credentials.delete(vendor_id)
