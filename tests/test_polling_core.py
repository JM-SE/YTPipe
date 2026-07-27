from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from googleapiclient.errors import HttpError
from httplib2 import Response

import app.api.routes.polling as polling_route_module
import app.services.polling as polling_module
from app.api.routes.polling import get_db_session, get_settings
from app.core.settings import Settings
from app.main import app
from app.models.channel import Channel
from app.models.notification_delivery import NotificationDelivery
from app.models.oauth_account import OAuthAccount
from app.models.pipeline_stage import PipelineStage
from app.models.sync_state import SyncState
from app.models.user import User
from app.models.user_channel import UserChannel
from app.models.video import Video
from app.services.email import EmailDeliveryAttemptError
from app.services.polling import POLLING_PROCESS, QUOTA_PROCESS, SUMMARIZATION_PROCESS, YouTubePollingService
from app.services.telegram import TelegramDeliveryService


def test_run_poll_requires_bearer_token(db_session) -> None:
    client = TestClient(app)
    settings = Settings(
        APP_SECRET_KEY="super-secret",
        INTERNAL_API_BEARER_TOKEN="internal-secret",
        GOOGLE_CLIENT_ID="client-id",
        GOOGLE_CLIENT_SECRET="client-secret",
        DATABASE_URL="sqlite://",
    )
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_db_session] = lambda: db_session

    try:
        response = client.post("/internal/run-poll")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 401


