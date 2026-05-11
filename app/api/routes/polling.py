from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.dependencies import require_admin_bearer_token
from app.core.settings import Settings, get_settings
from app.db.session import get_db_session
from app.models.oauth_account import OAuthAccount
from app.models.sync_state import SyncState
from app.models.user import User
from app.services.auth import GOOGLE_PROVIDER, GoogleOAuthService
from app.services.email import EmailDeliveryService
from app.services.mobile_push import MobilePushService
from app.services.polling import POLLING_PROCESS, YouTubePollingService

router = APIRouter(prefix="/internal", tags=["polling"])


class PollRunResponse(BaseModel):
    run_outcome: str
    channels_processed: int
    channels_failed: int
    baselines_established: int
    new_videos_detected: int
    quota_blocked: bool


class ErrorResponse(BaseModel):
    detail: str


@router.post(
    "/run-poll",
    dependencies=[Depends(require_admin_bearer_token)],
    response_model=PollRunResponse,
    responses={409: {"model": ErrorResponse}, 502: {"model": ErrorResponse}},
    summary="Run monitored-channel poll",
    description="Runs one polling cycle for explicitly monitored channels and returns aggregate run metrics.",
)
def run_poll(
    settings: Settings = Depends(get_settings),
    session: Session = Depends(get_db_session),
) -> PollRunResponse:
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

    try:
        auth_service = GoogleOAuthService(settings)
        email_service = EmailDeliveryService(settings)
        polling_service = YouTubePollingService(
            auth_service=auth_service,
            email_service=email_service,
            daily_quota_budget=settings.poll_quota_daily_budget,
            safety_stop_enabled=settings.poll_quota_safety_stop_enabled,
            mobile_push_service=MobilePushService(settings),
        )
        summary = polling_service.run_poll(session, user=user, oauth_account=oauth_account)
        session.commit()
    except HTTPException:
        session.rollback()
        raise
    except Exception as exc:  # noqa: BLE001
        session.rollback()
        if "polling_service" in locals():
            polling_service.record_polling_error(session, user.id, str(exc))
        else:
            polling_state = session.scalar(
                select(SyncState).where(
                    SyncState.user_id == user.id,
                    SyncState.process_type == POLLING_PROCESS,
                )
            )
            if polling_state is None:
                polling_state = SyncState(user_id=user.id, process_type=POLLING_PROCESS)
                session.add(polling_state)
            polling_state.last_error_message = str(exc)
            polling_state.last_error_at = datetime.now(UTC)
            session.flush()
        session.commit()
        detail = "Polling run failed. Inspect service logs or stored sync state for details."
        if settings.app_env == "local":
            detail = f"Polling run failed: {exc}"
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=detail) from exc

    return PollRunResponse(
        run_outcome=summary.run_outcome,
        channels_processed=summary.channels_processed,
        channels_failed=summary.channels_failed,
        baselines_established=summary.baselines_established,
        new_videos_detected=summary.new_videos_detected,
        quota_blocked=summary.quota_blocked,
    )
