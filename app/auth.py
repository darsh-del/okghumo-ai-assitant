import logging
import os

import jwt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app import logs

bearer = HTTPBearer()
log = logging.getLogger("auth")


def current_user(creds: HTTPAuthorizationCredentials = Depends(bearer)) -> dict:
    """Verified claims. `sub` (vendor id) is mandatory: identity is never taken from a request body."""
    try:
        claims = jwt.decode(creds.credentials, os.environ["JWT_SECRET"], algorithms=["HS256"])
    except jwt.PyJWTError as e:
        logs.event(log, "auth_failed", logging.WARNING, reason="invalid_token", error=type(e).__name__)
        raise HTTPException(401, "invalid token")
    if not claims.get("sub"):
        logs.event(log, "auth_failed", logging.WARNING, reason="no_subject")
        raise HTTPException(401, "token has no subject")
    claims["sub"] = str(claims["sub"])
    if claims["sub"].startswith("wa:"):  # "wa:<phone>" is the WhatsApp namespace: an app vendor must never be able to enter it
        logs.event(log, "auth_failed", logging.WARNING, reason="reserved_subject")
        raise HTTPException(401, "invalid token")
    return claims
