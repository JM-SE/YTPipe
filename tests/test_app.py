from datetime import UTC, datetime

from fastapi.testclient import TestClient

from app.api.routes.status import get_db_session, get_settings
from app.core.settings import Settings
from app.main import app
from app.models.channel import Channel
from app.models.notification_delivery import NotificationDelivery
from app.models.sync_state import SyncState
from app.models.user import User
from app.models.user_channel import UserChannel
from app.models.video import Video
from app.services.polling import POLLING_PROCESS, QUOTA_PROCESS
from app.services.subscriptions import SUBSCRIPTION_SYNC_PROCESS


def test_health_endpoint_returns_ok() -> None:
    client = TestClient(app)
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_status_endpoint_requires_internal_bearer_token(db_session) -> None:
    client = TestClient(app)
    settings = Settings(
        APP_SECRET_KEY="super-secret",
        INTERNAL_API_BEARER_TOKEN="internal-secret",
        DATABASE_URL="sqlite://",
    )
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_db_session] = lambda: db_session

    try:
        response = client.get("/status")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 401


def test_status_endpoint_returns_operational_summary(db_session) -> None:
    client = TestClient(app)
    settings = Settings(
        APP_SECRET_KEY="super-secret",
        INTERNAL_API_BEARER_TOKEN="internal-secret",
        DATABASE_URL="sqlite://",
    )
    now = datetime(2026, 4, 27, 18, 0, tzinfo=UTC)

    user = User(email="owner@example.com")
    channel_a = Channel(youtube_channel_id="channel-a", title="A", uploads_playlist_id="uploads-a")
    channel_b = Channel(youtube_channel_id="channel-b", title="B", uploads_playlist_id="uploads-b")
    db_session.add_all([user, channel_a, channel_b])
    db_session.flush()
    db_session.add_all(
        [
            UserChannel(user_id=user.id, channel_id=channel_a.id, is_monitored=True),
            UserChannel(user_id=user.id, channel_id=channel_b.id, is_monitored=False),
            SyncState(
                user_id=user.id,
                process_type=SUBSCRIPTION_SYNC_PROCESS,
                last_success_at=now,
                state_metadata={"imported_channels": 2},
            ),
            SyncState(
                user_id=user.id,
                process_type=POLLING_PROCESS,
                last_success_at=now,
                state_metadata={
                    "run_outcome": "success",
                    "channels_processed": 1,
                    "channels_failed": 0,
                    "baselines_established": 0,
                    "new_videos_detected": 1,
                    "quota_blocked": False,
                    "channel_errors": [],
                },
            ),
            SyncState(
                user_id=user.id,
                process_type=QUOTA_PROCESS,
                last_success_at=now,
                state_metadata={
                    "daily_quota_budget": 500,
                    "estimated_units_used_today": 7,
                    "last_run_estimated_units": 1,
                    "safety_stop_active": False,
                    "safety_stop_enabled": True,
                    "safety_stop_triggered_at": None,
                },
            ),
        ]
    )
    db_session.flush()
    video_delivered = Video(youtube_video_id="video-1", channel_id=channel_a.id, title="Delivered")
    video_retry = Video(youtube_video_id="video-2", channel_id=channel_a.id, title="Retry")
    video_failed = Video(youtube_video_id="video-3", channel_id=channel_a.id, title="Failed")
    db_session.add_all([video_delivered, video_retry, video_failed])
    db_session.flush()
    db_session.add_all(
        [
            NotificationDelivery(
                user_id=user.id,
                video_id=video_delivered.id,
                status="delivered",
                attempt_count=1,
                last_attempt_at=now,
            ),
            NotificationDelivery(
                user_id=user.id,
                video_id=video_retry.id,
                status="pending_retry",
                attempt_count=1,
                last_attempt_at=now,
                last_error="temporary provider failure",
            ),
            NotificationDelivery(
                user_id=user.id,
                video_id=video_failed.id,
                status="failed",
                attempt_count=1,
                last_attempt_at=now,
                last_error="permanent provider failure",
            ),
        ]
    )
    db_session.commit()

    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_db_session] = lambda: db_session

    try:
        response = client.get("/status", headers={"Authorization": "Bearer internal-secret"})
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    payload = response.json()
    assert set(payload) == {
        "service",
        "environment",
        "ready",
        "subscription_sync",
        "polling",
        "email",
        "quota",
        "channels",
    }
    assert set(payload["subscription_sync"]) == {
        "last_success_at",
        "last_error_at",
        "last_error_message",
        "metadata",
    }
    assert set(payload["polling"]) == {
        "last_success_at",
        "last_error_at",
        "last_error_message",
        "last_run",
    }
    assert set(payload["polling"]["last_run"]) == {
        "run_outcome",
        "channels_processed",
        "channels_failed",
        "baselines_established",
        "new_videos_detected",
        "quota_blocked",
        "channel_errors",
    }
    assert set(payload["email"]) == {
        "last_attempt_at",
        "last_success_at",
        "last_failure_at",
        "last_error",
        "pending_count",
        "pending_retry_count",
        "delivered_count",
        "failed_count",
    }
    assert set(payload["quota"]) == {
        "daily_quota_budget",
        "estimated_units_used_today",
        "last_run_estimated_units",
        "safety_stop_active",
        "safety_stop_enabled",
        "safety_stop_triggered_at",
    }
    assert set(payload["channels"]) == {"imported_count", "monitored_count"}
    assert payload["service"] == settings.app_name
    assert payload["environment"] == settings.app_env
    assert payload["ready"] is True
    assert payload["subscription_sync"]["metadata"] == {"imported_channels": 2}
    assert payload["polling"]["last_run"] == {
        "run_outcome": "success",
        "channels_processed": 1,
        "channels_failed": 0,
        "baselines_established": 0,
        "new_videos_detected": 1,
        "quota_blocked": False,
        "channel_errors": [],
    }
    assert payload["email"]["delivered_count"] == 1
    assert payload["email"]["pending_retry_count"] == 1
    assert payload["email"]["failed_count"] == 1
    assert payload["email"]["last_error"] in {"temporary provider failure", "permanent provider failure"}
    assert payload["quota"]["daily_quota_budget"] == 500
    assert payload["quota"]["estimated_units_used_today"] == 7
    assert payload["channels"] == {"imported_count": 2, "monitored_count": 1}
