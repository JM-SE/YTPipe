from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.api.routes.telegram_commands import get_db_session, get_settings
from app.core.settings import Settings
from app.main import app
from app.models.oauth_account import OAuthAccount
from app.models.sync_state import SyncState
from app.models.telegram_command_request import TelegramCommandRequest
from app.models.user import User
from app.models.user_channel import UserChannel
from app.models.video import Video
from app.services.youtube_video_metadata import YouTubeMetadataError, YouTubeVideoMetadataService
from app.services.youtube_video_url import YouTubeURLValidationError, parse_summary_command, parse_youtube_video_url


def command_settings(**overrides: object) -> Settings:
    values = {
        "APP_ENV": "local",
        "DATABASE_URL": "sqlite://",
        "INTERNAL_API_BEARER_TOKEN": "internal-secret",
        "TELEGRAM_COMMANDS_ENABLED": True,
        "TELEGRAM_NOTIFICATIONS_ENABLED": True,
        "TELEGRAM_BOT_TOKEN": "bot-secret",
        "TELEGRAM_CHAT_ID": "-100123",
        "TELEGRAM_ALLOWED_USER_ID": "456",
        "TELEGRAM_BOT_USERNAME": "ytpipe_bot",
    }
    values.update(overrides)
    return Settings(**values)


def intake_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "telegram_update_id": 100,
        "telegram_chat_id": -100123,
        "telegram_user_id": 456,
        "telegram_message_id": 200,
        "telegram_chat_type": "private",
        "telegram_update_type": "message",
        "is_forwarded": False,
        "sender_chat_id": None,
        "text": "/summary https://youtu.be/dQw4w9WgXcQ",
    }
    payload.update(overrides)
    return payload


def test_settings_require_complete_telegram_command_configuration_only_when_enabled() -> None:
    settings = Settings(TELEGRAM_COMMANDS_ENABLED=False)
    settings.validate_runtime_config()

    invalid = Settings(
        TELEGRAM_COMMANDS_ENABLED=True,
        TELEGRAM_NOTIFICATIONS_ENABLED=False,
        TELEGRAM_BOT_TOKEN="",
        TELEGRAM_CHAT_ID="",
        TELEGRAM_ALLOWED_USER_ID="",
        TELEGRAM_BOT_USERNAME="",
        INTERNAL_API_BEARER_TOKEN="replace-me-internal",
    )
    with pytest.raises(RuntimeError, match="TELEGRAM_NOTIFICATIONS_ENABLED"):
        invalid.validate_runtime_config()

    configured = command_settings(TELEGRAM_BOT_USERNAME="@YTPipe_Bot")
    configured.validate_runtime_config()
    assert configured.normalized_telegram_bot_username == "ytpipe_bot"


def test_telegram_command_request_schema_has_recovery_and_reply_contract() -> None:
    columns = TelegramCommandRequest.__table__.columns
    expected = {
        "telegram_update_id",
        "telegram_chat_id",
        "telegram_user_id",
        "telegram_message_id",
        "submitted_url",
        "youtube_video_id",
        "video_id",
        "status",
        "attempt_count",
        "max_attempts",
        "next_attempt_at",
        "processing_started_at",
        "lease_expires_at",
        "lease_token",
        "last_error",
        "acknowledged_at",
        "acknowledgment_message_id",
        "reply_status",
        "reply_attempt_count",
        "reply_max_attempts",
        "reply_next_attempt_at",
        "reply_started_at",
        "reply_sent_at",
        "telegram_reply_message_id",
    }
    assert expected.issubset(set(columns.keys()))
    assert columns["attempt_count"].default.arg == 0
    assert columns["max_attempts"].default.arg == 3
    assert columns["reply_attempt_count"].default.arg == 0
    assert columns["reply_max_attempts"].default.arg == 3
    assert {index.name for index in TelegramCommandRequest.__table__.indexes} == {
        "ix_telegram_command_requests_processing_queue",
        "ix_telegram_command_requests_reply_queue",
        "ix_telegram_command_requests_video_id",
    }


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("https://www.youtube.com/watch?v=dQw4w9WgXcQ", "dQw4w9WgXcQ"),
        ("https://m.youtube.com/watch?v=dQw4w9WgXcQ&t=10", "dQw4w9WgXcQ"),
        ("https://youtube.com/shorts/dQw4w9WgXcQ", "dQw4w9WgXcQ"),
        ("https://youtube.com/live/dQw4w9WgXcQ", "dQw4w9WgXcQ"),
        ("https://youtu.be/dQw4w9WgXcQ?si=tracking", "dQw4w9WgXcQ"),
    ],
)
def test_youtube_url_parser_accepts_supported_families(value: str, expected: str) -> None:
    parsed = parse_youtube_video_url(value)
    assert parsed.video_id == expected
    assert parsed.canonical_url == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"


