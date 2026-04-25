from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.dependencies import require_internal_bearer_token
from app.core.settings import Settings, get_settings
from app.db.session import get_db_session
from app.models.oauth_account import OAuthAccount
from app.models.user import User
from app.services.auth import GOOGLE_PROVIDER, GoogleOAuthService
from app.services.polling import YouTubePollingService

router = APIRouter(prefix="/internal", tags=["internal"])


@router.post("/run-poll", dependencies=[Depends(require_internal_bearer_token)])
def run_poll(
    settings: Settings = Depends(get_settings),
    session: Session = Depends(get_db_session),
) -> dict[str, object]:
    user = session.scalar(select(User))
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Google auth must complete before polling can run.",
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
            detail="Stored Google OAuth credentials are required before polling can run.",
        )

    auth_service = GoogleOAuthService(settings)
    polling_service = YouTubePollingService(
        auth_service=auth_service,
        daily_quota_budget=settings.poll_quota_daily_budget,
        safety_stop_enabled=settings.poll_quota_safety_stop_enabled,
    )

    try:
        summary = polling_service.run_poll(session, user=user, oauth_account=oauth_account)
        session.commit()
    except HTTPException:
        session.rollback()
        raise
    except Exception as exc:  # noqa: BLE001
        session.rollback()
        polling_service.record_polling_error(session, user.id, str(exc))
        session.commit()
        detail = "Polling run failed. Inspect service logs or stored sync state for details."
        if settings.app_env == "local":
            detail = f"Polling run failed: {exc}"
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=detail) from exc

    return {
        "run_outcome": summary.run_outcome,
        "channels_processed": summary.channels_processed,
        "channels_failed": summary.channels_failed,
        "baselines_established": summary.baselines_established,
        "new_videos_detected": summary.new_videos_detected,
        "quota_blocked": summary.quota_blocked,
    }
