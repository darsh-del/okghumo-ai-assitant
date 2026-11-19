"""Per-vendor OkGhumo credential store (Fernet-encrypted at rest; key in CREDENTIAL_KEY, outside the DB).

Replaces the old shared OKGHUMO_LOGIN_TOKEN: every vendor's MCP access derives from THEIR OWN credential.
Never log or return the token. Target: swap the stored value for an OkGhumo-issued read-only delegated
credential (SCHEDULING_ROADMAP.md, Phase 0 follow-up) instead of a full login token.
"""
import os
from datetime import datetime, timezone

from cryptography.fernet import Fernet

from app.db import run


class NoCredential(KeyError):
    pass


def _f() -> Fernet:
    return Fernet(os.environ["CREDENTIAL_KEY"])


def put(vendor_id: str, token: str) -> None:
    run(
        "INSERT INTO credentials(vendor_id, token_enc, updated_at) VALUES(?,?,?) "
        "ON CONFLICT(vendor_id) DO UPDATE SET token_enc=excluded.token_enc, updated_at=excluded.updated_at",
        (vendor_id, _f().encrypt(token.encode()), datetime.now(timezone.utc).isoformat(timespec="seconds")),
    )


def get(vendor_id: str) -> str:
    rows = run("SELECT token_enc FROM credentials WHERE vendor_id=?", (vendor_id,))
    if not rows:
        raise NoCredential(vendor_id)
    return _f().decrypt(rows[0]["token_enc"]).decode()


def delete(vendor_id: str) -> None:
    run("DELETE FROM credentials WHERE vendor_id=?", (vendor_id,))
