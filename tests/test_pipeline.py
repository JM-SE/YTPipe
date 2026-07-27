from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import select

from app.models.channel import Channel
from app.models.notification_delivery import NotificationDelivery
from app.models.pipeline_stage import PipelineStage
from app.models.user import User
from app.models.video import Video
from app.services.pipeline import (
    FALLBACK_REASON_SUMMARY,
    FALLBACK_REASON_TELEGRAM,
    FALLBACK_REASON_TRANSCRIPT,
    STAGE_FALLBACK_TELEGRAM,
    STAGE_SUMMARY,
    STAGE_TELEGRAM,
    STAGE_TRANSCRIPT,
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_PENDING,
    STATUS_PENDING_RETRY,
    STATUS_SKIPPED,
    PipelineService,
)
from app.services.telegram import TelegramDeliveryAttemptError, TelegramDeliveryService, TelegramNotificationPayload
from app.services.transcript import TranscriptService
from app.services.summarization import SummarizationRequestError


@pytest.fixture
def user(db_session) -> User:
    u = User(email="test@example.com")
    db_session.add(u)
    db_session.commit()
    return u


@pytest.fixture
def channel(db_session) -> Channel:
    c = Channel(youtube_channel_id="UC-TEST", title="Test Channel", uploads_playlist_id="UPL-TEST")
    db_session.add(c)
    db_session.commit()
    return c


@pytest.fixture
def video(db_session, channel) -> Video:
    v = Video(
        youtube_video_id="video-1",
        channel_id=channel.id,
        title="Test Video",
        published_at=datetime.now(UTC),
    )
    db_session.add(v)
    db_session.commit()
    return v


@pytest.fixture
def transcript_service() -> TranscriptService:
    return MagicMock(spec=TranscriptService)


@pytest.fixture
def summarization_service() -> MagicMock:
    return MagicMock()


@pytest.fixture
def telegram_service() -> MagicMock:
    return MagicMock(spec=TelegramDeliveryService)


def make_pipeline_service(transcript_svc=None, summarization_svc=None, telegram_svc=None):
    return PipelineService(
        transcript_service=transcript_svc,
        summarization_service=summarization_svc,
        telegram_service=telegram_svc,
    )


class TestPipelineStageCreation:
    def test_create_stages_for_new_video(self, db_session, user, video):
        svc = make_pipeline_service()
        stages = svc.create_stages_for_video(db_session, user.id, video.id)

        assert len(stages) == 3
        stage_map = {s.stage: s for s in stages}
        assert set(stage_map.keys()) == {STAGE_TRANSCRIPT, STAGE_SUMMARY, STAGE_TELEGRAM}
        for s in stages:
            assert s.status == STATUS_PENDING
            assert s.attempt_count == 0
            assert s.max_attempts == 3

    def test_create_stages_idempotent(self, db_session, user, video):
        svc = make_pipeline_service()
        first = svc.create_stages_for_video(db_session, user.id, video.id)

        one = first[0]
        one.status = STATUS_COMPLETED
        db_session.commit()

        second = svc.create_stages_for_video(db_session, user.id, video.id)
        assert len(second) == 3
        assert second[0].id == one.id
        assert second[0].status == STATUS_COMPLETED


