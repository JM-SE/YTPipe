from __future__ import annotations

from datetime import UTC, datetime, timedelta
import threading
from collections.abc import Generator
from urllib.parse import urlparse

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
from app.services.llama_recovery import LlamaRecoveryService
from app.services.mobile_push import MobilePushService
from app.services.pipeline import PipelineService
from app.services.polling import POLLING_PROCESS, YouTubePollingService
from app.services.summarization import SummarizationService
from app.services.telegram import TelegramDeliveryService
from app.services.transcript import TranscriptService

router = APIRouter(prefix="/internal", tags=["polling"])

GOOGLE_REAUTH_REQUIRED_DETAILS = {
    "Stored Google credentials can no longer be refreshed. Manual re-auth is required.",
    "Stored Google credentials are not valid. Manual re-auth is required.",
}
GOOGLE_REAUTH_ALERT_INTERVAL = timedelta(hours=24)
_POLL_EXECUTION_LOCK = threading.Lock()


class PollRunResponse(BaseModel):
    run_outcome: str
    channels_processed: int
    channels_failed: int
    baselines_established: int
    new_videos_detected: int
    quota_blocked: bool


class ErrorResponse(BaseModel):
    detail: str


class ReconciliationResponse(BaseModel):
    channels_processed: int
    channels_failed: int
    videos_discovered: int
    videos_processed: int
    channel_errors: list[dict[str, object]]


class ReconciliationRequest(BaseModel):
    process_recovered: bool = False


def require_poll_execution_lock() -> Generator[None, None, None]:
    if not _POLL_EXECUTION_LOCK.acquire(blocking=False):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Another polling or reconciliation run is already active.",
        )
    try:
        yield
    finally:
        _POLL_EXECUTION_LOCK.release()


@router.post(
    "/run-poll",
    dependencies=[Depends(require_admin_bearer_token), Depends(require_poll_execution_lock)],
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
        polling_service = _build_polling_service(settings)
        summary = polling_service.run_poll(session, user=user, oauth_account=oauth_account)
        session.commit()
    except HTTPException as exc:
        session.rollback()
        if _is_google_reauth_required(exc):
            _record_google_reauth_failure(
                session=session,
                user_id=user.id,
                settings=settings,
                detail=str(exc.detail),
            )
            session.commit()
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


@router.post(
    "/reconcile-missing-uploads",
    dependencies=[Depends(require_admin_bearer_token), Depends(require_poll_execution_lock)],
    response_model=ReconciliationResponse,
    responses={409: {"model": ErrorResponse}, 502: {"model": ErrorResponse}},
    summary="Recover uploads missed during an incident",
    description="Pages each monitored uploads playlist back to its durable marker and processes recovered videos oldest-first.",
)
def reconcile_missing_uploads(
    request: ReconciliationRequest,
    settings: Settings = Depends(get_settings),
    session: Session = Depends(get_db_session),
) -> ReconciliationResponse:
    user = session.scalar(select(User))
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Google auth must complete before reconciliation can run.",
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
            detail="Stored Google OAuth credentials are required before reconciliation can run.",
        )

    try:
        polling_service = _build_polling_service(settings)
        summary = polling_service.reconcile_missing_uploads(
            session,
            user,
            oauth_account,
            max_pages_per_channel=settings.reconciliation_max_pages_per_channel,
            process_recovered=request.process_recovered,
            drain_pause_seconds=settings.pipeline_drain_pause_seconds,
        )
        session.commit()
    except HTTPException:
        session.rollback()
        raise
    except Exception as exc:  # noqa: BLE001
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Reconciliation failed. Inspect service logs or reconciliation state for details.",
        ) from exc

    return ReconciliationResponse(
        channels_processed=summary.channels_processed,
        channels_failed=summary.channels_failed,
        videos_discovered=summary.videos_discovered,
        videos_processed=summary.videos_processed,
        channel_errors=summary.channel_errors,
    )


