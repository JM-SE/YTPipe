from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.dependencies import require_admin_bearer_token
from app.core.settings import Settings, get_settings
from app.db.session import get_db_session
from app.models.oauth_account import OAuthAccount
from app.models.user import User
from app.services.auth import GOOGLE_PROVIDER, GoogleOAuthService
from app.services.subscriptions import YouTubeSubscriptionService

router = APIRouter(prefix="/internal/subscriptions", tags=["subscriptions"])


class SubscriptionSyncSummary(BaseModel):
    imported_channels: int
    created_channels: int
    updated_channels: int
    created_user_channels: int
    updated_user_channels: int


class SubscriptionSyncResponse(BaseModel):
    message: str
    user: str
    subscription_sync: SubscriptionSyncSummary


class ErrorResponse(BaseModel):
    detail: str


@router.post(
    "/sync",
    dependencies=[Depends(require_admin_bearer_token)],
    response_model=SubscriptionSyncResponse,
    responses={409: {"model": ErrorResponse}, 502: {"model": ErrorResponse}},
    summary="Sync subscription catalog",
    description="Imports the YouTube subscription catalog into Channel/UserChannel records without enabling monitoring.",
)
def sync_subscription_catalog(
    settings: Settings = Depends(get_settings),
    session: Session = Depends(get_db_session),
) -> SubscriptionSyncResponse:
    user = session.scalar(select(User))
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Google auth must complete before subscription sync can run.",
        )

    oauth_account = session.scalar(
        select(OAuthAccount).where(
            OAuthAccount.user_id == user.id,
            OAuthAccount.provider == GOOGLE_PROVIDER,
        )
    )
    if oauth_account is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Stored Google OAuth credentials are required before subscription sync can run.",
        )

    auth_service = GoogleOAuthService(settings)
    subscription_service = YouTubeSubscriptionService(auth_service)

    try:
        sync_result = subscription_service.sync_subscriptions(session, user=user, oauth_account=oauth_account)
        session.commit()
    except HTTPException:
        session.rollback()
        raise
    except Exception as exc:
        session.rollback()
        subscription_service.record_sync_error(session, user.id, str(exc))
        session.commit()
        detail = "Subscription sync failed. Inspect service logs or stored sync state for details."
        if settings.app_env == "local":
            detail = f"Subscription sync failed: {exc}"
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=detail) from exc

    return SubscriptionSyncResponse(
        message="Subscription catalog sync completed.",
        user=user.email,
        subscription_sync=SubscriptionSyncSummary(
            imported_channels=sync_result.imported_channels,
            created_channels=sync_result.created_channels,
            updated_channels=sync_result.updated_channels,
            created_user_channels=sync_result.created_user_channels,
            updated_user_channels=sync_result.updated_user_channels,
        ),
    )
