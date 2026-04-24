from __future__ import annotations

from fastapi import Depends, Header, HTTPException, status

from app.core.settings import Settings, get_settings


def require_internal_bearer_token(
    authorization: str | None = Header(default=None),
    settings: Settings = Depends(get_settings),
) -> None:
    expected = settings.internal_api_bearer_token
    if not expected or expected == "replace-me-internal":
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal API bearer token is not configured.",
        )

    if authorization != f"Bearer {expected}":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid internal bearer token.",
        )