@pytest.mark.parametrize(
    "value",
    [
        "https://www.youtube.com/playlist?list=PL1234567890",
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ&v=AAAAAAAAAAA",
        "https://www.youtube.com/watch?v=%64Qw4w9WgXcQ",
        "https://www.youtube.com/watch?v=short",
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ#fragment",
        "https://www.youtube.com.evil.example/watch?v=dQw4w9WgXcQ",
        "https://user:pass@www.youtube.com/watch?v=dQw4w9WgXcQ",
        "ftp://www.youtube.com/watch?v=dQw4w9WgXcQ",
    ],
)
def test_youtube_url_parser_rejects_unsafe_or_malformed_urls(value: str) -> None:
    with pytest.raises(YouTubeURLValidationError):
        parse_youtube_video_url(value)


def test_summary_command_accepts_only_configured_bot_suffix() -> None:
    parsed = parse_summary_command(
        "/summary@YTPipe_Bot https://youtu.be/dQw4w9WgXcQ",
        "ytpipe_bot",
    )
    assert parsed.video.video_id == "dQw4w9WgXcQ"
    with pytest.raises(YouTubeURLValidationError, match="Only the /summary"):
        parse_summary_command("/SuMmArY https://youtu.be/dQw4w9WgXcQ", "ytpipe_bot")
    with pytest.raises(YouTubeURLValidationError, match="not addressed"):
        parse_summary_command("/summary@other_bot https://youtu.be/dQw4w9WgXcQ", "ytpipe_bot")


def test_intake_persists_valid_request_without_content_processing(db_session) -> None:
    settings = command_settings()
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_db_session] = lambda: db_session
    try:
        response = TestClient(app).post(
            "/internal/telegram-commands",
            headers={"Authorization": "Bearer internal-secret"},
            json=intake_payload(),
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    body = response.json()
    assert body["accepted"] is True
    assert body["accepted_for_offset"] is True
    assert body["status"] == "pending"
    assert body["acknowledgment_required"] is True
    assert db_session.scalar(select(Video)) is None
    assert db_session.scalar(select(UserChannel)) is None
    command_request = db_session.scalar(select(TelegramCommandRequest))
    assert command_request is not None
    assert command_request.youtube_video_id == "dQw4w9WgXcQ"
    assert command_request.attempt_count == 0
    assert command_request.reply_status == "pending"


def test_intake_is_idempotent_and_conflicting_replay_does_not_block_offset(db_session) -> None:
    settings = command_settings()
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_db_session] = lambda: db_session
    client = TestClient(app)
    try:
        first = client.post(
            "/internal/telegram-commands",
            headers={"Authorization": "Bearer internal-secret"},
            json=intake_payload(),
        )
        duplicate = client.post(
            "/internal/telegram-commands",
            headers={"Authorization": "Bearer internal-secret"},
            json=intake_payload(),
        )
        conflict = client.post(
            "/internal/telegram-commands",
            headers={"Authorization": "Bearer internal-secret"},
            json=intake_payload(telegram_message_id=999),
        )
    finally:
        app.dependency_overrides.clear()

    assert first.status_code == 200
    assert duplicate.status_code == 200
    assert duplicate.json()["duplicate"] is True
    assert duplicate.json()["acknowledgment_required"] is True
    assert conflict.status_code == 200
    assert conflict.json()["outcome"] == "duplicate_conflict"
    assert conflict.json()["accepted_for_offset"] is True
    assert db_session.query(TelegramCommandRequest).count() == 1


def test_intake_rejects_unauthorized_and_disabled_without_persistence(db_session) -> None:
    settings = command_settings()
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_db_session] = lambda: db_session
    try:
        unauthorized = TestClient(app).post(
            "/internal/telegram-commands",
            headers={"Authorization": "Bearer internal-secret"},
            json=intake_payload(telegram_user_id=999),
        )
        wrong_chat = TestClient(app).post(
            "/internal/telegram-commands",
            headers={"Authorization": "Bearer internal-secret"},
            json=intake_payload(telegram_chat_id=-100999),
        )
        non_private = TestClient(app).post(
            "/internal/telegram-commands",
            headers={"Authorization": "Bearer internal-secret"},
            json=intake_payload(telegram_chat_type="group"),
        )
        sender_chat = TestClient(app).post(
            "/internal/telegram-commands",
            headers={"Authorization": "Bearer internal-secret"},
            json=intake_payload(sender_chat_id=-100123),
        )
        disabled_settings = command_settings(TELEGRAM_COMMANDS_ENABLED=False)
        app.dependency_overrides[get_settings] = lambda: disabled_settings
        disabled = TestClient(app).post(
            "/internal/telegram-commands",
            headers={"Authorization": "Bearer internal-secret"},
            json=intake_payload(telegram_update_id=101),
        )
    finally:
        app.dependency_overrides.clear()

    assert unauthorized.status_code == 403
    assert wrong_chat.status_code == 403
    assert non_private.status_code == 403
    assert sender_chat.status_code == 403
    assert disabled.status_code == 409
    assert db_session.query(TelegramCommandRequest).count() == 0


