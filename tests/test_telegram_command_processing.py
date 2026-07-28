from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import httpx
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.settings import Settings
from app.api.routes.telegram_commands import get_db_session, get_settings
from app.main import app
from app.models.channel import Channel
from app.models.oauth_account import OAuthAccount
from app.models.pipeline_stage import PipelineStage
from app.models.telegram_command_request import TelegramCommandRequest
from app.models.user import User
from app.models.video import Video
from app.services.pipeline import ContentProcessingResult, PipelineService, STAGE_SUMMARY, STAGE_TELEGRAM, STAGE_TRANSCRIPT
from app.services.telegram import TelegramDeliveryAttemptError, TelegramDeliveryResult
from app.services.telegram import TelegramDeliveryService
from app.services.telegram_command_queue import TelegramCommandQueueService


def command_settings(**overrides: object) -> Settings:
    values = {
        "APP_ENV": "local",
        "DATABASE_URL": "sqlite://",
        "INTERNAL_API_BEARER_TOKEN": "internal-secret",
        "TELEGRAM_COMMANDS_ENABLED": True,
        "TELEGRAM_NOTIFICATIONS_ENABLED": True,
        "TELEGRAM_BOT_TOKEN": "bot-secret",
        "TELEGRAM_CHAT_ID": "7729262607",
        "TELEGRAM_ALLOWED_USER_ID": "7729262607",
        "TELEGRAM_BOT_USERNAME": "ytpipe_bot",
    }
    values.update(overrides)
    return Settings(**values)


def add_request(db_session, *, status: str = "pending", reply_status: str = "pending", video_id: int | None = None):
    request = TelegramCommandRequest(
        telegram_update_id=100,
        telegram_chat_id=7729262607,
        telegram_user_id=7729262607,
        telegram_message_id=200,
        command="summary",
        submitted_url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        youtube_video_id="dQw4w9WgXcQ",
        video_id=video_id,
        status=status,
        reply_status=reply_status,
    )
    db_session.add(request)
    db_session.commit()
    return request


def add_owner_and_video(db_session, *, summary: str | None = None, is_short: bool | None = None):
    user = User(email="owner@example.com")
    channel = Channel(youtube_channel_id="channel-1", title="Channel")
    video = Video(
        youtube_video_id="dQw4w9WgXcQ",
        channel=channel,
        title="Video",
        summary=summary,
        is_short=is_short,
    )
    db_session.add_all(
        [
            user,
            channel,
            video,
            OAuthAccount(
                user=user,
                provider="google",
                access_token="access",
                refresh_token="refresh",
            ),
        ]
    )
    db_session.commit()
    return user, video


def test_worker_reuses_cached_summary_and_sends_one_request_specific_reply(
    db_session,
    monkeypatch,
) -> None:
    _user, video = add_owner_and_video(db_session, summary="Cached summary")
    request = add_request(db_session)
    settings = command_settings()

    monkeypatch.setattr(
        "app.services.telegram_command_queue.YouTubeVideoMetadataService.resolve_and_upsert",
        lambda self, session, **kwargs: video,
    )
    sent: list[tuple[str, int, int]] = []

    def send_reply(self, text, *, chat_id, reply_to_message_id):
        sent.append((text, chat_id, reply_to_message_id))
        return TelegramDeliveryResult(provider_message_id=901)

    monkeypatch.setattr(
        "app.services.telegram_command_queue.TelegramDeliveryService.send_message_to_chat",
        send_reply,
    )
    monkeypatch.setattr(
        PipelineService,
        "process_content_stages",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("cached content was processed")),
    )

    first = TelegramCommandQueueService(settings, db_session).process_next()
    assert first.claimed is True
    assert first.status == "completed"
    assert sent == []

    second = TelegramCommandQueueService(settings, db_session).process_next()
    assert second.claimed is True
    assert sent == [("📝 Cached summary", 7729262607, 200)]

    stored = db_session.get(TelegramCommandRequest, request.id)
    assert stored is not None
    assert stored.attempt_count == 1
    assert stored.reply_attempt_count == 1
    assert stored.reply_status == "sent"
    assert stored.telegram_reply_message_id == 901


