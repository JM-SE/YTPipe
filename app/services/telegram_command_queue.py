from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import logging
from secrets import token_hex

from sqlalchemy import or_, select, update
from sqlalchemy.orm import Session

from app.core.settings import Settings
from app.models.oauth_account import OAuthAccount
from app.models.sync_state import SyncState
from app.models.telegram_command_request import TelegramCommandRequest
from app.models.user import User
from app.models.video import Video
from app.services.auth import GOOGLE_PROVIDER, GoogleOAuthService
from app.services.execution_lock import acquire_execution_lock
from app.services.llama_recovery import LlamaRecoveryService
from app.services.pipeline import PipelineService
from app.services.summarization import SummarizationService
from app.services.telegram import TelegramDeliveryAttemptError, TelegramDeliveryService
from app.services.transcript import TranscriptService
from app.services.youtube_video_metadata import YouTubeMetadataError, YouTubeVideoMetadataService


logger = logging.getLogger(__name__)

PROCESSING_TERMINAL = {"completed", "failed", "rejected"}
REPLY_DUE = {"pending", "pending_retry"}
LEASE_SECONDS = 3600
RETRY_DELAYS = (30, 120, 600)
SUMMARY_CIRCUIT_PROCESS = "summarization"


@dataclass(frozen=True)
class CommandWorkerResult:
    claimed: bool
    request_id: int | None
    status: str
    work_remaining: bool


