from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
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
from app.models.pipeline_stage import PipelineStage
from app.models.sync_state import SyncState
from app.models.user import User
from app.services.auth import GOOGLE_PROVIDER, GoogleOAuthService
from app.services.email import EmailDeliveryService
from app.services.execution_lock import ExecutionLockBusy, acquire_execution_lock
from app.services.llama_recovery import LlamaRecoveryService
from app.services.mobile_push import MobilePushService
from app.services.pipeline import PipelineService
from app.services.polling import POLLING_PROCESS, YouTubePollingService
from app.services.summary_route import build_routed_summarization_gateway, close_routed_summarization_gateway
from app.services.telegram import TelegramDeliveryService
from app.services.transcript import TranscriptService

router = APIRouter(prefix="/internal", tags=["polling"])

logger = logging.getLogger(__name__)

GOOGLE_REAUTH_REQUIRED_DETAILS = {
    "Stored Google credentials can no longer be refreshed. Manual re-auth is required.",
    "Stored Google credentials are not valid. Manual re-auth is required.",
}
GOOGLE_REAUTH_ALERT_INTERVAL = timedelta(hours=24)


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


class QuarantineReconciliationRequest(BaseModel):
    stage_id: int
    resolution_reference: str


class QuarantineReconciliationResponse(BaseModel):
    stage_id: int
    get_eligible: bool


def require_poll_execution_lock(
    session: Session = Depends(get_db_session),
) -> Generator[None, None, None]:
    try:
        lock = acquire_execution_lock(session)
        lock.__enter__()
    except ExecutionLockBusy as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Another polling or reconciliation run is already active.",
        ) from exc
    try:
        yield
    finally:
        lock.__exit__(None, None, None)


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
    finally:
        if "polling_service" in locals():
            _close_gateway_quietly(polling_service.summarization_service, root="polling")
            _close_gateway_quietly(
                polling_service.pipeline_service.summarization_service, root="polling-pipeline"
            )

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
    finally:
        if "polling_service" in locals():
            _close_gateway_quietly(polling_service.summarization_service, root="polling")
            _close_gateway_quietly(
                polling_service.pipeline_service.summarization_service, root="polling-pipeline"
            )

    return ReconciliationResponse(
        channels_processed=summary.channels_processed,
        channels_failed=summary.channels_failed,
        videos_discovered=summary.videos_discovered,
        videos_processed=summary.videos_processed,
        channel_errors=summary.channel_errors,
    )


@router.post(
    "/reconcile-quarantined-summary",
    dependencies=[Depends(require_admin_bearer_token), Depends(require_poll_execution_lock)],
    response_model=QuarantineReconciliationResponse,
    responses={404: {"model": ErrorResponse}, 409: {"model": ErrorResponse}, 422: {"model": ErrorResponse}},
    summary="Authorize GET-only reconciliation of a quarantined summary",
    description=(
        "Records an operator resolution reference after the broker task was "
        "resolved externally and enables GET-only reconciliation for a known "
        "broker task. YTPipe never queries or executes llm-broker worker "
        "resolve: the operator confirms resolution through the bounded "
        "reference. Rejects blank references, unknown stages, non-quarantined "
        "or wrong-state stages, and no-ID records."
    ),
)
def reconcile_quarantined_summary(
    request: QuarantineReconciliationRequest,
    settings: Settings = Depends(get_settings),
    session: Session = Depends(get_db_session),
) -> QuarantineReconciliationResponse:
    user = session.scalar(select(User))
    if user is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="No user is configured.")
    if not request.resolution_reference.strip() or len(request.resolution_reference) > 256:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="A resolution reference is required.")
    stage = session.scalar(
        select(PipelineStage).where(
            PipelineStage.id == request.stage_id,
            PipelineStage.user_id == user.id,
            PipelineStage.stage == "summary",
        )
    )
    if stage is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Summary stage not found.")
    polling_service = _build_polling_service(settings)
    try:
        eligible = polling_service.request_quarantine_reconciliation(
            session,
            user,
            request.stage_id,
            resolution_reference=request.resolution_reference,
            actor_source="admin_bearer",
        )
        session.commit()
    finally:
        _close_gateway_quietly(polling_service.summarization_service, root="quarantine")
        _close_gateway_quietly(
            polling_service.pipeline_service.summarization_service, root="quarantine-pipeline"
        )
    if not eligible:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Stage is not an eligible known-task quarantine awaiting operator resolution.",
        )
    return QuarantineReconciliationResponse(stage_id=request.stage_id, get_eligible=True)


def _close_gateway_quietly(service: object, *, root: str) -> None:
    """Close one routed gateway without letting a close failure mask the caller.

    Used by the polling handlers so a failing ``close()`` on one gateway
    cannot skip closing the other. Close errors are logged, never raised.
    """

    try:
        close_routed_summarization_gateway(service)
    except Exception as exc:  # noqa: BLE001
        logger.warning("polling gateway close failed", extra={"root": root, "error": str(exc)})


def _build_polling_service(settings: Settings) -> YouTubePollingService:
    polling_gateway = build_routed_summarization_gateway(settings, root="polling")
    try:
        pipeline_gateway = build_routed_summarization_gateway(settings, root="polling-pipeline")
    except Exception:
        # The second build failed after the first one already constructed its
        # broker client: release it here because the caller's
        # ``if "polling_service" in locals()`` guard would otherwise be false.
        close_routed_summarization_gateway(polling_gateway)
        raise
    return YouTubePollingService(
        auth_service=GoogleOAuthService(settings),
        email_service=EmailDeliveryService(settings),
        daily_quota_budget=settings.poll_quota_daily_budget,
        safety_stop_enabled=settings.poll_quota_safety_stop_enabled,
        shorts_processing_enabled=settings.shorts_processing_enabled,
        mobile_push_service=MobilePushService(settings),
        telegram_service=TelegramDeliveryService(settings),
        transcript_service=TranscriptService(settings),
        summarization_service=polling_gateway,
        pipeline_service=PipelineService(
            transcript_service=TranscriptService(settings),
            summarization_service=pipeline_gateway,
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
