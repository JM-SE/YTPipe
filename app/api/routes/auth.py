from __future__ import annotations

from fastapi import APIRouter, Cookie, Depends, HTTPException, Query, status
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.core.settings import Settings, get_settings
from app.db.session import get_db_session
from app.services.auth import (
    AuthConfigurationError,
    AuthFlowError,
    AuthStateError,
    GoogleOAuthService,
    OAUTH_PKCE_COOKIE_NAME,
    OAUTH_STATE_COOKIE_NAME,
    SingleUserViolation,
)

router = APIRouter(prefix="/auth", tags=["auth"])


@router.get("/google")
def start_google_oauth(settings: Settings = Depends(get_settings)) -> RedirectResponse:
    service = GoogleOAuthService(settings)
    try:
        authorization_url, nonce, code_verifier = service.create_authorization_url()
    except AuthConfigurationError as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)) from exc
    response = RedirectResponse(url=authorization_url, status_code=status.HTTP_302_FOUND)
    response.set_cookie(
        key=OAUTH_STATE_COOKIE_NAME,
        value=nonce,
        httponly=True,
        max_age=600,
        samesite="lax",
        secure=settings.app_env != "local",
    )
    response.set_cookie(
        key=OAUTH_PKCE_COOKIE_NAME,
        value=code_verifier,
        httponly=True,
        max_age=600,
        samesite="lax",
        secure=settings.app_env != "local",
    )
    return response


@router.get("/callback")
def google_oauth_callback(
    code: str = Query(...),
    state: str = Query(...),
    oauth_nonce: str | None = Cookie(default=None, alias=OAUTH_STATE_COOKIE_NAME),
    oauth_pkce_verifier: str | None = Cookie(default=None, alias=OAUTH_PKCE_COOKIE_NAME),
    settings: Settings = Depends(get_settings),
    session: Session = Depends(get_db_session),
) -> JSONResponse:
    auth_service = GoogleOAuthService(settings)

    try:
        oauth_result = auth_service.exchange_code_for_account(
            session,
            code=code,
            state=state,
            cookie_nonce=oauth_nonce,
            cookie_code_verifier=oauth_pkce_verifier,
        )
        session.commit()
    except AuthStateError as exc:
        session.rollback()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except SingleUserViolation as exc:
        session.rollback()
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except AuthConfigurationError as exc:
        session.rollback()
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)) from exc
    except AuthFlowError as exc:
        session.rollback()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except HTTPException:
        session.rollback()
        raise
    except Exception as exc:
        session.rollback()
        detail = "OAuth callback failed after token exchange. Inspect service logs for details."
        if settings.app_env == "local":
            detail = f"OAuth callback failed: {exc}"
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=detail,
        ) from exc

    response = JSONResponse(
        {
            "message": "Google auth succeeded. Run the internal subscription sync to import the channel catalog.",
            "user": oauth_result.user.email,
            "scopes": list(settings.google_oauth_scopes),
            "subscription_sync_required": True,
            "subscription_sync_endpoint": "/internal/subscriptions/sync",
        },
    )
    response.delete_cookie(OAUTH_STATE_COOKIE_NAME)
    response.delete_cookie(OAUTH_PKCE_COOKIE_NAME)
    return response
