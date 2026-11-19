import os

import jwt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

bearer = HTTPBearer()


def current_user(creds: HTTPAuthorizationCredentials = Depends(bearer)) -> dict:
    """Verified claims. `sub` (vendor id) is mandatory: identity is never taken from a request body."""
    try:
        claims = jwt.decode(creds.credentials, os.environ["JWT_SECRET"], algorithms=["HS256"])
    except jwt.PyJWTError:
        raise HTTPException(401, "invalid token")
    if not claims.get("sub"):
        raise HTTPException(401, "token has no subject")
    claims["sub"] = str(claims["sub"])
    return claims