class TelegramCommandQueueService:
    def __init__(self, settings: Settings, session: Session):
        self.settings = settings
        self.session = session

    def process_next(self) -> CommandWorkerResult:
        if not self.settings.telegram_commands_enabled:
            return CommandWorkerResult(False, None, "commands_disabled", False)

        with acquire_execution_lock(self.session):
            self._recover_stale_claims()
            self._terminalize_exhausted_requests()
            request, kind = self._select_candidate()
            if request is None:
                self.session.commit()
                return CommandWorkerResult(False, None, "empty", False)

            if not self._authorized_destination(request):
                self._suppress_unauthorized_request(request)
                self.session.commit()
                return CommandWorkerResult(True, request.id, request.status, False)

            if kind == "processing" and self._has_older_content_leader(request):
                request.status = "pending_retry"
                request.next_attempt_at = self._leader_retry_time(request)
                request.last_error = "Waiting for shared video content processing."
                self.session.commit()
                return CommandWorkerResult(True, request.id, request.status, True)

            token = token_hex(24)
            self._claim(request, kind, token)
            self.session.commit()

            if kind == "reply":
                self._deliver_reply(request.id, token)
            else:
                self._process_content(request.id, token)

            current = self.session.get(TelegramCommandRequest, request.id)
            status = current.status if current is not None else "failed"
            work_remaining = bool(
                current is not None
                and (
                    current.status in {"pending", "processing", "pending_retry"}
                    or current.reply_status in {"pending", "sending", "pending_retry"}
                )
            )
            return CommandWorkerResult(True, request.id, status, work_remaining)

    def _has_older_content_leader(self, request: TelegramCommandRequest) -> bool:
        if not request.youtube_video_id:
            return False
        return self.session.scalar(
            select(TelegramCommandRequest.id)
            .where(
                TelegramCommandRequest.id < request.id,
                TelegramCommandRequest.youtube_video_id == request.youtube_video_id,
                TelegramCommandRequest.status.in_({"pending", "processing", "pending_retry"}),
            )
            .order_by(TelegramCommandRequest.id.asc())
            .limit(1)
        ) is not None

    def _leader_retry_time(self, request: TelegramCommandRequest) -> datetime:
        leader = self.session.scalar(
            select(TelegramCommandRequest)
            .where(
                TelegramCommandRequest.id < request.id,
                TelegramCommandRequest.youtube_video_id == request.youtube_video_id,
                TelegramCommandRequest.status.in_({"pending", "processing", "pending_retry"}),
            )
            .order_by(TelegramCommandRequest.id.asc())
            .limit(1)
        )
        now = datetime.now(UTC)
        if leader is not None and leader.next_attempt_at is not None:
            return max(leader.next_attempt_at, now + timedelta(seconds=30))
        return now + timedelta(seconds=30)

    def _select_candidate(self) -> tuple[TelegramCommandRequest | None, str | None]:
        now = datetime.now(UTC)
        processing_due = (
            (TelegramCommandRequest.status == "pending")
            | (
                (TelegramCommandRequest.status == "pending_retry")
                & (
                    TelegramCommandRequest.next_attempt_at.is_(None)
                    | (TelegramCommandRequest.next_attempt_at <= now)
                )
            )
        )
        reply_due = (
            TelegramCommandRequest.status.in_(PROCESSING_TERMINAL)
            & TelegramCommandRequest.reply_status.in_(REPLY_DUE)
            & (
                TelegramCommandRequest.reply_next_attempt_at.is_(None)
                | (TelegramCommandRequest.reply_next_attempt_at <= now)
            )
        )
        request = self.session.scalar(
            select(TelegramCommandRequest)
            .where(or_(processing_due, reply_due))
            .order_by(TelegramCommandRequest.created_at.asc(), TelegramCommandRequest.id.asc())
            .limit(1)
        )
        if request is None:
            return None, None
        return request, "processing" if self._is_processing_due(request, now) else "reply"

    @staticmethod
    def _is_processing_due(request: TelegramCommandRequest, now: datetime) -> bool:
        return request.status == "pending" or (
            request.status == "pending_retry"
            and (request.next_attempt_at is None or request.next_attempt_at <= now)
        )

    def _recover_stale_claims(self) -> None:
        now = datetime.now(UTC)
        processing_rows = self.session.scalars(
            select(TelegramCommandRequest).where(
                TelegramCommandRequest.status == "processing",
                TelegramCommandRequest.lease_expires_at < now,
            )
        ).all()
        for request in processing_rows:
            request.status = "pending_retry"
            request.next_attempt_at = now
            request.processing_started_at = None
            request.lease_expires_at = None
            request.lease_token = None
            request.last_error = "Previous command processing lease expired."

        reply_rows = self.session.scalars(
            select(TelegramCommandRequest).where(
                TelegramCommandRequest.reply_status == "sending",
                TelegramCommandRequest.lease_expires_at < now,
            )
        ).all()
        for request in reply_rows:
            request.reply_status = "pending_retry"
            request.reply_next_attempt_at = now
            request.reply_started_at = None
            request.lease_expires_at = None
            request.lease_token = None
            request.last_error = "Previous Telegram reply lease expired."
        self.session.flush()

    def _terminalize_exhausted_requests(self) -> None:
        rows = self.session.scalars(
            select(TelegramCommandRequest).where(
                TelegramCommandRequest.status.in_({"pending", "pending_retry"}),
                TelegramCommandRequest.attempt_count >= TelegramCommandRequest.max_attempts,
            )
        ).all()
        for request in rows:
            request.status = "failed"
            request.next_attempt_at = None
            request.last_error = "Command processing attempts were exhausted."

        reply_rows = self.session.scalars(
            select(TelegramCommandRequest).where(
                TelegramCommandRequest.status.in_(PROCESSING_TERMINAL),
                TelegramCommandRequest.reply_status.in_(REPLY_DUE),
                TelegramCommandRequest.reply_attempt_count >= TelegramCommandRequest.reply_max_attempts,
            )
        ).all()
        for request in reply_rows:
            request.reply_status = "failed"
            request.reply_next_attempt_at = None
            request.last_error = "Telegram reply attempts were exhausted."
        self.session.flush()

    def _claim(self, request: TelegramCommandRequest, kind: str, token: str) -> None:
        now = datetime.now(UTC)
        request.lease_token = token
        request.lease_expires_at = now + timedelta(seconds=LEASE_SECONDS)
        if kind == "processing":
            request.status = "processing"
            request.processing_started_at = now
            request.next_attempt_at = None
        else:
            request.reply_status = "sending"
            request.reply_started_at = now
            request.reply_attempt_count += 1
            request.reply_next_attempt_at = None

    def _process_content(self, request_id: int, token: str) -> None:
        request = self.session.get(TelegramCommandRequest, request_id)
        if request is None or not self._owns(request, token):
            return

        user = self.session.scalar(select(User))
        if user is None:
            self._finish_processing(
                request,
                token,
                "pending_retry",
                "Google authentication is required.",
                delay_seconds=3600,
            )
            return

        video = self.session.get(Video, request.video_id) if request.video_id is not None else None
        if video is None:
            oauth_account = self.session.scalar(
                select(OAuthAccount).where(
                    OAuthAccount.user_id == user.id,
                    OAuthAccount.provider == GOOGLE_PROVIDER,
                )
            )
            if oauth_account is None:
                self._finish_processing(
                    request,
                    token,
                    "pending_retry",
                    "Google authentication is required.",
                    delay_seconds=3600,
                )
                return
            if not self._increment_metadata_attempt(request, token):
                return
            metadata_service = YouTubeVideoMetadataService(
                GoogleOAuthService(self.settings),
                daily_quota_budget=self.settings.poll_quota_daily_budget,
                safety_stop_enabled=self.settings.poll_quota_safety_stop_enabled,
            )
            try:
                video = metadata_service.resolve_and_upsert(
                    self.session,
                    user=user,
                    oauth_account=oauth_account,
                    youtube_video_id=request.youtube_video_id or "",
                )
                request.video_id = video.id
                self.session.flush()
            except YouTubeMetadataError as exc:
                if exc.code in {"google_reauth_required", "quota_blocked", "quota_exceeded"}:
                    if exc.code == "google_reauth_required":
                        self._record_google_reauth_pause(user.id, exc.message)
                    request.attempt_count = max(0, request.attempt_count - 1)
                    self._finish_processing(
                        request,
                        token,
                        "pending_retry",
                        exc.message,
                        delay_seconds=3600 if exc.code == "google_reauth_required" else 86400,
                    )
                elif exc.retryable:
                    if request.attempt_count >= request.max_attempts:
                        self._finish_processing(request, token, "failed", exc.message)
                    else:
                        self._finish_processing(
                            request,
                            token,
                            "pending_retry",
                            exc.message,
                            delay_seconds=self._retry_delay(request.attempt_count),
                        )
                else:
                    self._finish_processing(request, token, "failed", exc.message)
                return

        if video.is_short is True and not self.settings.shorts_processing_enabled:
            self._finish_processing(request, token, "rejected", "short_disabled")
            return
        if video.summary is not None:
            self._finish_processing(request, token, "completed", None)
            return

        pipeline = self._build_pipeline(user, video)
        content_result = pipeline.process_content_stages(self.session, user, video)
        if content_result.outcome == "completed":
            self._finish_processing(request, token, "completed", None)
        elif content_result.outcome == "failed":
            self._finish_processing(request, token, "failed", content_result.error)
        else:
            if pipeline.summary_paused:
                self._attempt_llama_recovery(user.id)
            self._finish_processing(
                request,
                token,
                "pending_retry",
                content_result.error or "Content processing is temporarily pending.",
                delay_seconds=300 if pipeline.summary_paused else 30,
            )

    def _deliver_reply(self, request_id: int, token: str) -> None:
        request = self.session.get(TelegramCommandRequest, request_id)
        if request is None or not self._owns(request, token):
            return
        if not self._authorized_destination(request):
            self._suppress_unauthorized_request(request)
            self.session.commit()
            return

        message = self._reply_message(request)
        telegram = TelegramDeliveryService(self.settings)
        try:
            if not self._owns(request, token):
                return
            result = telegram.send_message_to_chat(
                message,
                chat_id=request.telegram_chat_id,
                reply_to_message_id=request.telegram_message_id,
            )
        except TelegramDeliveryAttemptError as exc:
            if not exc.retryable or request.reply_attempt_count >= request.reply_max_attempts:
                values = {
                    "reply_status": "failed",
                    "last_error": _sanitize_error(exc.message),
                    "reply_next_attempt_at": None,
                }
            else:
                values = {
                    "reply_status": "pending_retry",
                    "reply_next_attempt_at": datetime.now(UTC) + timedelta(
                        seconds=exc.retry_after_seconds or self._retry_delay(request.reply_attempt_count)
                    ),
                    "last_error": _sanitize_error(exc.message),
                }
            if not self._conditional_update(request.id, token, values):
                return
            self.session.commit()
            return

        if not self._conditional_update(
            request.id,
            token,
            {
                "reply_status": "sent",
                "reply_sent_at": datetime.now(UTC),
                "telegram_reply_message_id": result.provider_message_id,
                "last_error": None,
            },
        ):
            return
        self.session.commit()

    def _finish_processing(
        self,
        request: TelegramCommandRequest,
        token: str,
        status: str,
        error: str | None,
        *,
        delay_seconds: int | None = None,
    ) -> None:
        if not self._owns(request, token):
            return
        if not self._conditional_update(
            request.id,
            token,
            {
                "status": status,
                "last_error": _sanitize_error(error) if error else None,
                "next_attempt_at": (
                    datetime.now(UTC) + timedelta(seconds=delay_seconds)
                    if status == "pending_retry" and delay_seconds is not None
                    else None
                ),
                "processing_started_at": None,
            },
        ):
            return
        self.session.commit()

    def _increment_metadata_attempt(
        self,
        request: TelegramCommandRequest,
        token: str,
    ) -> bool:
        result = self.session.execute(
            update(TelegramCommandRequest)
            .where(
                TelegramCommandRequest.id == request.id,
                TelegramCommandRequest.lease_token == token,
                TelegramCommandRequest.attempt_count < TelegramCommandRequest.max_attempts,
            )
            .values(attempt_count=TelegramCommandRequest.attempt_count + 1)
        )
        if result.rowcount != 1:
            self.session.rollback()
            return False
        self.session.commit()
        self.session.refresh(request)
        return True

    def _conditional_update(self, request_id: int, token: str, values: dict[str, object]) -> bool:
        values.update({"lease_expires_at": None, "lease_token": None})
        result = self.session.execute(
            update(TelegramCommandRequest)
            .where(
                TelegramCommandRequest.id == request_id,
                TelegramCommandRequest.lease_token == token,
            )
            .values(**values)
        )
        if result.rowcount == 1:
            return True
        self.session.rollback()
        return False

    def _owns(self, request: TelegramCommandRequest, token: str) -> bool:
        current = self.session.scalar(
            select(TelegramCommandRequest).where(
                TelegramCommandRequest.id == request.id,
                TelegramCommandRequest.lease_token == token,
            )
        )
        return current is not None

    def _authorized_destination(self, request: TelegramCommandRequest) -> bool:
        try:
            allowed_user_id = int(self.settings.telegram_allowed_user_id)
            configured_chat_id = int(self.settings.telegram_chat_id)
        except ValueError:
            return False
        return (
            request.telegram_user_id == allowed_user_id
            and request.telegram_chat_id == configured_chat_id
        )

    @staticmethod
    def _suppress_unauthorized_request(request: TelegramCommandRequest) -> None:
        if request.status not in PROCESSING_TERMINAL:
            request.status = "rejected"
            request.last_error = "Telegram command authorization changed."
        request.reply_status = "suppressed"
        request.lease_expires_at = None
        request.lease_token = None

    def _build_pipeline(self, user: User, video: Video) -> PipelineService:
        summary_state = self.session.scalar(
            select(SyncState).where(
                SyncState.user_id == user.id,
                SyncState.process_type == SUMMARY_CIRCUIT_PROCESS,
            )
        )
        summary_paused = bool(
            summary_state and (summary_state.state_metadata or {}).get("paused", False)
        )
        return PipelineService(
            transcript_service=TranscriptService(self.settings),
            summarization_service=SummarizationService(self.settings),
            startup_batch_size=0,
            summary_paused=summary_paused,
            shorts_processing_enabled=self.settings.shorts_processing_enabled,
        )

    def _record_google_reauth_pause(self, user_id: int, error: str) -> None:
        state = self.session.scalar(
            select(SyncState).where(
                SyncState.user_id == user_id,
                SyncState.process_type == "polling",
            )
        )
        if state is None:
            state = SyncState(user_id=user_id, process_type="polling")
            self.session.add(state)
            self.session.flush()
        metadata = dict(state.state_metadata or {})
        metadata["google_reauth_required"] = True
        metadata["google_reauth_last_error"] = _sanitize_error(error)
        metadata["google_reauth_detected_at"] = datetime.now(UTC).isoformat()
        state.last_error_at = datetime.now(UTC)
        state.last_error_message = _sanitize_error(error)
        state.state_metadata = metadata
        self.session.flush()

    def _attempt_llama_recovery(self, user_id: int) -> None:
        state = self.session.scalar(
            select(SyncState).where(
                SyncState.user_id == user_id,
                SyncState.process_type == SUMMARY_CIRCUIT_PROCESS,
            )
        )
        if state is None:
            return
        metadata = dict(state.state_metadata or {})
        if not self.settings.llama_cpp_auto_restart_enabled:
            return
        previous = metadata.get("restart_attempted_at")
        if isinstance(previous, str):
            try:
                elapsed = datetime.now(UTC) - datetime.fromisoformat(previous)
                if elapsed.total_seconds() < self.settings.llama_cpp_restart_cooldown_seconds:
                    return
            except ValueError:
                pass
        result = LlamaRecoveryService(
            enabled=True,
            cooldown_seconds=self.settings.llama_cpp_restart_cooldown_seconds,
        ).restart()
        metadata["restart_attempted_at"] = datetime.now(UTC).isoformat()
        metadata["restart_succeeded"] = result.succeeded
        metadata["restart_reason"] = _sanitize_error(result.reason)
        state.state_metadata = metadata
        self.session.flush()

    @staticmethod
    def _reply_message(request: TelegramCommandRequest) -> str:
        if request.status == "completed":
            video = request.video
            summary = video.summary if video is not None else None
            return _truncate_telegram_text(f"📝 {summary or 'El resumen no está disponible.'}")
        if request.last_error == "short_disabled":
            return "Este video es un Short y su procesamiento está desactivado."
        if request.status == "rejected":
            return "No pude procesar esta solicitud. Revisa la URL o la configuración actual."
        if request.status == "failed":
            return "No se pudo completar el resumen de este video."
        return "No se pudo completar la solicitud."

    @staticmethod
    def _retry_delay(attempt_count: int) -> int:
        return RETRY_DELAYS[min(max(attempt_count - 1, 0), len(RETRY_DELAYS) - 1)]


def _sanitize_error(error: str | None) -> str | None:
    if not error:
        return None
    return " ".join(str(error).replace("\n", " ").replace("\r", " ").split())[:500]


def _truncate_telegram_text(text: str) -> str:
    if len(text) <= 3800:
        return text
    return text[:3800].rsplit(" ", 1)[0]