def test_manual_content_stages_do_not_create_automatic_telegram_stage(db_session) -> None:
    user, video = add_owner_and_video(db_session)
    transcript = MagicMock()
    transcript.fetch_transcript.return_value = "Transcript"
    summarizer = MagicMock()
    summarizer.summarize.return_value = "Summary"
    service = PipelineService(transcript_service=transcript, summarization_service=summarizer)

    result = service.process_content_stages(db_session, user, video)
    db_session.commit()

    assert result.outcome == "completed"
    assert video.summary == "Summary"
    stages = set(db_session.scalars(select(PipelineStage.stage)).all())
    assert stages == {STAGE_TRANSCRIPT, STAGE_SUMMARY}
    assert STAGE_TELEGRAM not in stages


def test_open_summary_circuit_still_fetches_transcript_for_recovery(db_session) -> None:
    user, video = add_owner_and_video(db_session)
    transcript = MagicMock()
    transcript.fetch_transcript.return_value = "Transcript"
    summarizer = MagicMock()
    service = PipelineService(
        transcript_service=transcript,
        summarization_service=summarizer,
        summary_paused=True,
    )

    result = service.process_content_stages(db_session, user, video)

    assert isinstance(result, ContentProcessingResult)
    assert result.outcome == "pending_retry"
    transcript.fetch_transcript.assert_called_once()
    summarizer.summarize.assert_not_called()
    assert set(db_session.scalars(select(PipelineStage.stage)).all()) == {
        STAGE_TRANSCRIPT,
        STAGE_SUMMARY,
    }


def test_automatic_drain_ignores_manual_only_content_stages(db_session) -> None:
    user, video = add_owner_and_video(db_session)
    service = PipelineService()
    service.create_content_stages_for_video(db_session, user.id, video.id)
    db_session.commit()

    stats = service.process_next_pending_video(db_session, user)

    assert stats.stages_processed == 0
    assert all(
        stage.status == "pending"
        for stage in db_session.scalars(select(PipelineStage)).all()
    )


def test_reply_delivery_failure_retries_without_regenerating_summary(db_session, monkeypatch) -> None:
    _user, video = add_owner_and_video(db_session, summary="Cached summary")
    request = add_request(db_session, status="completed", reply_status="pending", video_id=video.id)
    settings = command_settings()
    failures = iter(
        [
            TelegramDeliveryAttemptError("timeout", retryable=True),
            TelegramDeliveryResult(provider_message_id=902),
        ]
    )

    def send_reply(self, text, *, chat_id, reply_to_message_id):
        value = next(failures)
        if isinstance(value, Exception):
            raise value
        return value

    monkeypatch.setattr(
        "app.services.telegram_command_queue.TelegramDeliveryService.send_message_to_chat",
        send_reply,
    )
    first = TelegramCommandQueueService(settings, db_session).process_next()
    assert first.status == "completed"
    stored = db_session.get(TelegramCommandRequest, request.id)
    assert stored is not None
    assert stored.reply_status == "pending_retry"
    stored.reply_next_attempt_at = datetime.now(UTC) - timedelta(seconds=1)
    db_session.commit()

    second = TelegramCommandQueueService(settings, db_session).process_next()
    assert second.status == "completed"
    assert db_session.get(TelegramCommandRequest, request.id).reply_status == "sent"
    assert video.summary == "Cached summary"