class TestTranscriptStage:
    def test_success_on_first_attempt(self, db_session, user, channel, video, transcript_service, telegram_service):
        transcript_service.fetch_transcript.return_value = "fake transcript text"
        mock_sum = MagicMock()
        mock_sum.summarize.return_value = "fake summary"
        telegram_service.send_video_notification = MagicMock()

        svc = make_pipeline_service(
            transcript_svc=transcript_service,
            summarization_svc=mock_sum,
            telegram_svc=telegram_service,
        )

        stages = svc.create_stages_for_video(db_session, user.id, video.id)
        stats = svc.process_new_video_stages(db_session, user, channel, video)

        db_session.refresh(video)
        assert video.transcript == "fake transcript text"
        assert stats.stages_succeeded == 3

    def test_failure_then_pending_retry(self, db_session, user, channel, video, transcript_service):
        transcript_service.fetch_transcript.return_value = None
        svc = make_pipeline_service(transcript_svc=transcript_service)

        stages = svc.create_stages_for_video(db_session, user.id, video.id)
        stats = svc.process_new_video_stages(db_session, user, channel, video)

        transcript_stage = db_session.scalar(
            select(PipelineStage).where(
                PipelineStage.video_id == video.id,
                PipelineStage.user_id == user.id,
                PipelineStage.stage == STAGE_TRANSCRIPT,
            )
        )
        assert transcript_stage.status == STATUS_PENDING_RETRY
        assert transcript_stage.attempt_count == 1
        assert stats.stages_failed == 0

    def test_failure_all_attempts_exhausted(self, db_session, user, channel, video, transcript_service):
        transcript_service.fetch_transcript.return_value = None
        svc = make_pipeline_service(transcript_svc=transcript_service)

        stages = svc.create_stages_for_video(db_session, user.id, video.id)
        transcript_stage = [s for s in stages if s.stage == STAGE_TRANSCRIPT][0]
        transcript_stage.attempt_count = 2
        transcript_stage.status = STATUS_PENDING_RETRY
        db_session.commit()

        stats = svc.process_new_video_stages(db_session, user, channel, video)

        db_session.refresh(transcript_stage)
        assert transcript_stage.status == STATUS_FAILED
        assert transcript_stage.attempt_count == 3
        assert stats.stages_failed == 1