def test_rejected_summary_is_durable_and_ack_endpoint_is_idempotent(db_session) -> None:
    settings = command_settings()
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_db_session] = lambda: db_session
    client = TestClient(app)
    try:
        rejected = client.post(
            "/internal/telegram-commands",
            headers={"Authorization": "Bearer internal-secret"},
            json=intake_payload(text="/summary https://example.com/not-youtube"),
        )
        accepted = client.post(
            "/internal/telegram-commands",
            headers={"Authorization": "Bearer internal-secret"},
            json=intake_payload(telegram_update_id=101),
        )
        request_id = accepted.json()["request_id"]
        acknowledgment = client.post(
            f"/internal/telegram-commands/{request_id}/acknowledgment",
            headers={"Authorization": "Bearer internal-secret"},
            json={"acknowledgment_message_id": 300},
        )
        repeated_acknowledgment = client.post(
            f"/internal/telegram-commands/{request_id}/acknowledgment",
            headers={"Authorization": "Bearer internal-secret"},
            json={"acknowledgment_message_id": 301},
        )
    finally:
        app.dependency_overrides.clear()

    assert rejected.status_code == 200
    assert rejected.json()["status"] == "rejected"
    assert rejected.json()["acknowledgment_required"] is False
    assert acknowledgment.status_code == 200
    assert repeated_acknowledgment.json()["acknowledgment_message_id"] == 300
    row = db_session.get(TelegramCommandRequest, request_id)
    assert row is not None
    assert row.acknowledged_at is not None
    assert row.acknowledgment_message_id == 300
    assert row.status == "pending"
    assert row.reply_status == "pending"


def test_internal_bearer_is_required_for_intake_and_acknowledgment(db_session) -> None:
    settings = command_settings()
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_db_session] = lambda: db_session
    try:
        response = TestClient(app).post(
            "/internal/telegram-commands",
            json=intake_payload(),
        )
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 401


def test_mobile_bearer_cannot_access_telegram_command_intake(db_session) -> None:
    settings = command_settings(MOBILE_API_BEARER_TOKEN="mobile-secret")
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_db_session] = lambda: db_session
    try:
        response = TestClient(app).post(
            "/internal/telegram-commands",
            headers={"Authorization": "Bearer mobile-secret"},
            json=intake_payload(),
        )
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 401


def test_metadata_boundary_upserts_channel_video_and_quota(monkeypatch, db_session) -> None:
    user = User(email="owner@example.com")
    oauth_account = OAuthAccount(
        user=user,
        provider="google",
        access_token="access",
        refresh_token="refresh",
    )
    db_session.add_all([user, oauth_account])
    db_session.flush()

    class FakeAuth:
        def ensure_valid_credentials(self, session, account):
            return object()

    class FakeRequest:
        def execute(self):
            return {
                "items": [
                    {
                        "snippet": {
                            "channelId": "channel-1",
                            "channelTitle": "Channel",
                            "title": "A short #shorts",
                            "publishedAt": "2026-07-28T12:00:00Z",
                        },
                        "contentDetails": {"duration": "PT60S"},
                    }
                ]
            }

    class FakeYouTube:
        def videos(self):
            return self

        def list(self, **kwargs):
            assert kwargs == {"part": "snippet,contentDetails", "id": "dQw4w9WgXcQ"}
            return FakeRequest()

    monkeypatch.setattr("app.services.youtube_video_metadata.build", lambda *args, **kwargs: FakeYouTube())
    service = YouTubeVideoMetadataService(
        FakeAuth(),
        daily_quota_budget=500,
        safety_stop_enabled=True,
    )
    video = service.resolve_and_upsert(
        db_session,
        user=user,
        oauth_account=oauth_account,
        youtube_video_id="dQw4w9WgXcQ",
    )
    db_session.commit()

    assert video.is_short is True
    assert video.channel.title == "Channel"
    assert db_session.scalar(select(UserChannel)) is None
    quota = db_session.scalar(select(SyncState).where(SyncState.process_type == "quota"))
    assert quota is not None
    assert quota.state_metadata["estimated_units_used_today"] == 1


def test_metadata_boundary_classifies_missing_and_malformed_provider_payloads() -> None:
    with pytest.raises(YouTubeMetadataError, match="not accessible") as missing:
        YouTubeVideoMetadataService._parse_metadata({"items": []}, "dQw4w9WgXcQ")
    assert missing.value.retryable is False

    with pytest.raises(YouTubeMetadataError, match="invalid video duration") as malformed:
        YouTubeVideoMetadataService._parse_metadata(
            {
                "items": [
                    {
                        "snippet": {
                            "channelId": "channel-1",
                            "channelTitle": "Channel",
                            "title": "Video",
                        },
                        "contentDetails": {"duration": "PT"},
                    }
                ]
            },
            "dQw4w9WgXcQ",
        )
    assert malformed.value.retryable is True
