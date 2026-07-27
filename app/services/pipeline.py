from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.channel import Channel
from app.models.pipeline_stage import PipelineStage
from app.models.sync_state import SyncState
from app.models.user import User
from app.models.video import Video
from app.services.summarization import SummarizationService
from app.services.telegram import TelegramDeliveryAttemptError, TelegramDeliveryService, TelegramNotificationPayload
from app.services.transcript import TranscriptService

logger = logging.getLogger(__name__)
_SUMMARY_INFERENCE_LOCK = threading.Lock()

STAGE_TRANSCRIPT = "transcript"
STAGE_SUMMARY = "summary"
STAGE_TELEGRAM = "telegram"
STAGE_FALLBACK_TELEGRAM = "fallback_telegram"
SUMMARIZATION_PROCESS = "summarization"

STATUS_PENDING = "pending"
STATUS_PENDING_RETRY = "pending_retry"
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"
STATUS_SKIPPED = "skipped"
SHORT_PROCESSING_DISABLED_ERROR = "Short processing is disabled by configuration."

FALLBACK_MESSAGE_TEMPLATE = (
    "⚠️ No se pudo completar el envío\n"
    "🎬 {title}\n"
    "{channel}\n"
    "{url}\n\n"
    "Motivo: {reason}"
)

FALLBACK_REASON_TRANSCRIPT = "No se pudo obtener la transcripción después de {max_attempts} intentos."
FALLBACK_REASON_SUMMARY = "No se pudo generar el resumen después de {max_attempts} intentos."
FALLBACK_REASON_TELEGRAM = "No se pudo enviar el mensaje por Telegram después de {max_attempts} intentos."


def _compact_error(message: str) -> str:
    compacted = " ".join(message.replace("\n", " ").replace("\r", " ").split())
    return compacted[:500] or "Unknown summarization failure."


@dataclass
class PipelineProcessingStats:
    stages_processed: int
    stages_succeeded: int
    stages_failed: int
    stages_skipped: int
    fallbacks_sent: int
    summary_attempted: bool = False


@dataclass
class PipelineDrainStats:
    videos_processed: int
    stages_processed: int
    stages_succeeded: int
    stages_failed: int
    stages_skipped: int
    fallbacks_sent: int
    summary_attempted: bool = False