def test_disabled_short_is_rejected_before_content_processing(db_session, monkeypatch) -> None:
    _user, video = add_owner_and_video(db_session, is_short=True)
    request = add_request(db_session, video_id=video.id)
    settings = command_settings(SHORTS_PROCESSING_ENABLED=False)
    monkeypatch.setattr(
        "app.services.telegram_command_queue.YouTubeVideoMetadataService.resolve_and_upsert",
        lambda self, session, **kwargs: video,
    )
    process_content = MagicMock()
    monkeypatch.setattr(PipelineService, "process_content_stages", process_content)

    result = TelegramCommandQueueService(settings, db_session).process_next()

    assert result.status == "rejected"
    process_content.assert_not_called()
    stored = db_session.get(TelegramCommandRequest, request.id)
    assert stored is not None
    assert stored.last_error == "short_disabled"
    assert stored.reply_status == "pending"


def test_worker_endpoint_is_internal_only_and_returns_empty_queue(db_session) -> None:
    settings = command_settings()
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_db_session] = lambda: db_session
    try:
        client = TestClient(app)
        empty = client.post(
            "/internal/telegram-commands/process-next",
            headers={"Authorization": "Bearer internal-secret"},
        )
        unauthorized = client.post("/internal/telegram-commands/process-next")
    finally:
        app.dependency_overrides.clear()

    assert empty.status_code == 200
    assert empty.json() == {
        "claimed": False,
        "request_id": None,
        "status": "empty",
        "work_remaining": False,
    }
    assert unauthorized.status_code == 401


def test_stale_processing_claim_is_recovered_without_losing_the_request(db_session) -> None:
    request = add_request(db_session, status="processing")
    request.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
    request.lease_token = "stale-token"
    db_session.commit()

    service = TelegramCommandQueueService(command_settings(), db_session)
    service._recover_stale_claims()
    db_session.commit()

    stored = db_session.get(TelegramCommandRequest, request.id)
    assert stored is not None
    assert stored.status == "pending_retry"
    assert stored.lease_token is None
    assert stored.last_error == "Previous command processing lease expired."


def test_sibling_command_waits_without_consuming_attempts(db_session) -> None:
    leader = add_request(db_session)
    leader.status = "processing"
    leader.lease_expires_at = datetime.now(UTC) + timedelta(minutes=5)
    leader.lease_token = "active-leader"
    sibling = TelegramCommandRequest(
        telegram_update_id=101,
        telegram_chat_id=7729262607,
        telegram_user_id=7729262607,
        telegram_message_id=201,
        command="summary",
        submitted_url=leader.submitted_url,
        youtube_video_id=leader.youtube_video_id,
        status="pending",
        reply_status="pending",
    )
    db_session.add(sibling)
    db_session.commit()

    result = TelegramCommandQueueService(command_settings(), db_session).process_next()

    assert result.request_id == sibling.id
    assert db_session.get(TelegramCommandRequest, sibling.id).status == "pending_retry"
    assert db_session.get(TelegramCommandRequest, sibling.id).attempt_count == 0


def test_request_specific_telegram_reply_uses_reply_parameters_and_validates_success(monkeypatch) -> None:
    settings = command_settings()
    captured: dict[str, object] = {}

    def fake_post(url, *, json, timeout):
        captured.update(json)
        assert "bot-secret" in url
        assert timeout == 10.0
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 77}})

    monkeypatch.setattr("app.services.telegram.httpx.post", fake_post)
    result = TelegramDeliveryService(settings).send_message_to_chat(
        "summary",
        chat_id=7729262607,
        reply_to_message_id=200,
    )

    assert result.provider_message_id == 77
    assert captured["reply_parameters"] == {
        "message_id": 200,
        "allow_sending_without_reply": True,
    }

    monkeypatch.setattr(
        "app.services.telegram.httpx.post",
        lambda *args, **kwargs: httpx.Response(200, json={"ok": True, "result": {}}),
    )
    try:
        TelegramDeliveryService(settings).send_message_to_chat("summary", chat_id=7729262607)
    except TelegramDeliveryAttemptError as exc:
        assert exc.retryable is True
    else:
        raise AssertionError("malformed Telegram success was accepted")
