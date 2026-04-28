from __future__ import annotations

from fastapi import Depends, Header, HTTPException, status

from app.core.settings import Settings, get_settings


def _validate_expected_bearer_token(
    authorization: str | None,
    expected_token: str,
    placeholder: str,
    missing_detail: str,
    invalid_detail: str,
) -> None:
    if not expected_token or expected_token == placeholder:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=missing_detail,
        )

    if authorization != f"Bearer {expected_token}":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=invalid_detail,
        )


def validate_internal_bearer_token(authorization: str | None, settings: Settings) -> None:
    _validate_expected_bearer_token(
        authorization=authorization,
        expected_token=settings.internal_api_bearer_token,
        placeholder="replace-me-internal",
        missing_detail="Internal API bearer token is not configured.",
        invalid_detail="Invalid internal bearer token.",
    )


def validate_mobile_bearer_token(authorization: str | None, settings: Settings) -> None:
    _validate_expected_bearer_token(
        authorization=authorization,
        expected_token=settings.mobile_api_bearer_token,
        placeholder="replace-me-mobile",
        missing_detail="Mobile API bearer token is not configured.",
        invalid_detail="Invalid mobile bearer token.",
    )


def validate_admin_bearer_token(authorization: str | None, settings: Settings) -> None:
    internal_error: HTTPException | None = None
    mobile_error: HTTPException | None = None

    try:
        validate_mobile_bearer_token(authorization, settings)
        return
    except HTTPException as exc:
        mobile_error = exc

    try:
        validate_internal_bearer_token(authorization, settings)
        return
    except HTTPException as exc:
        internal_error = exc

    if (
        mobile_error is not None
        and internal_error is not None
        and mobile_error.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
        and internal_error.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
    ):
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Admin bearer tokens are not configured.",
        )

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid admin bearer token.",
    )


def require_internal_bearer_token(
    authorization: str | None = Header(default=None),
    settings: Settings = Depends(get_settings),
) -> None:
    validate_internal_bearer_token(authorization, settings)


def require_mobile_bearer_token(
    authorization: str | None = Header(default=None),
    settings: Settings = Depends(get_settings),
) -> None:
    validate_mobile_bearer_token(authorization, settings)


def require_admin_bearer_token(
    authorization: str | None = Header(default=None),
    settings: Settings = Depends(get_settings),
) -> None:
    validate_admin_bearer_token(authorization, settings)
