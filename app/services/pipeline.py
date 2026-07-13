from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.channel import Channel
from app.models.pipeline_stage import PipelineStage
from app.models.user import User
from app.models.video import Video
from app.services.summarization import SummarizationService
from app.services.telegram import TelegramDeliveryAttemptError, TelegramDeliveryService, TelegramNotificationPayload
from app.services.transcript import TranscriptService

logger = logging.getLogger(__name__)

STAGE_TRANSCRIPT = "transcript"
STAGE_SUMMARY = "summary"
STAGE_TELEGRAM = "telegram"
STAGE_FALLBACK_TELEGRAM = "fallback_telegram"

STATUS_PENDING = "pending"
STATUS_PENDING_RETRY = "pending_retry"
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"
STATUS_SKIPPED = "skipped"

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


@dataclass
class PipelineProcessingStats:
    stages_processed: int
    stages_succeeded: int
    stages_failed: int
    stages_skipped: int
    fallbacks_sent: int


class PipelineService:
    def __init__(
        self,
        transcript_service: TranscriptService | None = None,
        summarization_service: SummarizationService | None = None,
        telegram_service: TelegramDeliveryService | None = None,
        startup_batch_size: int = 0,
        startup_batch_delay_seconds: float = 30.0,
    ):
        self.transcript_service = transcript_service
        self.summarization_service = summarization_service
        self.telegram_service = telegram_service
        self.startup_batch_size = max(0, startup_batch_size)
        self.startup_batch_delay_seconds = max(0, startup_batch_delay_seconds)

    def create_stages_for_video(self, session: Session, user_id: int, video_id: int) -> list[PipelineStage]:
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
        stages_processed = 0
        stages_succeeded = 0
        stages_failed = 0
        stages_skipped = 0
        fallbacks_sent = 0

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
            self._attempt_summary_stage(session, stage_map.get(STAGE_SUMMARY), video)
            stages_processed += 1
            summary_stage = stage_map.get(STAGE_SUMMARY)
            if summary_stage and summary_stage.status == STATUS_COMPLETED:
                stages_succeeded += 1
            elif summary_stage and summary_stage.status == STATUS_FAILED:
                stages_failed += 1
        else:
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
        else:
            self._skip_stage(session, stage_map.get(STAGE_TELEGRAM))
            stages_skipped += 1

        session.flush()

        if self._should_trigger_fallback(stage_map):
            fallback_sent = self._attempt_fallback_telegram(
                session, user, channel, video, stage_map
            )
            if fallback_sent:
                fallbacks_sent += 1

        return PipelineProcessingStats(
            stages_processed=stages_processed,
            stages_succeeded=stages_succeeded,
            stages_failed=stages_failed,
            stages_skipped=stages_skipped,
            fallbacks_sent=fallbacks_sent,
        )

    def process_pending_stages(
        self,
        session: Session,
        user: User,
    ) -> PipelineProcessingStats:
        stages_processed = 0
        stages_succeeded = 0
        stages_failed = 0
        stages_skipped = 0
        fallbacks_sent = 0

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

                self._attempt_summary_stage(session, ps, video)
                stages_processed += 1
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

        return PipelineProcessingStats(
            stages_processed=stages_processed,
            stages_succeeded=stages_succeeded,
            stages_failed=stages_failed,
            stages_skipped=stages_skipped,
            fallbacks_sent=fallbacks_sent,
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
    ) -> None:
        if stage is None:
            return
        if stage.status not in (STATUS_PENDING, STATUS_PENDING_RETRY):
            return
        if video.summary is not None:
            stage.status = STATUS_COMPLETED
            return
        if self.summarization_service is None:
            stage.status = STATUS_FAILED
            stage.last_error = "Summarization service not available."
            return
        if video.transcript is None:
            stage.status = STATUS_FAILED
            stage.last_error = "No transcript available to summarize."
            return

        attempted_at = datetime.now(UTC)
        stage.attempt_count += 1
        stage.last_attempt_at = attempted_at

        try:
            summary = self.summarization_service.summarize(video.transcript)
        except Exception as exc:
            summary = None
            stage.last_error = str(exc)

        if summary:
            video.summary = summary
            stage.status = STATUS_COMPLETED
            stage.last_error = None
            return

        if stage.last_error is None:
            stage.last_error = "Summary generation failed."

        if stage.attempt_count >= stage.max_attempts:
            stage.status = STATUS_FAILED
        else:
            stage.status = STATUS_PENDING_RETRY

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
                return FALLBACK_REASON_TRANSCRIPT.format(max_attempts=stage.max_attempts)
            if stage_name == STAGE_SUMMARY:
                return FALLBACK_REASON_SUMMARY.format(max_attempts=stage.max_attempts)
            if stage_name == STAGE_TELEGRAM:
                return FALLBACK_REASON_TELEGRAM.format(max_attempts=stage.max_attempts)

        return None