class TestDependencyChain:
    def test_summary_skipped_when_transcript_fails(self, db_session, user, channel, video, transcript_service):
        transcript_service.fetch_transcript.return_value = None
        svc = make_pipeline_service(transcript_svc=transcript_service)

        stages = svc.create_stages_for_video(db_session, user.id, video.id)
        transcript_stage = [s for s in stages if s.stage == STAGE_TRANSCRIPT][0]
        transcript_stage.attempt_count = 2
        transcript_stage.status = STATUS_PENDING_RETRY
        db_session.commit()

        svc.process_new_video_stages(db_session, user, channel, video)

        summary_stage = db_session.scalar(
            select(PipelineStage).where(
                PipelineStage.video_id == video.id,
                PipelineStage.user_id == user.id,
                PipelineStage.stage == STAGE_SUMMARY,
            )
        )
        assert summary_stage.status == STATUS_SKIPPED

    def test_telegram_skipped_when_summary_fails(self, db_session, user, channel, video, transcript_service):
        transcript_service.fetch_transcript.return_value = "some transcript"
        mock_sum = MagicMock()
        mock_sum.summarize.return_value = None

        svc = make_pipeline_service(
            transcript_svc=transcript_service,
            summarization_svc=mock_sum,
        )

        stages = svc.create_stages_for_video(db_session, user.id, video.id)
        summary_stage = [s for s in stages if s.stage == STAGE_SUMMARY][0]
        summary_stage.attempt_count = 2
        summary_stage.status = STATUS_PENDING_RETRY
        db_session.commit()

        svc.process_new_video_stages(db_session, user, channel, video)

        telegram_stage = db_session.scalar(
            select(PipelineStage).where(
                PipelineStage.video_id == video.id,
                PipelineStage.user_id == user.id,
                PipelineStage.stage == STAGE_TELEGRAM,
            )
        )
        assert telegram_stage.status == STATUS_SKIPPED

    def test_summary_infrastructure_failure_pauses_later_summaries(
        self, db_session, user, channel, video, transcript_service, telegram_service
    ):
        transcript_service.fetch_transcript.return_value = "some transcript"
        summarization_service = MagicMock()
        summarization_service.summarize.side_effect = SummarizationRequestError(
            "Summarization server returned HTTP 500: Vulkan device lost."
        )

        second_video = Video(
            youtube_video_id="video-2",
            channel_id=channel.id,
            title="Second Video",
            published_at=datetime.now(UTC),
        )
        db_session.add(second_video)
        db_session.commit()

        svc = make_pipeline_service(
            transcript_svc=transcript_service,
            summarization_svc=summarization_service,
            telegram_svc=telegram_service,
        )
        svc.create_stages_for_video(db_session, user.id, video.id)
        svc.create_stages_for_video(db_session, user.id, second_video.id)

        svc.process_new_video_stages(db_session, user, channel, video)
        svc.process_new_video_stages(db_session, user, channel, second_video)

        first_summary = db_session.scalar(
            select(PipelineStage).where(
                PipelineStage.video_id == video.id,
                PipelineStage.user_id == user.id,
                PipelineStage.stage == STAGE_SUMMARY,
            )
        )
        second_summary = db_session.scalar(
            select(PipelineStage).where(
                PipelineStage.video_id == second_video.id,
                PipelineStage.user_id == user.id,
                PipelineStage.stage == STAGE_SUMMARY,
            )
        )
        second_telegram = db_session.scalar(
            select(PipelineStage).where(
                PipelineStage.video_id == second_video.id,
                PipelineStage.user_id == user.id,
                PipelineStage.stage == STAGE_TELEGRAM,
            )
        )

        assert svc.summary_paused is True
        assert svc.summary_pause_reason == "Summarization server returned HTTP 500: Vulkan device lost."
        assert first_summary.status == STATUS_PENDING_RETRY
        assert second_summary.status == STATUS_PENDING
        assert second_telegram.status == STATUS_PENDING
        assert summarization_service.summarize.call_count == 1
        assert transcript_service.fetch_transcript.call_count == 2

    def test_summary_recovery_closes_circuit(self, db_session, user, channel, video, telegram_service):
        video.transcript = "existing transcript"
        db_session.commit()
        summarization_service = MagicMock()
        summarization_service.summarize.return_value = "recovered summary"
        svc = make_pipeline_service(
            transcript_svc=MagicMock(),
            summarization_svc=summarization_service,
            telegram_svc=telegram_service,
        )
        svc.create_stages_for_video(db_session, user.id, video.id)
        svc.summary_paused = True

        assert svc.attempt_summary_recovery(db_session, user) is True
        assert svc.summary_paused is False
        assert svc.summary_recovery_succeeded is True

        summary_stage = db_session.scalar(
            select(PipelineStage).where(
                PipelineStage.video_id == video.id,
                PipelineStage.user_id == user.id,
                PipelineStage.stage == STAGE_SUMMARY,
            )
        )
        assert summary_stage.status == STATUS_COMPLETED
        assert video.summary == "recovered summary"

    def test_telegram_skipped_when_transcript_fails(self, db_session, user, channel, video, transcript_service):
        transcript_service.fetch_transcript.return_value = None
        svc = make_pipeline_service(transcript_svc=transcript_service)

        stages = svc.create_stages_for_video(db_session, user.id, video.id)
        transcript_stage = [s for s in stages if s.stage == STAGE_TRANSCRIPT][0]
        transcript_stage.attempt_count = 2
        transcript_stage.status = STATUS_PENDING_RETRY
        db_session.commit()

        svc.process_new_video_stages(db_session, user, channel, video)

        telegram_stage = db_session.scalar(
            select(PipelineStage).where(
                PipelineStage.video_id == video.id,
                PipelineStage.user_id == user.id,
                PipelineStage.stage == STAGE_TELEGRAM,
            )
        )
        assert telegram_stage.status == STATUS_SKIPPED