def _build_polling_service(settings: Settings) -> YouTubePollingService:
    return YouTubePollingService(
        auth_service=GoogleOAuthService(settings),
        email_service=EmailDeliveryService(settings),
        daily_quota_budget=settings.poll_quota_daily_budget,
        safety_stop_enabled=settings.poll_quota_safety_stop_enabled,
        shorts_processing_enabled=settings.shorts_processing_enabled,
        mobile_push_service=MobilePushService(settings),
        telegram_service=TelegramDeliveryService(settings),
        transcript_service=TranscriptService(settings),
        summarization_service=SummarizationService(settings),
        pipeline_service=PipelineService(
            transcript_service=TranscriptService(settings),
            summarization_service=SummarizationService(settings),
            telegram_service=TelegramDeliveryService(settings),
            startup_batch_size=settings.pipeline_startup_batch_size,
            startup_batch_delay_seconds=settings.pipeline_startup_batch_delay_seconds,
            shorts_processing_enabled=settings.shorts_processing_enabled,
        ),
        pipeline_drain_pause_seconds=settings.pipeline_drain_pause_seconds,
        llama_recovery_service=LlamaRecoveryService(
            enabled=settings.llama_cpp_auto_restart_enabled,
            cooldown_seconds=settings.llama_cpp_restart_cooldown_seconds,
        ),
    )


def _is_google_reauth_required(exc: HTTPException) -> bool:
    detail = exc.detail if isinstance(exc.detail, str) else None
    return exc.status_code == status.HTTP_401_UNAUTHORIZED and detail in GOOGLE_REAUTH_REQUIRED_DETAILS


def _record_google_reauth_failure(
    *,
    session: Session,
    user_id: int,
    settings: Settings,
    detail: str,
) -> None:
    now = datetime.now(UTC)
    polling_state = session.scalar(
        select(SyncState).where(
            SyncState.user_id == user_id,
            SyncState.process_type == POLLING_PROCESS,
        )
    )
    if polling_state is None:
        polling_state = SyncState(user_id=user_id, process_type=POLLING_PROCESS)
        session.add(polling_state)

    metadata = dict(polling_state.state_metadata or {})
    polling_state.last_error_at = now
    polling_state.last_error_message = detail

    metadata["google_reauth_required"] = True
    metadata["google_reauth_last_error"] = detail
    metadata["google_reauth_detected_at"] = now.isoformat()

    if _should_send_google_reauth_alert(metadata, now):
        try:
            telegram_service = TelegramDeliveryService(settings)
            if telegram_service.enabled:
                telegram_service.send_message(_build_google_reauth_message(settings))
                metadata["google_reauth_alert_sent_at"] = now.isoformat()
                metadata["google_reauth_alert_error"] = None
            else:
                metadata["google_reauth_alert_error"] = "Telegram notifications are disabled."
        except Exception as exc:  # noqa: BLE001
            metadata["google_reauth_alert_error"] = str(exc)

    polling_state.state_metadata = metadata
    session.flush()


def _should_send_google_reauth_alert(metadata: dict[str, object], now: datetime) -> bool:
    raw_last_sent_at = metadata.get("google_reauth_alert_sent_at")
    if not isinstance(raw_last_sent_at, str) or not raw_last_sent_at:
        return True

    try:
        last_sent_at = datetime.fromisoformat(raw_last_sent_at)
    except ValueError:
        return True

    if last_sent_at.tzinfo is None:
        last_sent_at = last_sent_at.replace(tzinfo=UTC)
    else:
        last_sent_at = last_sent_at.astimezone(UTC)

    return now - last_sent_at >= GOOGLE_REAUTH_ALERT_INTERVAL


def _build_google_reauth_message(settings: Settings) -> str:
    parsed_redirect = urlparse(settings.google_redirect_uri)
    scheme = parsed_redirect.scheme or "http"
    netloc = parsed_redirect.netloc or "127.0.0.1:8000"
    port = parsed_redirect.port or (443 if scheme == "https" else 80)
    auth_url = f"{scheme}://{netloc}/auth/google"

    return (
        "⚠️ YTPipe necesita re-auth de Google\n\n"
        "No se pudo refrescar el token de Google, por lo que el polling de YouTube quedo detenido.\n\n"
        "Como reautenticar desde la PC principal:\n\n"
        "1. Abrir un tunel SSH y mantener la terminal abierta:\n"
        f"   ssh -N -L 127.0.0.1:{port}:127.0.0.1:{port} <usuario>@<homelab-host-o-ip>\n\n"
        "2. Abrir en el navegador:\n"
        f"   {auth_url}\n\n"
        "3. Completar el login de Google.\n\n"
        "4. Avisar para correr un poll manual y verificar."
    )
