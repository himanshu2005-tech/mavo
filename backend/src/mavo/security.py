from __future__ import annotations

import secrets

from fastapi import Header, HTTPException, status


def token_guard(expected_token: str):
    async def require_token(authorization: str | None = Header(default=None)) -> None:
        scheme, _, supplied = (authorization or "").partition(" ")
        if scheme.lower() != "bearer" or not secrets.compare_digest(supplied, expected_token):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid local session token",
                headers={"WWW-Authenticate": "Bearer"},
            )

    return require_token