class TestTelegramStage:
    def test_telegram_requires_summary(self, db_session, user, channel, video, transcript_service, telegram_service):
        transcript_service.fetch_transcript.return_value = "some transcript"
        mock_sum = MagicMock()
        mock_sum.summarize.return_value = "some summary"

        svc = make_pipeline_service(
            transcript_svc=transcript_service,
            summarization_svc=mock_sum,
            telegram_svc=telegram_service,
        )

        svc.create_stages_for_video(db_session, user.id, video.id)
        stats = svc.process_new_video_stages(db_session, user, channel, video)

        telegram_service.send_video_notification.assert_called_once()
        assert stats.stages_succeeded == 3

    def test_telegram_failure_retryable(self, db_session, user, channel, video,
                                         transcript_service, telegram_service):
        transcript_service.fetch_transcript.return_value = "some transcript"
        mock_sum = MagicMock()
        mock_sum.summarize.return_value = "some summary"
        telegram_service.send_video_notification.side_effect = TelegramDeliveryAttemptError(
            "timeout", retryable=True
        )

        svc = make_pipeline_service(
            transcript_svc=transcript_service,
            summarization_svc=mock_sum,
            telegram_svc=telegram_service,
        )

        svc.create_stages_for_video(db_session, user.id, video.id)
        svc.process_new_video_stages(db_session, user, channel, video)

        telegram_stage = db_session.scalar(
            select(PipelineStage).where(
                PipelineStage.video_id == video.id,
                PipelineStage.user_id == user.id,
                PipelineStage.stage == STAGE_TELEGRAM,
            )
        )
        assert telegram_stage.status == STATUS_PENDING_RETRY

    def test_telegram_failure_non_retryable(self, db_session, user, channel, video,
                                             transcript_service, telegram_service):
        transcript_service.fetch_transcript.return_value = "some transcript"
        mock_sum = MagicMock()
        mock_sum.summarize.return_value = "some summary"
        telegram_service.send_video_notification.side_effect = TelegramDeliveryAttemptError(
            "forbidden", retryable=False
        )

        svc = make_pipeline_service(
            transcript_svc=transcript_service,
            summarization_svc=mock_sum,
            telegram_svc=telegram_service,
        )

        svc.create_stages_for_video(db_session, user.id, video.id)
        svc.process_new_video_stages(db_session, user, channel, video)

        telegram_stage = db_session.scalar(
            select(PipelineStage).where(
                PipelineStage.video_id == video.id,
                PipelineStage.user_id == user.id,
                PipelineStage.stage == STAGE_TELEGRAM,
            )
        )
        assert telegram_stage.status == STATUS_FAILED

    def test_telegram_exhausted_retries(self, db_session, user, channel, video,
                                          transcript_service, telegram_service):
        transcript_service.fetch_transcript.return_value = "some transcript"
        mock_sum = MagicMock()
        mock_sum.summarize.return_value = "some summary"
        telegram_service.send_video_notification.side_effect = TelegramDeliveryAttemptError(
            "timeout", retryable=True
        )

        svc = make_pipeline_service(
            transcript_svc=transcript_service,
            summarization_svc=mock_sum,
            telegram_svc=telegram_service,
        )

        stages = svc.create_stages_for_video(db_session, user.id, video.id)
        telegram_stage = [s for s in stages if s.stage == STAGE_TELEGRAM][0]
        telegram_stage.attempt_count = 2
        telegram_stage.status = STATUS_PENDING_RETRY
        db_session.commit()

        svc.process_new_video_stages(db_session, user, channel, video)

        db_session.refresh(telegram_stage)
        assert telegram_stage.status == STATUS_FAILED
        assert telegram_stage.attempt_count == 3