def test_run_poll_google_reauth_401_sends_telegram_alert_and_records_state(db_session, monkeypatch) -> None:
    client = TestClient(app)
    settings = Settings(
        APP_SECRET_KEY="super-secret",
        INTERNAL_API_BEARER_TOKEN="internal-secret",
        GOOGLE_CLIENT_ID="client-id",
        GOOGLE_CLIENT_SECRET="client-secret",
        GOOGLE_REDIRECT_URI="http://127.0.0.1:8000/auth/callback",
        TELEGRAM_NOTIFICATIONS_ENABLED=True,
        TELEGRAM_BOT_TOKEN="bot-token",
        TELEGRAM_CHAT_ID="chat-id",
        DATABASE_URL="sqlite://",
    )

    user = User(email="owner@example.com")
    db_session.add(user)
    db_session.flush()
    db_session.add(
        OAuthAccount(
            user_id=user.id,
            provider="google",
            access_token="expired-token",
            refresh_token="refresh",
            token_expiry=datetime.now(UTC) - timedelta(hours=1),
        )
    )
    db_session.commit()

    def raise_reauth(self, session, oauth_account):  # noqa: ARG001
        raise HTTPException(
            status_code=401,
            detail="Stored Google credentials can no longer be refreshed. Manual re-auth is required.",
        )

    sent_messages: list[str] = []

    monkeypatch.setattr(polling_module.GoogleOAuthService, "ensure_valid_credentials", raise_reauth)
    monkeypatch.setattr(
        polling_route_module.TelegramDeliveryService,
        "send_message",
        lambda self, text: sent_messages.append(text),
    )

    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_db_session] = lambda: db_session

    try:
        response = client.post(
            "/internal/run-poll",
            headers={"Authorization": "Bearer internal-secret"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 401
    assert response.json() == {
        "detail": "Stored Google credentials can no longer be refreshed. Manual re-auth is required.",
    }
    assert len(sent_messages) == 1
    assert "YTPipe necesita re-auth de Google" in sent_messages[0]
    assert "ssh -N -L 127.0.0.1:8000:127.0.0.1:8000" in sent_messages[0]
    assert "http://127.0.0.1:8000/auth/google" in sent_messages[0]

    polling_state = db_session.query(SyncState).filter_by(process_type=POLLING_PROCESS).one()
    assert polling_state.last_error_message == response.json()["detail"]
    assert polling_state.last_error_at is not None
    assert polling_state.state_metadata is not None
    assert polling_state.state_metadata["google_reauth_required"] is True
    assert polling_state.state_metadata["google_reauth_last_error"] == response.json()["detail"]
    assert polling_state.state_metadata["google_reauth_alert_sent_at"]
    assert polling_state.state_metadata["google_reauth_alert_error"] is None


def test_run_poll_google_reauth_401_alert_is_throttled_for_24_hours(db_session, monkeypatch) -> None:
    client = TestClient(app)
    settings = Settings(
        APP_SECRET_KEY="super-secret",
        INTERNAL_API_BEARER_TOKEN="internal-secret",
        GOOGLE_CLIENT_ID="client-id",
        GOOGLE_CLIENT_SECRET="client-secret",
        GOOGLE_REDIRECT_URI="http://127.0.0.1:8000/auth/callback",
        TELEGRAM_NOTIFICATIONS_ENABLED=True,
        TELEGRAM_BOT_TOKEN="bot-token",
        TELEGRAM_CHAT_ID="chat-id",
        DATABASE_URL="sqlite://",
    )

    user = User(email="owner@example.com")
    db_session.add(user)
    db_session.flush()
    db_session.add(
        OAuthAccount(
            user_id=user.id,
            provider="google",
            access_token="expired-token",
            refresh_token="refresh",
            token_expiry=datetime.now(UTC) - timedelta(hours=1),
        )
    )
    db_session.commit()

    def raise_reauth(self, session, oauth_account):  # noqa: ARG001
        raise HTTPException(
            status_code=401,
            detail="Stored Google credentials can no longer be refreshed. Manual re-auth is required.",
        )

    sent_messages: list[str] = []

    monkeypatch.setattr(polling_module.GoogleOAuthService, "ensure_valid_credentials", raise_reauth)
    monkeypatch.setattr(
        polling_route_module.TelegramDeliveryService,
        "send_message",
        lambda self, text: sent_messages.append(text),
    )

    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_db_session] = lambda: db_session

    try:
        first = client.post(
            "/internal/run-poll",
            headers={"Authorization": "Bearer internal-secret"},
        )
        polling_state = db_session.query(SyncState).filter_by(process_type=POLLING_PROCESS).one()
        first_sent_at = polling_state.state_metadata["google_reauth_alert_sent_at"]

        second = client.post(
            "/internal/run-poll",
            headers={"Authorization": "Bearer internal-secret"},
        )
    finally:
        app.dependency_overrides.clear()

    assert first.status_code == 401
    assert second.status_code == 401
    assert len(sent_messages) == 1

    polling_state = db_session.query(SyncState).filter_by(process_type=POLLING_PROCESS).one()
    assert polling_state.state_metadata is not None
    assert polling_state.state_metadata["google_reauth_alert_sent_at"] == first_sent_at


def test_run_poll_quota_blocked_exits_before_channel_processing(db_session, monkeypatch) -> None:
    client = TestClient(app)
    settings = Settings(
        APP_SECRET_KEY="super-secret",
        INTERNAL_API_BEARER_TOKEN="internal-secret",
        POLL_QUOTA_DAILY_BUDGET=2,
        POLL_QUOTA_SAFETY_STOP_ENABLED=True,
        GOOGLE_CLIENT_ID="client-id",
        GOOGLE_CLIENT_SECRET="client-secret",
        DATABASE_URL="sqlite://",
    )

    user = User(email="owner@example.com")
    channel = Channel(youtube_channel_id="channel-1", title="Channel 1", uploads_playlist_id="uploads-1")
    db_session.add_all([user, channel])
    db_session.flush()
    db_session.add(
        UserChannel(
            user_id=user.id,
            channel_id=channel.id,
            is_monitored=True,
        )
    )
    db_session.add(
        OAuthAccount(
            user_id=user.id,
            provider="google",
            access_token="token",
            refresh_token="refresh",
            token_expiry=datetime.now(UTC) + timedelta(hours=1),
        )
    )
    db_session.add(
        SyncState(
            user_id=user.id,
            process_type=QUOTA_PROCESS,
            state_metadata={
                "usage_day": datetime.now(UTC).date().isoformat(),
                "estimated_units_used_today": 2,
            },
        )
    )
    db_session.commit()

    monkeypatch.setattr(
        polling_module.GoogleOAuthService,
        "ensure_valid_credentials",
        lambda self, session, oauth_account: object(),  # noqa: ARG005
    )

    class FakeYouTube:
        def playlistItems(self):
            raise AssertionError("Polling should not process channels when quota is blocked")

    monkeypatch.setattr(polling_module, "build", lambda *args, **kwargs: FakeYouTube())

    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_db_session] = lambda: db_session

    try:
        response = client.post(
            "/internal/run-poll",
            headers={"Authorization": "Bearer internal-secret"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json() == {
        "run_outcome": "blocked",
        "channels_processed": 0,
        "channels_failed": 0,
        "baselines_established": 0,
        "new_videos_detected": 0,
        "quota_blocked": True,
    }

    polling_state = db_session.query(SyncState).filter_by(process_type=POLLING_PROCESS).one()
    quota_state = db_session.query(SyncState).filter_by(process_type=QUOTA_PROCESS).one()
    assert polling_state.state_metadata["run_outcome"] == "blocked"
    assert quota_state.state_metadata["daily_quota_budget"] == 2
    assert quota_state.state_metadata["estimated_units_used_today"] == 2
    assert quota_state.state_metadata["last_run_estimated_units"] == 0
    assert quota_state.state_metadata["safety_stop_active"] is True


def test_run_poll_processes_only_monitored_channels_sequentially_and_sets_baselines(db_session, monkeypatch) -> None:
    client = TestClient(app)
    settings = Settings(
        APP_SECRET_KEY="super-secret",
        INTERNAL_API_BEARER_TOKEN="internal-secret",
        POLL_QUOTA_DAILY_BUDGET=50,
        POLL_QUOTA_SAFETY_STOP_ENABLED=True,
        GOOGLE_CLIENT_ID="client-id",
        GOOGLE_CLIENT_SECRET="client-secret",
        DATABASE_URL="sqlite://",
    )

    user = User(email="owner@example.com")
    channel_a = Channel(youtube_channel_id="channel-a", title="A", uploads_playlist_id="uploads-a")
    channel_b = Channel(youtube_channel_id="channel-b", title="B", uploads_playlist_id="uploads-b")
    channel_c = Channel(youtube_channel_id="channel-c", title="C", uploads_playlist_id="uploads-c")
    db_session.add_all([user, channel_a, channel_b, channel_c])
    db_session.flush()
    db_session.add_all(
        [
            UserChannel(user_id=user.id, channel_id=channel_a.id, is_monitored=True),
            UserChannel(user_id=user.id, channel_id=channel_b.id, is_monitored=False),
            UserChannel(user_id=user.id, channel_id=channel_c.id, is_monitored=True),
        ]
    )
    db_session.add(
        OAuthAccount(
            user_id=user.id,
            provider="google",
            access_token="token",
            refresh_token="refresh",
            token_expiry=datetime.now(UTC) + timedelta(hours=1),
        )
    )
    db_session.commit()

    monkeypatch.setattr(
        polling_module.GoogleOAuthService,
        "ensure_valid_credentials",
        lambda self, session, oauth_account: object(),  # noqa: ARG005
    )

    call_order: list[str] = []

    class FakeRequest:
        def __init__(self, payload: dict[str, object], playlist_id: str):
            self.payload = payload
            self.playlist_id = playlist_id

        def execute(self):
            call_order.append(self.playlist_id)
            return self.payload

    class FakePlaylistItemsResource:
        def list(self, **kwargs):
            playlist_id = kwargs["playlistId"]
            return FakeRequest(
                {
                    "items": [
                        {
                            "snippet": {"title": f"Title-{playlist_id}", "publishedAt": "2026-04-25T12:00:00Z"},
                            "contentDetails": {"videoId": f"video-{playlist_id}"},
                        }
                    ]
                },
                playlist_id,
            )

    class FakeYouTube:
        def playlistItems(self):
            return FakePlaylistItemsResource()

    monkeypatch.setattr(polling_module, "build", lambda *args, **kwargs: FakeYouTube())

    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_db_session] = lambda: db_session

    try:
        response = client.post(
            "/internal/run-poll",
            headers={"Authorization": "Bearer internal-secret"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json() == {
        "run_outcome": "success",
        "channels_processed": 2,
        "channels_failed": 0,
        "baselines_established": 2,
        "new_videos_detected": 0,
        "quota_blocked": False,
    }
    assert call_order == ["uploads-a", "uploads-c"]

    monitored_rows = db_session.query(UserChannel).order_by(UserChannel.channel_id.asc()).all()
    assert monitored_rows[0].baseline_established_at is not None
    assert monitored_rows[0].last_seen_video_id == "video-uploads-a"
    assert monitored_rows[1].baseline_established_at is None
    assert monitored_rows[2].baseline_established_at is not None
    assert monitored_rows[2].last_seen_video_id == "video-uploads-c"


def test_run_poll_new_video_creates_video_and_delivery(db_session, monkeypatch) -> None:
    client = TestClient(app)
    settings = Settings(
        APP_SECRET_KEY="super-secret",
        INTERNAL_API_BEARER_TOKEN="internal-secret",
        POLL_QUOTA_DAILY_BUDGET=50,
        POLL_QUOTA_SAFETY_STOP_ENABLED=True,
        GOOGLE_CLIENT_ID="client-id",
        GOOGLE_CLIENT_SECRET="client-secret",
        DATABASE_URL="sqlite://",
    )

    user = User(email="owner@example.com")
    channel = Channel(youtube_channel_id="channel-a", title="A", uploads_playlist_id="uploads-a")
    db_session.add_all([user, channel])
    db_session.flush()
    db_session.add(
        UserChannel(
            user_id=user.id,
            channel_id=channel.id,
            is_monitored=True,
            last_seen_video_id="video-old",
            baseline_established_at=datetime.now(UTC) - timedelta(days=1),
        )
    )
    db_session.add(
        OAuthAccount(
            user_id=user.id,
            provider="google",
            access_token="token",
            refresh_token="refresh",
            token_expiry=datetime.now(UTC) + timedelta(hours=1),
        )
    )
    db_session.commit()

    monkeypatch.setattr(
        polling_module.GoogleOAuthService,
        "ensure_valid_credentials",
        lambda self, session, oauth_account: object(),  # noqa: ARG005
    )

    class FakeRequest:
        def execute(self):
            return {
                "items": [
                    {
                        "snippet": {"title": "Brand New Video", "publishedAt": "2026-04-25T12:00:00Z"},
                        "contentDetails": {"videoId": "video-new"},
                    }
                ]
            }

    class FakePlaylistItemsResource:
        def list(self, **kwargs):  # noqa: ARG002
            return FakeRequest()

    class FakeYouTube:
        def playlistItems(self):
            return FakePlaylistItemsResource()

    monkeypatch.setattr(polling_module, "build", lambda *args, **kwargs: FakeYouTube())

    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_db_session] = lambda: db_session

    try:
        response = client.post(
            "/internal/run-poll",
            headers={"Authorization": "Bearer internal-secret"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["new_videos_detected"] == 1

    user_channel = db_session.query(UserChannel).one()
    video = db_session.query(Video).one()
    delivery = db_session.query(NotificationDelivery).one()

    assert user_channel.last_seen_video_id == "video-new"
    assert video.youtube_video_id == "video-new"
    assert video.title == "Brand New Video"
    assert delivery.user_id == user.id
    assert delivery.video_id == video.id
    assert delivery.status == "delivered"
    assert delivery.attempt_count == 1
    assert delivery.last_attempt_at is not None
    assert delivery.last_error is None


def test_run_poll_unchanged_latest_video_is_noop(db_session, monkeypatch) -> None:
    client = TestClient(app)
    settings = Settings(
        APP_SECRET_KEY="super-secret",
        INTERNAL_API_BEARER_TOKEN="internal-secret",
        POLL_QUOTA_DAILY_BUDGET=50,
        POLL_QUOTA_SAFETY_STOP_ENABLED=True,
        GOOGLE_CLIENT_ID="client-id",
        GOOGLE_CLIENT_SECRET="client-secret",
        DATABASE_URL="sqlite://",
    )

    user = User(email="owner@example.com")
    channel = Channel(youtube_channel_id="channel-a", title="A", uploads_playlist_id="uploads-a")
    db_session.add_all([user, channel])
    db_session.flush()
    db_session.add(
        UserChannel(
            user_id=user.id,
            channel_id=channel.id,
            is_monitored=True,
            last_seen_video_id="video-same",
            baseline_established_at=datetime.now(UTC) - timedelta(days=1),
        )
    )
    db_session.add(
        OAuthAccount(
            user_id=user.id,
            provider="google",
            access_token="token",
            refresh_token="refresh",
            token_expiry=datetime.now(UTC) + timedelta(hours=1),
        )
    )
    db_session.commit()

    monkeypatch.setattr(
        polling_module.GoogleOAuthService,
        "ensure_valid_credentials",
        lambda self, session, oauth_account: object(),  # noqa: ARG005
    )

    class FakeRequest:
        def execute(self):
            return {
                "items": [
                    {
                        "snippet": {"title": "Same Video", "publishedAt": "2026-04-25T12:00:00Z"},
                        "contentDetails": {"videoId": "video-same"},
                    }
                ]
            }

    class FakePlaylistItemsResource:
        def list(self, **kwargs):  # noqa: ARG002
            return FakeRequest()

    class FakeYouTube:
        def playlistItems(self):
            return FakePlaylistItemsResource()

    monkeypatch.setattr(polling_module, "build", lambda *args, **kwargs: FakeYouTube())

    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_db_session] = lambda: db_session

    try:
        response = client.post(
            "/internal/run-poll",
            headers={"Authorization": "Bearer internal-secret"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json() == {
        "run_outcome": "success",
        "channels_processed": 1,
        "channels_failed": 0,
        "baselines_established": 0,
        "new_videos_detected": 0,
        "quota_blocked": False,
    }
    assert db_session.query(Video).count() == 0
    assert db_session.query(NotificationDelivery).count() == 0


def test_run_poll_success_persists_quota_metadata(db_session, monkeypatch) -> None:
    client = TestClient(app)
    settings = Settings(
        APP_SECRET_KEY="super-secret",
        INTERNAL_API_BEARER_TOKEN="internal-secret",
        POLL_QUOTA_DAILY_BUDGET=50,
        POLL_QUOTA_SAFETY_STOP_ENABLED=True,
        GOOGLE_CLIENT_ID="client-id",
        GOOGLE_CLIENT_SECRET="client-secret",
        DATABASE_URL="sqlite://",
    )

    user = User(email="owner@example.com")
    channel = Channel(youtube_channel_id="channel-a", title="A", uploads_playlist_id="uploads-a")
    db_session.add_all([user, channel])
    db_session.flush()
    db_session.add(UserChannel(user_id=user.id, channel_id=channel.id, is_monitored=True))
    db_session.add(
        OAuthAccount(
            user_id=user.id,
            provider="google",
            access_token="token",
            refresh_token="refresh",
            token_expiry=datetime.now(UTC) + timedelta(hours=1),
        )
    )
    db_session.commit()

    monkeypatch.setattr(
        polling_module.GoogleOAuthService,
        "ensure_valid_credentials",
        lambda self, session, oauth_account: object(),  # noqa: ARG005
    )

    class FakeRequest:
        def execute(self):
            return {"items": []}

    class FakePlaylistItemsResource:
        def list(self, **kwargs):  # noqa: ARG002
            return FakeRequest()

    class FakeYouTube:
        def playlistItems(self):
            return FakePlaylistItemsResource()

    monkeypatch.setattr(polling_module, "build", lambda *args, **kwargs: FakeYouTube())

    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_db_session] = lambda: db_session

    try:
        response = client.post(
            "/internal/run-poll",
            headers={"Authorization": "Bearer internal-secret"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200

    quota_state = db_session.query(SyncState).filter_by(process_type=QUOTA_PROCESS).one()
    assert quota_state.state_metadata["daily_quota_budget"] == 50
    assert quota_state.state_metadata["estimated_units_used_today"] == 1
    assert quota_state.state_metadata["last_run_estimated_units"] == 1
    assert quota_state.state_metadata["safety_stop_active"] is False


def test_run_poll_unusable_playlist_is_classified_and_run_continues(db_session, monkeypatch) -> None:
    client = TestClient(app)
    settings = Settings(
        APP_SECRET_KEY="super-secret",
        INTERNAL_API_BEARER_TOKEN="internal-secret",
        POLL_QUOTA_DAILY_BUDGET=50,
        POLL_QUOTA_SAFETY_STOP_ENABLED=True,
        GOOGLE_CLIENT_ID="client-id",
        GOOGLE_CLIENT_SECRET="client-secret",
        DATABASE_URL="sqlite://",
    )

    user = User(email="owner@example.com")
    bad_channel = Channel(youtube_channel_id="channel-bad", title="Bad", uploads_playlist_id=None)
    good_channel = Channel(youtube_channel_id="channel-good", title="Good", uploads_playlist_id="uploads-good")
    db_session.add_all([user, bad_channel, good_channel])
    db_session.flush()
    db_session.add_all(
        [
            UserChannel(user_id=user.id, channel_id=bad_channel.id, is_monitored=True),
            UserChannel(user_id=user.id, channel_id=good_channel.id, is_monitored=True),
        ]
    )
    db_session.add(
        OAuthAccount(
            user_id=user.id,
            provider="google",
            access_token="token",
            refresh_token="refresh",
            token_expiry=datetime.now(UTC) + timedelta(hours=1),
        )
    )
    db_session.commit()

    monkeypatch.setattr(
        polling_module.GoogleOAuthService,
        "ensure_valid_credentials",
        lambda self, session, oauth_account: object(),  # noqa: ARG005
    )

    class FakeRequest:
        def execute(self):
            return {
                "items": [
                    {
                        "snippet": {"title": "Good Video", "publishedAt": "2026-04-25T12:00:00Z"},
                        "contentDetails": {"videoId": "video-good"},
                    }
                ]
            }

    class FakePlaylistItemsResource:
        def list(self, **kwargs):  # noqa: ARG002
            return FakeRequest()

    class FakeYouTube:
        def playlistItems(self):
            return FakePlaylistItemsResource()

    monkeypatch.setattr(polling_module, "build", lambda *args, **kwargs: FakeYouTube())

    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_db_session] = lambda: db_session

    try:
        response = client.post(
            "/internal/run-poll",
            headers={"Authorization": "Bearer internal-secret"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["run_outcome"] == "partial_success"
    assert response.json()["channels_processed"] == 1
    assert response.json()["channels_failed"] == 1

    bad_user_channel = db_session.query(UserChannel).filter_by(channel_id=bad_channel.id).one()
    assert bad_user_channel.is_monitored is True
    assert bad_user_channel.baseline_established_at is None

    polling_state = db_session.query(SyncState).filter_by(process_type=POLLING_PROCESS).one()
    errors = polling_state.state_metadata["channel_errors"]
    assert len(errors) == 1
    assert errors[0]["error_code"] == "unusable_uploads_playlist"


def test_run_poll_playlist_not_found_is_classified_as_unusable(db_session, monkeypatch) -> None:
    client = TestClient(app)
    settings = Settings(
        APP_SECRET_KEY="super-secret",
        INTERNAL_API_BEARER_TOKEN="internal-secret",
        POLL_QUOTA_DAILY_BUDGET=50,
        POLL_QUOTA_SAFETY_STOP_ENABLED=True,
        GOOGLE_CLIENT_ID="client-id",
        GOOGLE_CLIENT_SECRET="client-secret",
        DATABASE_URL="sqlite://",
    )

    user = User(email="owner@example.com")
    channel = Channel(youtube_channel_id="channel-a", title="A", uploads_playlist_id="uploads-a")
    db_session.add_all([user, channel])
    db_session.flush()
    db_session.add(UserChannel(user_id=user.id, channel_id=channel.id, is_monitored=True))
    db_session.add(
        OAuthAccount(
            user_id=user.id,
            provider="google",
            access_token="token",
            refresh_token="refresh",
            token_expiry=datetime.now(UTC) + timedelta(hours=1),
        )
    )
    db_session.commit()

    monkeypatch.setattr(
        polling_module.GoogleOAuthService,
        "ensure_valid_credentials",
        lambda self, session, oauth_account: object(),  # noqa: ARG005
    )

    error_content = json.dumps(
        {
            "error": {
                "errors": [
                    {
                        "reason": "playlistNotFound",
                    }
                ]
            }
        }
    ).encode("utf-8")

    class FakeRequest:
        def execute(self):
            raise HttpError(Response({"status": "404"}), error_content)

    class FakePlaylistItemsResource:
        def list(self, **kwargs):  # noqa: ARG002
            return FakeRequest()

    class FakeYouTube:
        def playlistItems(self):
            return FakePlaylistItemsResource()

    monkeypatch.setattr(polling_module, "build", lambda *args, **kwargs: FakeYouTube())

    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_db_session] = lambda: db_session

    try:
        response = client.post(
            "/internal/run-poll",
            headers={"Authorization": "Bearer internal-secret"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["run_outcome"] == "failed"
    assert response.json()["channels_failed"] == 1

    polling_state = db_session.query(SyncState).filter_by(process_type=POLLING_PROCESS).one()
    errors = polling_state.state_metadata["channel_errors"]
    assert errors[0]["error_code"] == "unusable_uploads_playlist"

    quota_state = db_session.query(SyncState).filter_by(process_type=QUOTA_PROCESS).one()
    assert quota_state.state_metadata["estimated_units_used_today"] == 1
    assert quota_state.state_metadata["last_run_estimated_units"] == 1


def test_run_poll_response_is_aggregate_only(db_session, monkeypatch) -> None:
    client = TestClient(app)
    settings = Settings(
        APP_SECRET_KEY="super-secret",
        INTERNAL_API_BEARER_TOKEN="internal-secret",
        POLL_QUOTA_DAILY_BUDGET=50,
        POLL_QUOTA_SAFETY_STOP_ENABLED=True,
        GOOGLE_CLIENT_ID="client-id",
        GOOGLE_CLIENT_SECRET="client-secret",
        DATABASE_URL="sqlite://",
    )

    user = User(email="owner@example.com")
    channel = Channel(youtube_channel_id="channel-a", title="A", uploads_playlist_id="uploads-a")
    db_session.add_all([user, channel])
    db_session.flush()
    db_session.add(UserChannel(user_id=user.id, channel_id=channel.id, is_monitored=True))
    db_session.add(
        OAuthAccount(
            user_id=user.id,
            provider="google",
            access_token="token",
            refresh_token="refresh",
            token_expiry=datetime.now(UTC) + timedelta(hours=1),
        )
    )
    db_session.commit()

    monkeypatch.setattr(
        polling_module.GoogleOAuthService,
        "ensure_valid_credentials",
        lambda self, session, oauth_account: object(),  # noqa: ARG005
    )

    class FakeRequest:
        def execute(self):
            return {"items": []}

    class FakePlaylistItemsResource:
        def list(self, **kwargs):  # noqa: ARG002
            return FakeRequest()

    class FakeYouTube:
        def playlistItems(self):
            return FakePlaylistItemsResource()

    monkeypatch.setattr(polling_module, "build", lambda *args, **kwargs: FakeYouTube())

    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_db_session] = lambda: db_session

    try:
        response = client.post(
            "/internal/run-poll",
            headers={"Authorization": "Bearer internal-secret"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    payload = response.json()
    assert sorted(payload.keys()) == sorted(
        [
            "run_outcome",
            "channels_processed",
            "channels_failed",
            "baselines_established",
            "new_videos_detected",
            "quota_blocked",
        ]
    )
    assert "channel_errors" not in payload


def test_run_poll_retryable_initial_email_failure_marks_pending_retry(db_session, monkeypatch) -> None:
    client = TestClient(app)
    settings = Settings(
        APP_SECRET_KEY="super-secret",
        INTERNAL_API_BEARER_TOKEN="internal-secret",
        POLL_QUOTA_DAILY_BUDGET=50,
        POLL_QUOTA_SAFETY_STOP_ENABLED=True,
        GOOGLE_CLIENT_ID="client-id",
        GOOGLE_CLIENT_SECRET="client-secret",
        DATABASE_URL="sqlite://",
        EMAIL_DELIVERY_MODE="fake",
    )

    user = User(email="owner@example.com")
    channel = Channel(youtube_channel_id="channel-a", title="A", uploads_playlist_id="uploads-a")
    db_session.add_all([user, channel])
    db_session.flush()
    db_session.add(
        UserChannel(
            user_id=user.id,
            channel_id=channel.id,
            is_monitored=True,
            last_seen_video_id="video-old",
            baseline_established_at=datetime.now(UTC) - timedelta(days=1),
        )
    )
    db_session.add(
        OAuthAccount(
            user_id=user.id,
            provider="google",
            access_token="token",
            refresh_token="refresh",
            token_expiry=datetime.now(UTC) + timedelta(hours=1),
        )
    )
    db_session.commit()

    monkeypatch.setattr(
        polling_module.GoogleOAuthService,
        "ensure_valid_credentials",
        lambda self, session, oauth_account: object(),  # noqa: ARG005
    )

    class FakeRequest:
        def execute(self):
            return {
                "items": [
                    {
                        "snippet": {"title": "Brand New Video", "publishedAt": "2026-04-25T12:00:00Z"},
                        "contentDetails": {"videoId": "video-new"},
                    }
                ]
            }

    class FakePlaylistItemsResource:
        def list(self, **kwargs):  # noqa: ARG002
            return FakeRequest()

    class FakeYouTube:
        def playlistItems(self):
            return FakePlaylistItemsResource()

    monkeypatch.setattr(polling_module, "build", lambda *args, **kwargs: FakeYouTube())

    def fake_send_failure(self, payload):  # noqa: ANN001, ARG001
        raise EmailDeliveryAttemptError("transient email error", retryable=True)

    monkeypatch.setattr(polling_module.EmailDeliveryService, "send_video_notification", fake_send_failure)

    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_db_session] = lambda: db_session

    try:
        response = client.post(
            "/internal/run-poll",
            headers={"Authorization": "Bearer internal-secret"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["channels_failed"] == 0
    assert response.json()["new_videos_detected"] == 1

    delivery = db_session.query(NotificationDelivery).one()
    assert delivery.status == "pending_retry"
    assert delivery.attempt_count == 1
    assert delivery.last_attempt_at is not None
    assert delivery.last_error == "transient email error"

    polling_state = db_session.query(SyncState).filter_by(process_type=POLLING_PROCESS).one()
    assert polling_state.state_metadata["channels_failed"] == 0
    assert polling_state.state_metadata["new_videos_detected"] == 1


def test_run_poll_permanent_initial_email_failure_marks_failed(db_session, monkeypatch) -> None:
    client = TestClient(app)
    settings = Settings(
        APP_SECRET_KEY="super-secret",
        INTERNAL_API_BEARER_TOKEN="internal-secret",
        POLL_QUOTA_DAILY_BUDGET=50,
        POLL_QUOTA_SAFETY_STOP_ENABLED=True,
        GOOGLE_CLIENT_ID="client-id",
        GOOGLE_CLIENT_SECRET="client-secret",
        DATABASE_URL="sqlite://",
        EMAIL_DELIVERY_MODE="fake",
    )

    user = User(email="owner@example.com")
    channel = Channel(youtube_channel_id="channel-a", title="A", uploads_playlist_id="uploads-a")
    db_session.add_all([user, channel])
    db_session.flush()
    db_session.add(
        UserChannel(
            user_id=user.id,
            channel_id=channel.id,
            is_monitored=True,
            last_seen_video_id="video-old",
            baseline_established_at=datetime.now(UTC) - timedelta(days=1),
        )
    )
    db_session.add(
        OAuthAccount(
            user_id=user.id,
            provider="google",
            access_token="token",
            refresh_token="refresh",
            token_expiry=datetime.now(UTC) + timedelta(hours=1),
        )
    )
    db_session.commit()

    monkeypatch.setattr(
        polling_module.GoogleOAuthService,
        "ensure_valid_credentials",
        lambda self, session, oauth_account: object(),  # noqa: ARG005
    )

    class FakeRequest:
        def execute(self):
            return {
                "items": [
                    {
                        "snippet": {"title": "Brand New Video", "publishedAt": "2026-04-25T12:00:00Z"},
                        "contentDetails": {"videoId": "video-new"},
                    }
                ]
            }

    class FakePlaylistItemsResource:
        def list(self, **kwargs):  # noqa: ARG002
            return FakeRequest()

    class FakeYouTube:
        def playlistItems(self):
            return FakePlaylistItemsResource()

    monkeypatch.setattr(polling_module, "build", lambda *args, **kwargs: FakeYouTube())

    def fake_send_failure(self, payload):  # noqa: ANN001, ARG001
        raise EmailDeliveryAttemptError("invalid recipient", retryable=False)

    monkeypatch.setattr(polling_module.EmailDeliveryService, "send_video_notification", fake_send_failure)

    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_db_session] = lambda: db_session

    try:
        response = client.post(
            "/internal/run-poll",
            headers={"Authorization": "Bearer internal-secret"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200

    delivery = db_session.query(NotificationDelivery).one()
    assert delivery.status == "failed"
    assert delivery.attempt_count == 1
    assert delivery.last_attempt_at is not None
    assert delivery.last_error == "invalid recipient"


def test_run_poll_retries_pending_retry_once_and_marks_delivered(db_session, monkeypatch) -> None:
    client = TestClient(app)
    settings = Settings(
        APP_SECRET_KEY="super-secret",
        INTERNAL_API_BEARER_TOKEN="internal-secret",
        POLL_QUOTA_DAILY_BUDGET=50,
        POLL_QUOTA_SAFETY_STOP_ENABLED=True,
        GOOGLE_CLIENT_ID="client-id",
        GOOGLE_CLIENT_SECRET="client-secret",
        DATABASE_URL="sqlite://",
        EMAIL_DELIVERY_MODE="fake",
    )

    user = User(email="owner@example.com")
    channel = Channel(youtube_channel_id="channel-a", title="A", uploads_playlist_id="uploads-a")
    db_session.add_all([user, channel])
    db_session.flush()

    video = Video(
        youtube_video_id="video-a",
        channel_id=channel.id,
        title="Video A",
        published_at=datetime.now(UTC),
    )
    db_session.add(video)
    db_session.flush()

    delivery = NotificationDelivery(
        user_id=user.id,
        video_id=video.id,
        status="pending_retry",
        attempt_count=1,
        last_error="timeout",
    )
    db_session.add(delivery)
    db_session.add(
        OAuthAccount(
            user_id=user.id,
            provider="google",
            access_token="token",
            refresh_token="refresh",
            token_expiry=datetime.now(UTC) + timedelta(hours=1),
        )
    )
    db_session.commit()

    monkeypatch.setattr(
        polling_module.GoogleOAuthService,
        "ensure_valid_credentials",
        lambda self, session, oauth_account: object(),  # noqa: ARG005
    )

    class FakeYouTube:
        def playlistItems(self):  # pragma: no cover
            raise AssertionError("No monitored channels should be polled in this test")

    monkeypatch.setattr(polling_module, "build", lambda *args, **kwargs: FakeYouTube())

    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_db_session] = lambda: db_session

    try:
        response = client.post(
            "/internal/run-poll",
            headers={"Authorization": "Bearer internal-secret"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200

    refreshed = db_session.query(NotificationDelivery).one()
    assert refreshed.status == "delivered"
    assert refreshed.attempt_count == 2
    assert refreshed.last_attempt_at is not None
    assert refreshed.last_error is None


def test_run_poll_processes_existing_pending_delivery_without_new_video(db_session, monkeypatch) -> None:
    client = TestClient(app)
    settings = Settings(
        APP_SECRET_KEY="super-secret",
        INTERNAL_API_BEARER_TOKEN="internal-secret",
        POLL_QUOTA_DAILY_BUDGET=50,
        POLL_QUOTA_SAFETY_STOP_ENABLED=True,
        GOOGLE_CLIENT_ID="client-id",
        GOOGLE_CLIENT_SECRET="client-secret",
        DATABASE_URL="sqlite://",
        EMAIL_DELIVERY_MODE="fake",
    )

    user = User(email="owner@example.com")
    channel = Channel(youtube_channel_id="channel-a", title="A", uploads_playlist_id="uploads-a")
    db_session.add_all([user, channel])
    db_session.flush()

    video = Video(
        youtube_video_id="video-already-seen",
        channel_id=channel.id,
        title="Already Seen Video",
        published_at=datetime.now(UTC),
    )
    db_session.add(video)
    db_session.flush()

    db_session.add(
        UserChannel(
            user_id=user.id,
            channel_id=channel.id,
            is_monitored=True,
            last_seen_video_id="video-already-seen",
            baseline_established_at=datetime.now(UTC) - timedelta(days=1),
        )
    )
    db_session.add(
        NotificationDelivery(
            user_id=user.id,
            video_id=video.id,
            status="pending",
            attempt_count=0,
        )
    )
    db_session.add(
        OAuthAccount(
            user_id=user.id,
            provider="google",
            access_token="token",
            refresh_token="refresh",
            token_expiry=datetime.now(UTC) + timedelta(hours=1),
        )
    )
    db_session.commit()

    monkeypatch.setattr(
        polling_module.GoogleOAuthService,
        "ensure_valid_credentials",
        lambda self, session, oauth_account: object(),  # noqa: ARG005
    )

    class FakeRequest:
        def execute(self):
            return {
                "items": [
                    {
                        "snippet": {"title": "Already Seen Video", "publishedAt": "2026-04-25T12:00:00Z"},
                        "contentDetails": {"videoId": "video-already-seen"},
                    }
                ]
            }

    class FakePlaylistItemsResource:
        def list(self, **kwargs):  # noqa: ARG002
            return FakeRequest()

    class FakeYouTube:
        def playlistItems(self):
            return FakePlaylistItemsResource()

    monkeypatch.setattr(polling_module, "build", lambda *args, **kwargs: FakeYouTube())

    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_db_session] = lambda: db_session

    try:
        response = client.post(
            "/internal/run-poll",
            headers={"Authorization": "Bearer internal-secret"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["new_videos_detected"] == 0

    delivery = db_session.query(NotificationDelivery).one()
    assert delivery.status == "delivered"
    assert delivery.attempt_count == 1
    assert delivery.last_attempt_at is not None
    assert delivery.last_error is None


def test_run_poll_failed_retry_marks_delivery_failed(db_session, monkeypatch) -> None:
    client = TestClient(app)
    settings = Settings(
        APP_SECRET_KEY="super-secret",
        INTERNAL_API_BEARER_TOKEN="internal-secret",
        POLL_QUOTA_DAILY_BUDGET=50,
        POLL_QUOTA_SAFETY_STOP_ENABLED=True,
        GOOGLE_CLIENT_ID="client-id",
        GOOGLE_CLIENT_SECRET="client-secret",
        DATABASE_URL="sqlite://",
        EMAIL_DELIVERY_MODE="fake",
    )

    user = User(email="owner@example.com")
    channel = Channel(youtube_channel_id="channel-a", title="A", uploads_playlist_id="uploads-a")
    db_session.add_all([user, channel])
    db_session.flush()

    video = Video(
        youtube_video_id="video-a",
        channel_id=channel.id,
        title="Video A",
        published_at=datetime.now(UTC),
    )
    db_session.add(video)
    db_session.flush()

    delivery = NotificationDelivery(
        user_id=user.id,
        video_id=video.id,
        status="pending_retry",
        attempt_count=1,
        last_error="timeout",
    )
    db_session.add(delivery)
    db_session.add(
        OAuthAccount(
            user_id=user.id,
            provider="google",
            access_token="token",
            refresh_token="refresh",
            token_expiry=datetime.now(UTC) + timedelta(hours=1),
        )
    )
    db_session.commit()

    monkeypatch.setattr(
        polling_module.GoogleOAuthService,
        "ensure_valid_credentials",
        lambda self, session, oauth_account: object(),  # noqa: ARG005
    )

    class FakeYouTube:
        def playlistItems(self):  # pragma: no cover
            raise AssertionError("No monitored channels should be polled in this test")

    monkeypatch.setattr(polling_module, "build", lambda *args, **kwargs: FakeYouTube())

    def fake_send_failure(self, payload):  # noqa: ANN001, ARG001
        raise EmailDeliveryAttemptError("retry failed", retryable=True)

    monkeypatch.setattr(polling_module.EmailDeliveryService, "send_video_notification", fake_send_failure)

    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_db_session] = lambda: db_session

    try:
        response = client.post(
            "/internal/run-poll",
            headers={"Authorization": "Bearer internal-secret"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200

    refreshed = db_session.query(NotificationDelivery).one()
    assert refreshed.status == "failed"
    assert refreshed.attempt_count == 2
    assert refreshed.last_attempt_at is not None
    assert refreshed.last_error == "retry failed"


def test_run_poll_blocks_fake_email_mode_in_production(db_session) -> None:
    client = TestClient(app)
    settings = Settings(
        APP_SECRET_KEY="super-secret",
        INTERNAL_API_BEARER_TOKEN="internal-secret",
        APP_ENV="production",
        EMAIL_DELIVERY_MODE="fake",
        GOOGLE_CLIENT_ID="client-id",
        GOOGLE_CLIENT_SECRET="client-secret",
        DATABASE_URL="sqlite://",
    )

    user = User(email="owner@example.com")
    db_session.add(user)
    db_session.flush()
    db_session.add(
        OAuthAccount(
            user_id=user.id,
            provider="google",
            access_token="token",
            refresh_token="refresh",
            token_expiry=datetime.now(UTC) + timedelta(hours=1),
        )
    )
    db_session.commit()

    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_db_session] = lambda: db_session

    try:
        response = client.post(
            "/internal/run-poll",
            headers={"Authorization": "Bearer internal-secret"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 502
    assert response.json()["detail"] == "Polling run failed. Inspect service logs or stored sync state for details."


def test_run_poll_detects_short_by_title_hashtag(db_session, monkeypatch) -> None:
    client = TestClient(app)
    settings = Settings(
        APP_SECRET_KEY="super-secret",
        INTERNAL_API_BEARER_TOKEN="internal-secret",
        POLL_QUOTA_DAILY_BUDGET=50,
        POLL_QUOTA_SAFETY_STOP_ENABLED=True,
        GOOGLE_CLIENT_ID="client-id",
        GOOGLE_CLIENT_SECRET="client-secret",
        DATABASE_URL="sqlite://",
    )

    user = User(email="owner@example.com")
    channel = Channel(youtube_channel_id="channel-a", title="A", uploads_playlist_id="uploads-a")
    db_session.add_all([user, channel])
    db_session.flush()
    db_session.add(
        UserChannel(
            user_id=user.id,
            channel_id=channel.id,
            is_monitored=True,
            last_seen_video_id="video-old",
            baseline_established_at=datetime.now(UTC) - timedelta(days=1),
        )
    )
    db_session.add(
        OAuthAccount(
            user_id=user.id,
            provider="google",
            access_token="token",
            refresh_token="refresh",
            token_expiry=datetime.now(UTC) + timedelta(hours=1),
        )
    )
    db_session.commit()

    monkeypatch.setattr(
        polling_module.GoogleOAuthService,
        "ensure_valid_credentials",
        lambda self, session, oauth_account: object(),  # noqa: ARG005
    )

    class FakeRequest:
        def execute(self):
            return {
                "items": [
                    {
                        "snippet": {"title": "Cool Short #shorts", "publishedAt": "2026-04-25T12:00:00Z"},
                        "contentDetails": {"videoId": "video-short"},
                    }
                ]
            }

    class FakePlaylistItemsResource:
        def list(self, **kwargs):  # noqa: ARG002
            return FakeRequest()

    class FakeYouTube:
        def playlistItems(self):
            return FakePlaylistItemsResource()

    monkeypatch.setattr(polling_module, "build", lambda *args, **kwargs: FakeYouTube())

    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_db_session] = lambda: db_session

    try:
        response = client.post(
            "/internal/run-poll",
            headers={"Authorization": "Bearer internal-secret"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    video = db_session.query(Video).one()
    assert video.youtube_video_id == "video-short"
    assert video.is_short is True


def test_run_poll_detects_short_by_video_duration(db_session, monkeypatch) -> None:
    client = TestClient(app)
    settings = Settings(
        APP_SECRET_KEY="super-secret",
        INTERNAL_API_BEARER_TOKEN="internal-secret",
        POLL_QUOTA_DAILY_BUDGET=50,
        POLL_QUOTA_SAFETY_STOP_ENABLED=True,
        GOOGLE_CLIENT_ID="client-id",
        GOOGLE_CLIENT_SECRET="client-secret",
        DATABASE_URL="sqlite://",
    )

    user = User(email="owner@example.com")
    channel = Channel(youtube_channel_id="channel-a", title="A", uploads_playlist_id="uploads-a")
    db_session.add_all([user, channel])
    db_session.flush()
    db_session.add(
        UserChannel(
            user_id=user.id,
            channel_id=channel.id,
            is_monitored=True,
            last_seen_video_id="video-old",
            baseline_established_at=datetime.now(UTC) - timedelta(days=1),
        )
    )
    db_session.add(
        OAuthAccount(
            user_id=user.id,
            provider="google",
            access_token="token",
            refresh_token="refresh",
            token_expiry=datetime.now(UTC) + timedelta(hours=1),
        )
    )
    db_session.commit()

    monkeypatch.setattr(
        polling_module.GoogleOAuthService,
        "ensure_valid_credentials",
        lambda self, session, oauth_account: object(),  # noqa: ARG005
    )

    class PlaylistItemsRequest:
        def execute(self):
            return {
                "items": [
                    {
                        "snippet": {"title": "Cool Short Without Hashtag", "publishedAt": "2026-04-25T12:00:00Z"},
                        "contentDetails": {"videoId": "video-short"},
                    }
                ]
            }

    class VideosRequest:
        def execute(self):
            return {
                "items": [
                    {
                        "contentDetails": {"duration": "PT0M45S"},
                    }
                ]
            }

    class FakePlaylistItemsResource:
        def list(self, **kwargs):  # noqa: ARG002
            return PlaylistItemsRequest()

    class FakeVideosResource:
        def list(self, **kwargs):  # noqa: ARG002
            return VideosRequest()

    class FakeYouTube:
        def playlistItems(self):
            return FakePlaylistItemsResource()

        def videos(self):
            return FakeVideosResource()

    monkeypatch.setattr(polling_module, "build", lambda *args, **kwargs: FakeYouTube())

    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_db_session] = lambda: db_session

    try:
        response = client.post(
            "/internal/run-poll",
            headers={"Authorization": "Bearer internal-secret"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    video = db_session.query(Video).one()
    assert video.youtube_video_id == "video-short"
    assert video.is_short is True


def test_run_poll_skips_disabled_short_processing(db_session, monkeypatch) -> None:
    client = TestClient(app)
    settings = Settings(
        APP_SECRET_KEY="super-secret",
        INTERNAL_API_BEARER_TOKEN="internal-secret",
        POLL_QUOTA_DAILY_BUDGET=50,
        POLL_QUOTA_SAFETY_STOP_ENABLED=True,
        SHORTS_PROCESSING_ENABLED=False,
        GOOGLE_CLIENT_ID="client-id",
        GOOGLE_CLIENT_SECRET="client-secret",
        DATABASE_URL="sqlite://",
    )

    user = User(email="owner@example.com")
    channel = Channel(youtube_channel_id="channel-a", title="A", uploads_playlist_id="uploads-a")
    db_session.add_all([user, channel])
    db_session.flush()
    user_channel = UserChannel(
        user_id=user.id,
        channel_id=channel.id,
        is_monitored=True,
        last_seen_video_id="video-old",
        baseline_established_at=datetime.now(UTC) - timedelta(days=1),
    )
    db_session.add(user_channel)
    db_session.add(
        OAuthAccount(
            user_id=user.id,
            provider="google",
            access_token="token",
            refresh_token="refresh",
            token_expiry=datetime.now(UTC) + timedelta(hours=1),
        )
    )
    db_session.commit()

    monkeypatch.setattr(
        polling_module.GoogleOAuthService,
        "ensure_valid_credentials",
        lambda self, session, oauth_account: object(),  # noqa: ARG005
    )

    class FakeRequest:
        def execute(self):
            return {
                "items": [
                    {
                        "snippet": {"title": "Ignored Short #shorts", "publishedAt": "2026-04-25T12:00:00Z"},
                        "contentDetails": {"videoId": "video-short"},
                    }
                ]
            }

    class FakePlaylistItemsResource:
        def list(self, **kwargs):  # noqa: ARG002
            return FakeRequest()

    class FakeYouTube:
        def playlistItems(self):
            return FakePlaylistItemsResource()

    monkeypatch.setattr(polling_module, "build", lambda *args, **kwargs: FakeYouTube())

    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_db_session] = lambda: db_session

    try:
        response = client.post(
            "/internal/run-poll",
            headers={"Authorization": "Bearer internal-secret"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    video = db_session.query(Video).one()
    assert video.is_short is True
    assert db_session.query(NotificationDelivery).count() == 0
    assert db_session.query(PipelineStage).count() == 0
    assert user_channel.last_seen_video_id == "video-short"


def test_run_poll_quota_alert_sent_at_threshold(db_session, monkeypatch) -> None:
    client = TestClient(app)
    settings = Settings(
        APP_SECRET_KEY="super-secret",
        INTERNAL_API_BEARER_TOKEN="internal-secret",
        POLL_QUOTA_DAILY_BUDGET=2,
        POLL_QUOTA_SAFETY_STOP_ENABLED=True,
        GOOGLE_CLIENT_ID="client-id",
        GOOGLE_CLIENT_SECRET="client-secret",
        DATABASE_URL="sqlite://",
        TELEGRAM_NOTIFICATIONS_ENABLED=True,
        TELEGRAM_BOT_TOKEN="bot-token",
        TELEGRAM_CHAT_ID="chat-id",
    )

    user = User(email="owner@example.com")
    channel = Channel(youtube_channel_id="channel-a", title="A", uploads_playlist_id="uploads-a")
    db_session.add_all([user, channel])
    db_session.flush()
    db_session.add(
        UserChannel(
            user_id=user.id,
            channel_id=channel.id,
            is_monitored=True,
            last_seen_video_id="video-old",
            baseline_established_at=datetime.now(UTC) - timedelta(days=1),
        )
    )
    db_session.add(
        OAuthAccount(
            user_id=user.id,
            provider="google",
            access_token="token",
            refresh_token="refresh",
            token_expiry=datetime.now(UTC) + timedelta(hours=1),
        )
    )
    db_session.commit()

    monkeypatch.setattr(
        polling_module.GoogleOAuthService,
        "ensure_valid_credentials",
        lambda self, session, oauth_account: object(),  # noqa: ARG005
    )
    monkeypatch.setattr(
        TelegramDeliveryService,
        "send_video_notification",
        lambda self, payload: None,
    )

    sent_messages: list[str] = []
    monkeypatch.setattr(
        TelegramDeliveryService,
        "send_message",
        lambda self, text: sent_messages.append(text),
    )

    class FakeRequest:
        def execute(self):
            return {
                "items": [
                    {
                        "snippet": {"title": "Cool Short #shorts", "publishedAt": "2026-04-25T12:00:00Z"},
                        "contentDetails": {"videoId": "video-new"},
                    }
                ]
            }

    class FakePlaylistItemsResource:
        def list(self, **kwargs):  # noqa: ARG002
            return FakeRequest()

    class FakeYouTube:
        def playlistItems(self):
            return FakePlaylistItemsResource()

    monkeypatch.setattr(polling_module, "build", lambda *args, **kwargs: FakeYouTube())

    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_db_session] = lambda: db_session

    try:
        response = client.post(
            "/internal/run-poll",
            headers={"Authorization": "Bearer internal-secret"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert len(sent_messages) == 1
    assert "Quota alert: 50%" in sent_messages[0]
    assert "1/2" in sent_messages[0]

    quota_state = db_session.query(SyncState).filter_by(process_type=QUOTA_PROCESS).one()
    assert quota_state.state_metadata is not None
    assert quota_state.state_metadata["quota_alerts_sent"]["thresholds"] == [50]


def test_check_and_send_quota_alert_does_not_duplicate_within_24_hours(db_session, monkeypatch) -> None:
    settings = Settings(
        APP_SECRET_KEY="super-secret",
        GOOGLE_CLIENT_ID="client-id",
        GOOGLE_CLIENT_SECRET="client-secret",
        DATABASE_URL="sqlite://",
        TELEGRAM_NOTIFICATIONS_ENABLED=True,
        TELEGRAM_BOT_TOKEN="bot-token",
        TELEGRAM_CHAT_ID="chat-id",
    )

    user = User(email="owner@example.com")
    db_session.add(user)
    db_session.commit()

    telegram_service = TelegramDeliveryService(settings)
    sent_messages: list[str] = []
    monkeypatch.setattr(telegram_service, "send_message", lambda text: sent_messages.append(text))

    polling_service = YouTubePollingService(
        auth_service=object(),  # not used by the method under test
        email_service=object(),  # not used by the method under test
        daily_quota_budget=100,
        safety_stop_enabled=True,
        telegram_service=telegram_service,
    )

    today = datetime.now(UTC).date().isoformat()
    quota_context = {
        "daily_quota_budget": 100,
        "estimated_units_used_today": 50,
        "usage_day": today,
        "quota_alerts_sent": {"day": today, "thresholds": []},
    }

    polling_service._check_and_send_quota_alert(db_session, user, quota_context)
    assert len(sent_messages) == 1
    assert "Quota alert: 50%" in sent_messages[0]
    assert quota_context["quota_alerts_sent"]["thresholds"] == [50]

    polling_service._check_and_send_quota_alert(db_session, user, quota_context)
    assert len(sent_messages) == 1
    assert quota_context["quota_alerts_sent"]["thresholds"] == [50]


def test_summary_circuit_alert_persists_and_restarts_once(db_session, monkeypatch) -> None:
    settings = Settings(
        APP_SECRET_KEY="super-secret",
        GOOGLE_CLIENT_ID="client-id",
        GOOGLE_CLIENT_SECRET="client-secret",
        DATABASE_URL="sqlite://",
        TELEGRAM_NOTIFICATIONS_ENABLED=True,
        TELEGRAM_BOT_TOKEN="bot-token",
        TELEGRAM_CHAT_ID="chat-id",
    )
    user = User(email="owner@example.com")
    channel = Channel(youtube_channel_id="UC-CIRCUIT", title="Circuit Channel")
    db_session.add_all([user, channel])
    db_session.flush()
    video = Video(
        youtube_video_id="circuit-video",
        channel_id=channel.id,
        title="Circuit Video",
    )
    db_session.add(video)
    db_session.commit()

    pipeline_service = polling_module.PipelineService()
    pipeline_service.summary_paused = True
    pipeline_service.summary_pause_reason = "Summarization server returned HTTP 500: Vulkan device lost."
    pipeline_service.summary_pause_video_id = video.id
    telegram_service = TelegramDeliveryService(settings)
    sent_messages: list[str] = []
    monkeypatch.setattr(telegram_service, "send_message", lambda text: sent_messages.append(text))
    restart_service = type(
        "FakeRecovery",
        (),
        {
            "enabled": True,
            "cooldown_seconds": 300,
            "restart": lambda self: type("Result", (), {"succeeded": True, "reason": "restart requested"})(),
        },
    )()
    polling_service = YouTubePollingService(
        auth_service=object(),
        email_service=object(),
        daily_quota_budget=100,
        safety_stop_enabled=True,
        telegram_service=telegram_service,
        pipeline_service=pipeline_service,
        llama_recovery_service=restart_service,
    )
    summarization_state = SyncState(user_id=user.id, process_type=SUMMARIZATION_PROCESS)
    db_session.add(summarization_state)
    db_session.flush()

    polling_service._finalize_summary_circuit(
        db_session,
        user,
        summarization_state,
        datetime.now(UTC),
    )
    assert len(sent_messages) == 1
    assert "Vulkan device lost" in sent_messages[0]
    assert summarization_state.state_metadata["paused"] is True
    assert summarization_state.state_metadata["restart_succeeded"] is True

    polling_service._finalize_summary_circuit(
        db_session,
        user,
        summarization_state,
        datetime.now(UTC),
    )
    assert len(sent_messages) == 1
