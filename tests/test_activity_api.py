from __future__ import annotations

from datetime import UTC, datetime

from fastapi.testclient import TestClient

from app.api.dependencies import get_settings
from app.core.settings import Settings
from app.db.session import get_db_session
from app.main import app
from app.models.channel import Channel
from app.models.notification_delivery import NotificationDelivery
from app.models.user import User
from app.models.video import Video


def _seed_activity_data(db_session):
    user = User(email="owner@example.com")
    channel = Channel(youtube_channel_id="channel-abc", title="Alpha Channel", uploads_playlist_id="uploads-abc")
    db_session.add_all([user, channel])
    db_session.flush()

    video_delivered = Video(
        youtube_video_id="video-delivered",
        channel_id=channel.id,
        title="Delivered video",
        published_at=datetime(2026, 5, 1, 10, 0, tzinfo=UTC),
    )
    video_failed = Video(
        youtube_video_id="video-failed",
        channel_id=channel.id,
        title="Failed video",
        published_at=datetime(2026, 5, 1, 11, 0, tzinfo=UTC),
    )
    db_session.add_all([video_delivered, video_failed])
    db_session.flush()

    db_session.add_all(
        [
            NotificationDelivery(
                user_id=user.id,
                video_id=video_delivered.id,
                status="delivered",
                attempt_count=1,
                last_attempt_at=datetime(2026, 5, 1, 10, 5, tzinfo=UTC),
            ),
            NotificationDelivery(
                user_id=user.id,
                video_id=video_failed.id,
                status="failed",
                attempt_count=2,
                last_attempt_at=datetime(2026, 5, 1, 11, 5, tzinfo=UTC),
                last_error="provider rejected recipient",
            ),
        ]
    )
    db_session.commit()
    return user


def _settings() -> Settings:
    return Settings(
        APP_SECRET_KEY="super-secret",
        INTERNAL_API_BEARER_TOKEN="internal-secret",
        MOBILE_API_BEARER_TOKEN="mobile-secret",
        DATABASE_URL="sqlite://",
    )


def test_activity_endpoint_requires_bearer_token(db_session) -> None:
    client = TestClient(app)
    app.dependency_overrides[get_settings] = _settings
    app.dependency_overrides[get_db_session] = lambda: db_session

    try:
        response = client.get("/internal/activity")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 401


def test_activity_default_listing_and_url_generation(db_session) -> None:
    client = TestClient(app)
    _seed_activity_data(db_session)

    app.dependency_overrides[get_settings] = _settings
    app.dependency_overrides[get_db_session] = lambda: db_session

    try:
        response = client.get(
            "/internal/activity",
            headers={"Authorization": "Bearer mobile-secret"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    payload = response.json()
    assert payload["pagination"] == {"limit": 50, "offset": 0, "total": 2}
    assert len(payload["items"]) == 2
    assert payload["items"][0]["delivery_status"] == "failed"
    assert payload["items"][0]["youtube_url"] == "https://www.youtube.com/watch?v=video-failed"
    assert payload["items"][0]["last_error"] == "provider rejected recipient"
    assert payload["items"][1]["delivery_status"] == "delivered"
    assert "last_error" not in payload["items"][1]


def test_activity_status_filter_and_pagination(db_session) -> None:
    client = TestClient(app)
    _seed_activity_data(db_session)

    app.dependency_overrides[get_settings] = _settings
    app.dependency_overrides[get_db_session] = lambda: db_session

    try:
        delivered_response = client.get(
            "/internal/activity?status=delivered&limit=1&offset=0",
            headers={"Authorization": "Bearer internal-secret"},
        )
        failed_response = client.get(
            "/internal/activity?status=failed",
            headers={"Authorization": "Bearer internal-secret"},
        )
    finally:
        app.dependency_overrides.clear()

    assert delivered_response.status_code == 200
    delivered_payload = delivered_response.json()
    assert delivered_payload["pagination"] == {"limit": 1, "offset": 0, "total": 1}
    assert [item["delivery_status"] for item in delivered_payload["items"]] == ["delivered"]

    assert failed_response.status_code == 200
    failed_payload = failed_response.json()
    assert failed_payload["pagination"] == {"limit": 50, "offset": 0, "total": 1}
    assert failed_payload["items"][0]["delivery_status"] == "failed"
    assert failed_payload["items"][0]["last_error"] == "provider rejected recipient"