class TestFallbackTelegram:
    def test_fallback_triggered_when_transcript_fails(self, db_session, user, channel, video,
                                                        transcript_service, telegram_service):
        transcript_service.fetch_transcript.return_value = None
        telegram_service.send_message = MagicMock()
        telegram_service.send_video_notification = MagicMock()

        svc = make_pipeline_service(
            transcript_svc=transcript_service,
            telegram_svc=telegram_service,
        )

        stages = svc.create_stages_for_video(db_session, user.id, video.id)
        transcript_stage = [s for s in stages if s.stage == STAGE_TRANSCRIPT][0]
        transcript_stage.attempt_count = 2
        transcript_stage.status = STATUS_PENDING_RETRY
        db_session.commit()

        stats = svc.process_new_video_stages(db_session, user, channel, video)

        assert stats.fallbacks_sent == 1

        fallback_stage = db_session.scalar(
            select(PipelineStage).where(
                PipelineStage.video_id == video.id,
                PipelineStage.user_id == user.id,
                PipelineStage.stage == STAGE_FALLBACK_TELEGRAM,
            )
        )
        assert fallback_stage is not None
        assert fallback_stage.status == STATUS_COMPLETED

        call = telegram_service.send_message.call_args
        assert call is not None
        message_text = call[0][0]
        assert FALLBACK_REASON_TRANSCRIPT.format(max_attempts=3) in message_text

    def test_fallback_retry_on_failure(self, db_session, user, channel, video,
                                         transcript_service, telegram_service):
        transcript_service.fetch_transcript.return_value = None
        telegram_service.send_message.side_effect = TelegramDeliveryAttemptError(
            "timeout", retryable=True
        )

        svc = make_pipeline_service(
            transcript_svc=transcript_service,
            telegram_svc=telegram_service,
        )

        stages = svc.create_stages_for_video(db_session, user.id, video.id)
        transcript_stage = [s for s in stages if s.stage == STAGE_TRANSCRIPT][0]
        transcript_stage.attempt_count = 2
        transcript_stage.status = STATUS_PENDING_RETRY
        db_session.commit()

        stats = svc.process_new_video_stages(db_session, user, channel, video)

        assert stats.fallbacks_sent == 0

        fallback_stage = db_session.scalar(
            select(PipelineStage).where(
                PipelineStage.video_id == video.id,
                PipelineStage.user_id == user.id,
                PipelineStage.stage == STAGE_FALLBACK_TELEGRAM,
            )
        )
        assert fallback_stage.status == STATUS_PENDING_RETRY

    def test_no_fallback_when_all_succeed(self, db_session, user, channel, video,
                                            transcript_service, telegram_service):
        transcript_service.fetch_transcript.return_value = "some transcript"
        mock_sum = MagicMock()
        mock_sum.summarize.return_value = "some summary"
        telegram_service.send_message = MagicMock()
        telegram_service.send_video_notification = MagicMock()

        svc = make_pipeline_service(
            transcript_svc=transcript_service,
            summarization_svc=mock_sum,
            telegram_svc=telegram_service,
        )

        svc.create_stages_for_video(db_session, user.id, video.id)
        stats = svc.process_new_video_stages(db_session, user, channel, video)

        assert stats.fallbacks_sent == 0

        fallback_stage = db_session.scalar(
            select(PipelineStage).where(
                PipelineStage.video_id == video.id,
                PipelineStage.user_id == user.id,
                PipelineStage.stage == STAGE_FALLBACK_TELEGRAM,
            )
        )
        assert fallback_stage is None

    def test_fallback_reason_summary(self, db_session, user, channel, video,
                                       transcript_service, summarization_service, telegram_service):
        transcript_service.fetch_transcript.return_value = "some transcript"
        summarization_service.summarize.return_value = None
        telegram_service.send_message = MagicMock()

        svc = make_pipeline_service(
            transcript_svc=transcript_service,
            summarization_svc=summarization_service,
            telegram_svc=telegram_service,
        )

        stages = svc.create_stages_for_video(db_session, user.id, video.id)
        summary_stage = [s for s in stages if s.stage == STAGE_SUMMARY][0]
        summary_stage.attempt_count = 2
        summary_stage.status = STATUS_PENDING_RETRY
        db_session.commit()

        stats = svc.process_new_video_stages(db_session, user, channel, video)

        assert stats.fallbacks_sent == 1
        call = telegram_service.send_message.call_args
        assert FALLBACK_REASON_SUMMARY.format(max_attempts=3) in call[0][0]
        assert "Causa: Summary generation failed." in call[0][0]

    def test_fallback_reason_telegram(self, db_session, user, channel, video,
                                        transcript_service, telegram_service):
        transcript_service.fetch_transcript.return_value = "some transcript"
        mock_sum = MagicMock()
        mock_sum.summarize.return_value = "some summary"
        telegram_service.send_video_notification.side_effect = TelegramDeliveryAttemptError(
            "timeout", retryable=True
        )
        telegram_service.send_message = MagicMock()

        svc = make_pipeline_service(
            transcript_svc=transcript_service,
            summarization_svc=mock_sum,
            telegram_svc=telegram_service,
        )

        stages = svc.create_stages_for_video(db_session, user.id, video.id)
        telegram_stage = [s for s in stages if s.stage == STAGE_TELEGRAM][0]
        telegram_stage.attempt_count = 2
        telegram_stage.status = STATUS_PENDING_RETRY
        db_session.commit()

        stats = svc.process_new_video_stages(db_session, user, channel, video)

        assert stats.fallbacks_sent == 1
        call = telegram_service.send_message.call_args
        assert FALLBACK_REASON_TELEGRAM.format(max_attempts=3) in call[0][0]