class PipelineService:
    def __init__(
        self,
        transcript_service: TranscriptService | None = None,
        summarization_service: SummarizationService | None = None,
        telegram_service: TelegramDeliveryService | None = None,
        startup_batch_size: int = 0,
        startup_batch_delay_seconds: float = 30.0,
        summary_paused: bool = False,
        shorts_processing_enabled: bool = True,
    ):
        self.transcript_service = transcript_service
        self.summarization_service = summarization_service
        self.telegram_service = telegram_service
        self.startup_batch_size = max(0, startup_batch_size)
        self.startup_batch_delay_seconds = max(0, startup_batch_delay_seconds)
        self.summary_paused = summary_paused
        self.shorts_processing_enabled = shorts_processing_enabled
        self.summary_pause_reason: str | None = None
        self.summary_pause_video_id: int | None = None
        self.summary_recovery_succeeded = False

    def create_stages_for_video(self, session: Session, user_id: int, video_id: int) -> list[PipelineStage]:
        video = session.get(Video, video_id)
        if video is not None and not self._should_process_video(video):
            return session.scalars(
                select(PipelineStage).where(
                    PipelineStage.video_id == video_id,
                    PipelineStage.user_id == user_id,
                )
            ).all()

        stages = []
        for stage_name in (STAGE_TRANSCRIPT, STAGE_SUMMARY, STAGE_TELEGRAM):
            existing = session.scalar(
                select(PipelineStage).where(
                    PipelineStage.video_id == video_id,
                    PipelineStage.user_id == user_id,
                    PipelineStage.stage == stage_name,
                )
            )
            if existing is not None:
                stages.append(existing)
                continue

            ps = PipelineStage(
                video_id=video_id,
                user_id=user_id,
                stage=stage_name,
                status=STATUS_PENDING,
            )
            session.add(ps)
            stages.append(ps)

        session.flush()
        return stages

    def process_new_video_stages(
        self,
        session: Session,
        user: User,
        channel: Channel,
        video: Video,
    ) -> PipelineProcessingStats:
        if not self._should_process_video(video):
            return self._skip_disabled_short_stages(session, user.id, video.id)

        stages_processed = 0
        stages_succeeded = 0
        stages_failed = 0
        stages_skipped = 0
        fallbacks_sent = 0
        summary_attempted = False

        stages = self.create_stages_for_video(session, user.id, video.id)
        stage_map: dict[str, PipelineStage] = {s.stage: s for s in stages}

        self._attempt_transcript_stage(session, stage_map.get(STAGE_TRANSCRIPT), video)
        stages_processed += 1
        transcript_stage = stage_map.get(STAGE_TRANSCRIPT)
        if transcript_stage and transcript_stage.status == STATUS_COMPLETED:
            stages_succeeded += 1
        elif transcript_stage and transcript_stage.status == STATUS_FAILED:
            stages_failed += 1

        if self._can_proceed(transcript_stage):
            summary_attempted = self._attempt_summary_stage(
                session, stage_map.get(STAGE_SUMMARY), video
            )
            stages_processed += int(summary_attempted)
            summary_stage = stage_map.get(STAGE_SUMMARY)
            if summary_stage and summary_stage.status == STATUS_COMPLETED:
                stages_succeeded += 1
            elif summary_stage and summary_stage.status == STATUS_FAILED:
                stages_failed += 1
        elif transcript_stage and transcript_stage.status == STATUS_FAILED:
            self._skip_stage(session, stage_map.get(STAGE_SUMMARY))
            stages_skipped += 1

        if self._can_proceed(stage_map.get(STAGE_SUMMARY)):
            self._attempt_telegram_stage(session, stage_map.get(STAGE_TELEGRAM), channel, video)
            stages_processed += 1
            telegram_stage = stage_map.get(STAGE_TELEGRAM)
            if telegram_stage and telegram_stage.status == STATUS_COMPLETED:
                stages_succeeded += 1
            elif telegram_stage and telegram_stage.status == STATUS_FAILED:
                stages_failed += 1
        elif stage_map.get(STAGE_SUMMARY) and stage_map[STAGE_SUMMARY].status in {
            STATUS_FAILED,
            STATUS_SKIPPED,
        }:
            self._skip_stage(session, stage_map.get(STAGE_TELEGRAM))
            stages_skipped += 1

        session.flush()

        if self._should_trigger_fallback(stage_map):
            fallback_sent = self._attempt_fallback_telegram(
                session, user, channel, video, stage_map
            )
            if fallback_sent:
                fallbacks_sent += 1

        self._persist_summary_pause_state(session, user)

        return PipelineProcessingStats(
            stages_processed=stages_processed,
            stages_succeeded=stages_succeeded,
            stages_failed=stages_failed,
            stages_skipped=stages_skipped,
            fallbacks_sent=fallbacks_sent,
            summary_attempted=summary_attempted,
        )

    def skip_disabled_short_work(self, session: Session, user: User) -> None:
        """Terminally skip queued Short stages before any recovery or drain work."""
        self._skip_disabled_short_stages_for_user(session, user.id)

    def process_pending_stages(
        self,
        session: Session,
        user: User,
    ) -> PipelineProcessingStats:
        self._skip_disabled_short_stages_for_user(session, user.id)

        stages_processed = 0
        stages_succeeded = 0
        stages_failed = 0
        stages_skipped = 0
        fallbacks_sent = 0
        summary_attempted = False

        pending_rows = session.execute(
            select(PipelineStage, Video, Channel)
            .join(Video, PipelineStage.video_id == Video.id)
            .join(Channel, Video.channel_id == Channel.id)
            .where(
                PipelineStage.user_id == user.id,
                PipelineStage.status.in_([STATUS_PENDING, STATUS_PENDING_RETRY]),
            )
            .order_by(PipelineStage.id.asc())
        ).all()

        if not pending_rows:
            return PipelineProcessingStats(
                stages_processed=0,
                stages_succeeded=0,
                stages_failed=0,
                stages_skipped=0,
                fallbacks_sent=0,
            )

        fallback_rows = session.execute(
            select(PipelineStage, Video, Channel)
            .join(Video, PipelineStage.video_id == Video.id)
            .join(Channel, Video.channel_id == Channel.id)
            .where(
                PipelineStage.user_id == user.id,
                PipelineStage.stage == STAGE_FALLBACK_TELEGRAM,
                PipelineStage.status.in_([STATUS_PENDING, STATUS_PENDING_RETRY]),
            )
            .order_by(PipelineStage.id.asc())
        ).all()

        for ps, video, channel in fallback_rows:
            if self._attempt_single_fallback(session, user, channel, video, ps):
                fallbacks_sent += 1

        for ps, video, channel in pending_rows:
            if ps.stage == STAGE_FALLBACK_TELEGRAM:
                continue

            if ps.stage == STAGE_TRANSCRIPT:
                self._attempt_transcript_stage(session, ps, video)
                stages_processed += 1
                if ps.status == STATUS_COMPLETED:
                    stages_succeeded += 1
                elif ps.status == STATUS_FAILED:
                    stages_failed += 1

            elif ps.stage == STAGE_SUMMARY:
                transcript_stage = self._get_dependency_stage(
                    session, user.id, video.id, STAGE_TRANSCRIPT
                )
                if not self._can_proceed(transcript_stage):
                    self._skip_stage(session, ps)
                    stages_skipped += 1
                    continue

                attempted = self._attempt_summary_stage(session, ps, video)
                summary_attempted = summary_attempted or attempted
                stages_processed += int(attempted)
                if ps.status == STATUS_COMPLETED:
                    stages_succeeded += 1
                elif ps.status == STATUS_FAILED:
                    stages_failed += 1

            elif ps.stage == STAGE_TELEGRAM:
                summary_stage = self._get_dependency_stage(
                    session, user.id, video.id, STAGE_SUMMARY
                )
                if not self._can_proceed(summary_stage):
                    self._skip_stage(session, ps)
                    stages_skipped += 1
                    continue

                self._attempt_telegram_stage(session, ps, channel, video)
                stages_processed += 1
                if ps.status == STATUS_COMPLETED:
                    stages_succeeded += 1
                elif ps.status == STATUS_FAILED:
                    stages_failed += 1

        session.flush()

        video_ids_processed = set()
        for ps, _video, _channel in pending_rows:
            if ps.stage == STAGE_FALLBACK_TELEGRAM:
                continue
            video_ids_processed.add(ps.video_id)

        for video_id in video_ids_processed:
            all_stages = session.execute(
                select(PipelineStage).where(
                    PipelineStage.video_id == video_id,
                    PipelineStage.user_id == user.id,
                )
            ).scalars().all()

            stage_map = {s.stage: s for s in all_stages}
            if self._should_trigger_fallback(stage_map):
                vid = session.get(Video, video_id)
                if vid is None:
                    continue
                ch = session.get(Channel, vid.channel_id)
                if ch is None:
                    continue
                sent = self._attempt_fallback_telegram(
                    session, user, ch, vid, stage_map
                )
                if sent:
                    fallbacks_sent += 1

        self._persist_summary_pause_state(session, user)

        return PipelineProcessingStats(
            stages_processed=stages_processed,
            stages_succeeded=stages_succeeded,
            stages_failed=stages_failed,
            stages_skipped=stages_skipped,
            fallbacks_sent=fallbacks_sent,
            summary_attempted=summary_attempted,
        )

    def process_next_pending_video(
        self,
        session: Session,
        user: User,
    ) -> PipelineProcessingStats:
        """Advance one recoverable video at a time during normal polling."""
        self._skip_disabled_short_stages_for_user(session, user.id)
        row = session.execute(
            select(Video, Channel)
            .join(PipelineStage, PipelineStage.video_id == Video.id)
            .join(Channel, Video.channel_id == Channel.id)
            .where(
                PipelineStage.user_id == user.id,
                PipelineStage.status.in_([STATUS_PENDING, STATUS_PENDING_RETRY]),
            )
            .order_by(Video.published_at.asc().nullsfirst(), Video.id.asc())
            .limit(1)
        ).first()
        if row is None:
            return PipelineProcessingStats(0, 0, 0, 0, 0)
        video, channel = row
        return self.process_new_video_stages(session, user, channel, video)

    def drain_pending_videos(
        self,
        session: Session,
        user: User,
        *,
        pause_seconds: float,
        sleep: Callable[[float], None] = time.sleep,
    ) -> PipelineDrainStats:
        """Process queued videos oldest-first, committing each before the next pause."""
        self._skip_disabled_short_stages_for_user(session, user.id)
        total = PipelineProcessingStats(0, 0, 0, 0, 0)
        videos_processed = 0
        pause_seconds = max(0.0, pause_seconds)
        summary_attempted = False

        pending_video_ids = session.scalars(
            select(Video.id)
            .join(PipelineStage, PipelineStage.video_id == Video.id)
            .where(
                PipelineStage.user_id == user.id,
                PipelineStage.status.in_([STATUS_PENDING, STATUS_PENDING_RETRY]),
            )
            .group_by(Video.id, Video.published_at)
            .order_by(Video.published_at.asc().nullsfirst(), Video.id.asc())
        ).all()

        for index, video_id in enumerate(pending_video_ids):
            video = session.get(Video, video_id)
            if video is None:
                continue
            channel = session.get(Channel, video.channel_id)
            if channel is None:
                continue

            stats = self.process_new_video_stages(session, user, channel, video)
            session.commit()
            videos_processed += 1
            total = PipelineProcessingStats(
                stages_processed=total.stages_processed + stats.stages_processed,
                stages_succeeded=total.stages_succeeded + stats.stages_succeeded,
                stages_failed=total.stages_failed + stats.stages_failed,
                stages_skipped=total.stages_skipped + stats.stages_skipped,
                fallbacks_sent=total.fallbacks_sent + stats.fallbacks_sent,
                summary_attempted=total.summary_attempted or stats.summary_attempted,
            )
            summary_attempted = summary_attempted or stats.summary_attempted

            if pause_seconds and stats.summary_attempted and index < len(pending_video_ids) - 1:
                logger.info("Waiting %.0f seconds before the next pipeline video.", pause_seconds)
                sleep(pause_seconds)

        return PipelineDrainStats(
            videos_processed=videos_processed,
            stages_processed=total.stages_processed,
            stages_succeeded=total.stages_succeeded,
            stages_failed=total.stages_failed,
            stages_skipped=total.stages_skipped,
            fallbacks_sent=total.fallbacks_sent,
            summary_attempted=summary_attempted,
        )

    def process_pending_stages_with_throttling(
        self,
        session: Session,
        user: User,
    ) -> PipelineProcessingStats:
        if self.startup_batch_size <= 0:
            return self.process_pending_stages(session=session, user=user)

        total_stages_processed = 0
        total_stages_succeeded = 0
        total_stages_failed = 0
        total_stages_skipped = 0
        total_fallbacks_sent = 0

        pending_rows = session.execute(
            select(PipelineStage)
            .where(
                PipelineStage.user_id == user.id,
                PipelineStage.status.in_([STATUS_PENDING, STATUS_PENDING_RETRY]),
            )
            .order_by(PipelineStage.id.asc())
            .limit(self.startup_batch_size)
        ).scalars().all()

        if not pending_rows:
            return PipelineProcessingStats(
                stages_processed=0,
                stages_succeeded=0,
                stages_failed=0,
                stages_skipped=0,
                fallbacks_sent=0,
            )

        logger.info(
            "Processing pending pipeline stages: %d stage(s) in batch",
            len(pending_rows),
        )
        stats = self.process_pending_stages(session=session, user=user)
        session.commit()

        total_stages_processed += stats.stages_processed
        total_stages_succeeded += stats.stages_succeeded
        total_stages_failed += stats.stages_failed
        total_stages_skipped += stats.stages_skipped
        total_fallbacks_sent += stats.fallbacks_sent

        remaining = session.execute(
            select(PipelineStage)
            .where(
                PipelineStage.user_id == user.id,
                PipelineStage.status.in_([STATUS_PENDING, STATUS_PENDING_RETRY]),
            )
            .order_by(PipelineStage.id.asc())
        ).scalars().all()

        for i, _ in enumerate(remaining):
            if i > 0 and i % self.startup_batch_size == 0:
                logger.info(
                    "Throttling startup processing: %d remaining. Sleeping %ds.",
                    len(remaining) - i,
                    self.startup_batch_delay_seconds,
                )
                time.sleep(self.startup_batch_delay_seconds)

                batch = session.execute(
                    select(PipelineStage)
                    .where(
                        PipelineStage.user_id == user.id,
                        PipelineStage.status.in_([STATUS_PENDING, STATUS_PENDING_RETRY]),
                    )
                    .order_by(PipelineStage.id.asc())
                    .limit(self.startup_batch_size)
                ).scalars().all()

                if batch:
                    stats = self.process_pending_stages(session=session, user=user)
                    session.commit()
                    total_stages_processed += stats.stages_processed
                    total_stages_succeeded += stats.stages_succeeded
                    total_stages_failed += stats.stages_failed
                    total_stages_skipped += stats.stages_skipped
                    total_fallbacks_sent += stats.fallbacks_sent

        return PipelineProcessingStats(
            stages_processed=total_stages_processed,
            stages_succeeded=total_stages_succeeded,
            stages_failed=total_stages_failed,
            stages_skipped=total_stages_skipped,
            fallbacks_sent=total_fallbacks_sent,
        )

    def _attempt_transcript_stage(
        self,
        session: Session,
        stage: PipelineStage | None,
        video: Video,
    ) -> None:
        if stage is None:
            return
        if stage.status not in (STATUS_PENDING, STATUS_PENDING_RETRY):
            return
        if video.transcript is not None:
            stage.status = STATUS_COMPLETED
            return
        if self.transcript_service is None:
            stage.status = STATUS_FAILED
            stage.last_error = "Transcript service not available."
            return

        attempted_at = datetime.now(UTC)
        stage.attempt_count += 1
        stage.last_attempt_at = attempted_at

        try:
            transcript = self.transcript_service.fetch_transcript(video.youtube_video_id)
        except Exception as exc:
            transcript = None
            stage.last_error = str(exc)

        if transcript:
            video.transcript = transcript
            stage.status = STATUS_COMPLETED
            stage.last_error = None
            return

        if stage.last_error is None:
            stage.last_error = "Transcript not available for this video."

        if stage.attempt_count >= stage.max_attempts:
            stage.status = STATUS_FAILED
        else:
            stage.status = STATUS_PENDING_RETRY

    def _attempt_summary_stage(
        self,
        session: Session,
        stage: PipelineStage | None,
        video: Video,
    ) -> bool:
        if stage is None:
            return False
        if stage.status not in (STATUS_PENDING, STATUS_PENDING_RETRY):
            return False
        if self.summary_paused:
            return False
        if video.summary is not None:
            stage.status = STATUS_COMPLETED
            return False
        if self.summarization_service is None:
            stage.status = STATUS_FAILED
            stage.last_error = "Summarization service not available."
            return True
        if video.transcript is None:
            stage.status = STATUS_FAILED
            stage.last_error = "No transcript available to summarize."
            return True

        attempted_at = datetime.now(UTC)
        stage.attempt_count += 1
        stage.last_attempt_at = attempted_at

        try:
            # llama.cpp runs on the local homelab GPU. Keep every entrypoint to
            # the model serialized, including retries and incident recovery.
            with _SUMMARY_INFERENCE_LOCK:
                summary = self.summarization_service.summarize(video.transcript)
        except Exception as exc:
            summary = None
            stage.last_error = _compact_error(str(exc))
            self.summary_paused = True
            self.summary_pause_reason = stage.last_error
            self.summary_pause_video_id = video.id
            stage.status = STATUS_PENDING_RETRY
            return True

        if summary:
            video.summary = summary
            stage.status = STATUS_COMPLETED
            stage.last_error = None
            return True

        if stage.last_error is None:
            stage.last_error = "Summary generation failed."

        if stage.attempt_count >= stage.max_attempts:
            stage.status = STATUS_FAILED
        else:
            stage.status = STATUS_PENDING_RETRY
        return True

    def attempt_summary_recovery(self, session: Session, user: User) -> bool | None:
        """Attempt at most one real pending summary while the circuit is open."""
        if not self.summary_paused:
            return True

        recovery_query = (
            select(PipelineStage, Video)
            .join(Video, PipelineStage.video_id == Video.id)
            .where(
                PipelineStage.user_id == user.id,
                PipelineStage.stage == STAGE_SUMMARY,
                PipelineStage.status.in_([STATUS_PENDING, STATUS_PENDING_RETRY]),
                Video.transcript.is_not(None),
            )
        )
        if not self.shorts_processing_enabled:
            recovery_query = recovery_query.where(Video.is_short.is_not(True))
        row = session.execute(
            recovery_query.order_by(Video.published_at.asc().nullsfirst(), Video.id.asc()).limit(1)
        ).first()
        if row is None:
            return None

        stage, video = row
        self.summary_paused = False
        self.summary_pause_reason = None
        self.summary_pause_video_id = None
        self._attempt_summary_stage(session, stage, video)
        if stage.status != STATUS_COMPLETED:
            self.summary_paused = True
            self.summary_recovery_succeeded = False
            self._persist_summary_pause_state(session, user)
            return False

        self.summary_recovery_succeeded = True
        self._persist_summary_recovery_state(session, user)
        return True

    def _persist_summary_pause_state(self, session: Session, user: User) -> None:
        if not self.summary_paused:
            return

        state = session.scalar(
            select(SyncState).where(
                SyncState.user_id == user.id,
                SyncState.process_type == SUMMARIZATION_PROCESS,
            )
        )
        if state is None:
            state = SyncState(user_id=user.id, process_type=SUMMARIZATION_PROCESS)
            session.add(state)
            session.flush()

        metadata = dict(state.state_metadata or {})
        metadata["paused"] = True
        metadata["recovery_pending_alert"] = False
        metadata["last_error"] = self.summary_pause_reason or "Unknown summarization failure."
        if self.summary_pause_video_id is not None:
            metadata["failed_video_id"] = self.summary_pause_video_id
        metadata.setdefault("alert_sent", False)
        metadata.setdefault("incident_started_at", datetime.now(UTC).isoformat())
        state.last_error_at = datetime.now(UTC)
        state.last_error_message = metadata["last_error"]
        state.state_metadata = metadata
        session.flush()

    def _persist_summary_recovery_state(self, session: Session, user: User) -> None:
        state = session.scalar(
            select(SyncState).where(
                SyncState.user_id == user.id,
                SyncState.process_type == SUMMARIZATION_PROCESS,
            )
        )
        if state is None:
            state = SyncState(user_id=user.id, process_type=SUMMARIZATION_PROCESS)
            session.add(state)
            session.flush()

        metadata = dict(state.state_metadata or {})
        metadata["paused"] = False
        metadata["recovery_pending_alert"] = True
        metadata["recovered_at"] = datetime.now(UTC).isoformat()
        state.last_success_at = datetime.now(UTC)
        state.last_error_at = None
        state.last_error_message = None
        state.state_metadata = metadata
        session.flush()

    def _attempt_telegram_stage(
        self,
        session: Session,
        stage: PipelineStage | None,
        channel: Channel,
        video: Video,
    ) -> None:
        if stage is None:
            return
        if stage.status not in (STATUS_PENDING, STATUS_PENDING_RETRY):
            return
        if self.telegram_service is None:
            stage.status = STATUS_SKIPPED
            return
        if video.summary is None:
            stage.status = STATUS_FAILED
            stage.last_error = "No summary available for Telegram delivery."
            return

        attempted_at = datetime.now(UTC)
        stage.attempt_count += 1
        stage.last_attempt_at = attempted_at

        try:
            self.telegram_service.send_video_notification(
                TelegramNotificationPayload(
                    channel_title=channel.title,
                    video_title=video.title,
                    youtube_video_id=video.youtube_video_id,
                    summary=video.summary,
                    is_short=bool(video.is_short),
                )
            )
        except TelegramDeliveryAttemptError as exc:
            stage.last_error = exc.message
            if not exc.retryable:
                stage.status = STATUS_FAILED
                return
            if stage.attempt_count >= stage.max_attempts:
                stage.status = STATUS_FAILED
            else:
                stage.status = STATUS_PENDING_RETRY
            return
        except Exception as exc:
            stage.last_error = str(exc)
            if stage.attempt_count >= stage.max_attempts:
                stage.status = STATUS_FAILED
            else:
                stage.status = STATUS_PENDING_RETRY
            return

        stage.status = STATUS_COMPLETED
        stage.last_error = None

    def _attempt_fallback_telegram(
        self,
        session: Session,
        user: User,
        channel: Channel,
        video: Video,
        stage_map: dict[str, PipelineStage],
    ) -> bool:
        if self.telegram_service is None:
            return False

        fallback_stage = self._get_or_create_fallback_stage(
            session, user.id, video.id
        )

        if fallback_stage.status == STATUS_COMPLETED:
            return False
        if fallback_stage.status not in (STATUS_PENDING, STATUS_PENDING_RETRY):
            return False

        return self._attempt_single_fallback(
            session, user, channel, video, fallback_stage, stage_map
        )

    def _attempt_single_fallback(
        self,
        session: Session,
        user: User,
        channel: Channel,
        video: Video,
        fallback_stage: PipelineStage,
        stage_map: dict[str, PipelineStage] | None = None,
    ) -> bool:
        if self.telegram_service is None:
            return False
        if fallback_stage.status not in (STATUS_PENDING, STATUS_PENDING_RETRY):
            return False

        if stage_map is None:
            all_stages = session.execute(
                select(PipelineStage).where(
                    PipelineStage.video_id == video.id,
                    PipelineStage.user_id == user.id,
                )
            ).scalars().all()
            stage_map = {s.stage: s for s in all_stages}

        reason = self._build_fallback_reason(stage_map)
        if reason is None:
            fallback_stage.status = STATUS_COMPLETED
            return False

        title = video.title or video.youtube_video_id
        channel_title = channel.title or "Canal desconocido"
        video_url = f"https://www.youtube.com/watch?v={video.youtube_video_id}"
        message = FALLBACK_MESSAGE_TEMPLATE.format(
            title=title,
            channel=channel_title,
            url=video_url,
            reason=reason,
        )

        attempted_at = datetime.now(UTC)
        fallback_stage.attempt_count += 1
        fallback_stage.last_attempt_at = attempted_at

        try:
            self.telegram_service.send_message(message)
        except TelegramDeliveryAttemptError as exc:
            fallback_stage.last_error = exc.message
            if not exc.retryable:
                fallback_stage.status = STATUS_FAILED
            else:
                fallback_stage.status = STATUS_PENDING_RETRY
            return False
        except Exception as exc:
            fallback_stage.last_error = str(exc)
            fallback_stage.status = STATUS_PENDING_RETRY
            return False

        fallback_stage.status = STATUS_COMPLETED
        fallback_stage.last_error = None
        return True

    def _get_or_create_fallback_stage(
        self,
        session: Session,
        user_id: int,
        video_id: int,
    ) -> PipelineStage:
        fallback = session.scalar(
            select(PipelineStage).where(
                PipelineStage.video_id == video_id,
                PipelineStage.user_id == user_id,
                PipelineStage.stage == STAGE_FALLBACK_TELEGRAM,
            )
        )
        if fallback is None:
            fallback = PipelineStage(
                video_id=video_id,
                user_id=user_id,
                stage=STAGE_FALLBACK_TELEGRAM,
                status=STATUS_PENDING,
            )
            session.add(fallback)
            session.flush()
        return fallback

    def _get_dependency_stage(
        self,
        session: Session,
        user_id: int,
        video_id: int,
        stage_name: str,
    ) -> PipelineStage | None:
        return session.scalar(
            select(PipelineStage).where(
                PipelineStage.video_id == video_id,
                PipelineStage.user_id == user_id,
                PipelineStage.stage == stage_name,
            )
        )

    @staticmethod
    def _can_proceed(stage: PipelineStage | None) -> bool:
        if stage is None:
            return False
        return stage.status == STATUS_COMPLETED

    @staticmethod
    def _skip_stage(session: Session, stage: PipelineStage | None) -> None:
        if stage is None:
            return
        if stage.status in (STATUS_COMPLETED, STATUS_FAILED):
            return
        stage.status = STATUS_SKIPPED
        stage.last_error = None

    def _should_process_video(self, video: Video) -> bool:
        return self.shorts_processing_enabled or video.is_short is not True

    def _skip_disabled_short_stages_for_user(self, session: Session, user_id: int) -> None:
        if self.shorts_processing_enabled:
            return

        rows = session.execute(
            select(PipelineStage, Video)
            .join(Video, PipelineStage.video_id == Video.id)
            .where(
                PipelineStage.user_id == user_id,
                PipelineStage.status.in_([STATUS_PENDING, STATUS_PENDING_RETRY]),
                Video.is_short.is_(True),
            )
        ).all()
        for stage, _video in rows:
            self._skip_stage(session, stage)
            stage.last_error = SHORT_PROCESSING_DISABLED_ERROR
        session.flush()

    def _skip_disabled_short_stages(
        self,
        session: Session,
        user_id: int,
        video_id: int,
    ) -> PipelineProcessingStats:
        if self.shorts_processing_enabled:
            return PipelineProcessingStats(0, 0, 0, 0, 0)

        stages = session.scalars(
            select(PipelineStage).where(
                PipelineStage.user_id == user_id,
                PipelineStage.video_id == video_id,
                PipelineStage.status.in_([STATUS_PENDING, STATUS_PENDING_RETRY]),
            )
        ).all()
        for stage in stages:
            self._skip_stage(session, stage)
            stage.last_error = SHORT_PROCESSING_DISABLED_ERROR
        session.flush()
        return PipelineProcessingStats(0, 0, 0, len(stages), 0)

    def _should_trigger_fallback(self, stage_map: dict[str, PipelineStage]) -> bool:
        for stage_name in (STAGE_TRANSCRIPT, STAGE_SUMMARY, STAGE_TELEGRAM):
            stage = stage_map.get(stage_name)
            if stage is None:
                continue
            if stage.status == STATUS_FAILED:
                return True
        return False

    def _build_fallback_reason(
        self,
        stage_map: dict[str, PipelineStage],
    ) -> str | None:
        for stage_name in (STAGE_TRANSCRIPT, STAGE_SUMMARY, STAGE_TELEGRAM):
            stage = stage_map.get(stage_name)
            if stage is None:
                continue
            if stage.status != STATUS_FAILED:
                continue

            if stage_name == STAGE_TRANSCRIPT:
                reason = FALLBACK_REASON_TRANSCRIPT.format(max_attempts=stage.max_attempts)
                return f"{reason} Causa: {_compact_error(stage.last_error or reason)}"
            if stage_name == STAGE_SUMMARY:
                reason = FALLBACK_REASON_SUMMARY.format(max_attempts=stage.max_attempts)
                return f"{reason} Causa: {_compact_error(stage.last_error or reason)}"
            if stage_name == STAGE_TELEGRAM:
                reason = FALLBACK_REASON_TELEGRAM.format(max_attempts=stage.max_attempts)
                return f"{reason} Causa: {_compact_error(stage.last_error or reason)}"

        return None