class TestProcessPendingStages:
    def test_retries_pending_retry_stages(self, db_session, user, channel, video,
                                            transcript_service, telegram_service):
        transcript_service.fetch_transcript.return_value = "some transcript"
        mock_sum = MagicMock()
        mock_sum.summarize.return_value = "some summary"
        telegram_service.send_video_notification.side_effect = TelegramDeliveryAttemptError(
            "timeout", retryable=True
        )
        telegram_service.send_message = MagicMock()

        svc = make_pipeline_service(
            transcript_svc=transcript_service,
            summarization_svc=mock_sum,
            telegram_svc=telegram_service,
        )

        stages = svc.create_stages_for_video(db_session, user.id, video.id)
        transcript_pending = [s for s in stages if s.stage == STAGE_TRANSCRIPT][0]
        transcript_pending.status = STATUS_PENDING_RETRY
        db_session.commit()

        telegram_service.send_video_notification.side_effect = None
        stats = svc.process_pending_stages(db_session, user)

        assert stats.stages_processed >= 1
        db_session.refresh(video)
        assert video.transcript == "some transcript"


class TestProcessPendingStagesWithThrottling:
    def test_no_batch_size_processes_all(self, db_session, user, channel, video, transcript_service):
        transcript_service.fetch_transcript.return_value = "some transcript"

        svc = PipelineService(
            transcript_service=transcript_service,
            startup_batch_size=0,
            startup_batch_delay_seconds=0,
        )

        stages = svc.create_stages_for_video(db_session, user.id, video.id)
        transcript_pending = [s for s in stages if s.stage == STAGE_TRANSCRIPT][0]
        transcript_pending.status = STATUS_PENDING_RETRY
        db_session.commit()

        stats = svc.process_pending_stages_with_throttling(db_session, user)

        assert stats.stages_processed >= 1
        db_session.refresh(video)
        assert video.transcript == "some transcript"


class TestSequentialVideoDrain:
    def test_retryable_transcript_keeps_downstream_stages_pending(
        self, db_session, user, channel, video, transcript_service, telegram_service
    ):
        transcript_service.fetch_transcript.side_effect = [None, "recovered transcript"]
        summarization_service = MagicMock()
        summarization_service.summarize.return_value = "summary"
        telegram_service.send_video_notification = MagicMock()
        service = make_pipeline_service(transcript_service, summarization_service, telegram_service)

        service.process_new_video_stages(db_session, user, channel, video)
        stages = {stage.stage: stage for stage in db_session.scalars(select(PipelineStage)).all()}
        assert stages[STAGE_TRANSCRIPT].status == STATUS_PENDING_RETRY
        assert stages[STAGE_SUMMARY].status == STATUS_PENDING
        assert stages[STAGE_TELEGRAM].status == STATUS_PENDING

        service.process_new_video_stages(db_session, user, channel, video)
        assert stages[STAGE_TRANSCRIPT].status == STATUS_COMPLETED
        assert stages[STAGE_SUMMARY].status == STATUS_COMPLETED
        assert stages[STAGE_TELEGRAM].status == STATUS_COMPLETED

    def test_drain_processes_videos_oldest_first_with_pause(
        self, db_session, user, channel, video, transcript_service, telegram_service
    ):
        older_video = Video(
            youtube_video_id="video-older",
            channel_id=channel.id,
            title="Older Video",
            published_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
        db_session.add(older_video)
        db_session.commit()

        transcript_service.fetch_transcript.side_effect = ["older transcript", "newer transcript"]
        summarization_service = MagicMock()
        summarization_service.summarize.side_effect = ["older summary", "newer summary"]
        telegram_service.send_video_notification = MagicMock()
        service = make_pipeline_service(transcript_service, summarization_service, telegram_service)
        service.create_stages_for_video(db_session, user.id, video.id)
        service.create_stages_for_video(db_session, user.id, older_video.id)
        db_session.commit()

        pauses: list[float] = []
        stats = service.drain_pending_videos(
            db_session,
            user,
            pause_seconds=60,
            sleep=pauses.append,
        )

        assert stats.videos_processed == 2
        assert pauses == [60]
        assert [call.args[0] for call in summarization_service.summarize.call_args_list] == [
            "older transcript",
            "newer transcript",
        ]
