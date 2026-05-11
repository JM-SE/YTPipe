from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient

import app.services.polling as polling_module
from app.api.routes.polling import get_db_session, get_settings
from app.core.settings import Settings
from app.main import app
from app.models.channel import Channel
from app.models.mobile_push_channel_preference import MobilePushChannelPreference
from app.models.mobile_push_delivery import MobilePushDelivery
from app.models.mobile_push_installation import MobilePushInstallation
from app.models.mobile_push_setting import MobilePushSetting
from app.models.notification_delivery import NotificationDelivery
from app.models.oauth_account import OAuthAccount
from app.models.user import User
from app.models.user_channel import UserChannel
from app.models.video import Video
from app.services.email import EmailDeliveryAttemptError


INTERNAL_TOKEN = "internal-secret"
MOBILE_TOKEN = "mobile-secret"
EXPO_TOKEN = "ExponentPushToken[push-polling-device-token]"


def _settings(push_enabled: bool = True, quota_budget: int = 50) -> Settings:
    return Settings(
        APP_SECRET_KEY="super-secret",
        INTERNAL_API_BEARER_TOKEN=INTERNAL_TOKEN,
        MOBILE_API_BEARER_TOKEN=MOBILE_TOKEN,
        POLL_QUOTA_DAILY_BUDGET=quota_budget,
        POLL_QUOTA_SAFETY_STOP_ENABLED=True,
        GOOGLE_CLIENT_ID="client-id",
        GOOGLE_CLIENT_SECRET="client-secret",
        DATABASE_URL="sqlite://",
        PUSH_NOTIFICATIONS_ENABLED=push_enabled,
        EXPO_ACCESS_TOKEN="expo-provider-secret",
    )


def _headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {INTERNAL_TOKEN}"}


def _seed_poll_ready_owner(
    db_session,
    *,
    last_seen_video_id: str = "video-old",
    monitored: bool = True,
    baseline_established: bool = True,
) -> tuple[User, Channel, UserChannel]:
    user = User(email="owner@example.com")
    channel = Channel(youtube_channel_id="channel-a", title="Alpha", uploads_playlist_id="uploads-a")
    db_session.add_all([user, channel])
    db_session.flush()
    user_channel = UserChannel(
        user_id=user.id,
        channel_id=channel.id,
        is_monitored=monitored,
        last_seen_video_id=last_seen_video_id,
        baseline_established_at=(datetime.now(UTC) - timedelta(days=1)) if baseline_established else None,
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
    db_session.flush()
    return user, channel, user_channel


def _enable_push(db_session, user: User, *, default_for_monitored: bool = True) -> MobilePushSetting:
    settings = MobilePushSetting(
        user_id=user.id,
        enabled=True,
        default_for_monitored_channels=default_for_monitored,
        first_enabled_at=datetime.now(UTC),
    )
    db_session.add(settings)
    db_session.flush()
    return settings


def _add_installation(db_session, user: User, token: str = EXPO_TOKEN, **kwargs) -> MobilePushInstallation:
    installation = MobilePushInstallation(
        user_id=user.id,
        installation_id=uuid4(),
        expo_push_token=token,
        **kwargs,
    )
    db_session.add(installation)
    db_session.flush()
    return installation


def _patch_polling(monkeypatch, *, video_id: str | None = "video-new", title: str = "Brand New Video") -> None:
    monkeypatch.setattr(
        polling_module.GoogleOAuthService,
        "ensure_valid_credentials",
        lambda self, session, oauth_account: object(),  # noqa: ARG005
    )

    class FakeRequest:
        def execute(self):
            if video_id is None:
                return {"items": []}
            return {
                "items": [
                    {
                        "snippet": {"title": title, "publishedAt": "2026-04-25T12:00:00Z"},
                        "contentDetails": {"videoId": video_id},
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


def _run_poll(db_session, settings: Settings) -> TestClient:
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_db_session] = lambda: db_session
    return TestClient(app)


def test_new_video_push_success_records_safe_delivery(db_session, monkeypatch) -> None:
    user, channel, _user_channel = _seed_poll_ready_owner(db_session)
    _enable_push(db_session, user)
    installation = _add_installation(db_session, user)
    db_session.commit()
    _patch_polling(monkeypatch)
    captured: dict[str, object] = {}

    def fake_post(url, json, headers, timeout):
        captured.update({"url": url, "json": json, "headers": headers, "timeout": timeout})
        return httpx.Response(200, json={"data": {"status": "ok", "id": "ticket-new-video"}})

    monkeypatch.setattr("app.services.mobile_push.httpx.post", fake_post)
    client = _run_poll(db_session, _settings(push_enabled=True))

    try:
        response = client.post("/internal/run-poll", headers=_headers())
    finally:
        app.dependency_overrides.clear()

    push_delivery = db_session.query(MobilePushDelivery).one()
    notification_delivery = db_session.query(NotificationDelivery).one()
    db_session.refresh(installation)

    assert response.status_code == 200
    assert response.json() == {
        "run_outcome": "success",
        "channels_processed": 1,
        "channels_failed": 0,
        "baselines_established": 0,
        "new_videos_detected": 1,
        "quota_blocked": False,
    }
    assert captured["json"]["to"] == EXPO_TOKEN
    assert captured["json"]["data"] == {
        "type": "new_video",
        "activity_id": notification_delivery.id,
        "delivery_id": notification_delivery.id,
        "video_id": db_session.query(Video).one().id,
        "channel_id": channel.id,
        "sent_at": captured["json"]["data"]["sent_at"],
    }
    assert EXPO_TOKEN not in str(captured["json"]["data"])
    assert push_delivery.event_type == "new_video"
    assert push_delivery.status == "sent"
    assert push_delivery.notification_delivery_id == notification_delivery.id
    assert push_delivery.video_id == db_session.query(Video).one().id
    assert push_delivery.channel_id == channel.id
    assert push_delivery.expo_ticket_id == "ticket-new-video"
    assert installation.last_success_at is not None


def test_new_video_push_fans_out_and_is_idempotent_for_existing_sent_rows(db_session, monkeypatch) -> None:
    user, _channel, user_channel = _seed_poll_ready_owner(db_session)
    _enable_push(db_session, user)
    _add_installation(db_session, user, "ExponentPushToken[first-device]")
    _add_installation(db_session, user, "ExponentPushToken[second-device]")
    db_session.commit()
    _patch_polling(monkeypatch)
    sent_payloads: list[dict[str, object]] = []

    def fake_post(url, json, headers, timeout):  # noqa: ARG001
        sent_payloads.append(json)
        return httpx.Response(200, json={"data": {"status": "ok", "id": f"ticket-{len(sent_payloads)}"}})

    monkeypatch.setattr("app.services.mobile_push.httpx.post", fake_post)
    client = _run_poll(db_session, _settings(push_enabled=True))

    try:
        first = client.post("/internal/run-poll", headers=_headers())
        user_channel.last_seen_video_id = "video-old"
        db_session.commit()
        second = client.post("/internal/run-poll", headers=_headers())
    finally:
        app.dependency_overrides.clear()

    assert first.status_code == 200
    assert second.status_code == 200
    assert len(sent_payloads) == 2
    assert db_session.query(MobilePushDelivery).count() == 2
    assert {delivery.status for delivery in db_session.query(MobilePushDelivery).all()} == {"sent"}


def test_push_disabled_gates_create_no_delivery_and_no_network(db_session, monkeypatch) -> None:
    user, _channel, _user_channel = _seed_poll_ready_owner(db_session)
    _enable_push(db_session, user)
    _add_installation(db_session, user)
    db_session.commit()
    _patch_polling(monkeypatch)

    def fail_if_called(*args, **kwargs):
        raise AssertionError("Expo should not be called when runtime push is disabled")

    monkeypatch.setattr("app.services.mobile_push.httpx.post", fail_if_called)
    client = _run_poll(db_session, _settings(push_enabled=False))

    try:
        response = client.post("/internal/run-poll", headers=_headers())
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["new_videos_detected"] == 1
    assert db_session.query(MobilePushDelivery).count() == 0


def test_global_and_channel_disabled_gates_create_no_delivery(db_session, monkeypatch) -> None:
    user, channel, _user_channel = _seed_poll_ready_owner(db_session)
    db_session.add(MobilePushSetting(user_id=user.id, enabled=False, default_for_monitored_channels=True))
    _add_installation(db_session, user)
    db_session.commit()
    _patch_polling(monkeypatch)
    monkeypatch.setattr("app.services.mobile_push.httpx.post", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError()))
    client = _run_poll(db_session, _settings(push_enabled=True))

    try:
        global_disabled = client.post("/internal/run-poll", headers=_headers())
        db_session.query(MobilePushSetting).filter_by(user_id=user.id).one().enabled = True
        db_session.add(
            MobilePushChannelPreference(
                user_id=user.id,
                channel_id=channel.id,
                push_enabled=False,
                explicitly_set=True,
            )
        )
        db_session.query(UserChannel).one().last_seen_video_id = "video-old-again"
        db_session.commit()
        _patch_polling(monkeypatch, video_id="video-newer")
        channel_disabled = client.post("/internal/run-poll", headers=_headers())
    finally:
        app.dependency_overrides.clear()

    assert global_disabled.status_code == 200
    assert channel_disabled.status_code == 200
    assert db_session.query(MobilePushDelivery).count() == 0


def test_ineligible_installations_baseline_unchanged_and_pending_email_do_not_push(db_session, monkeypatch) -> None:
    user, _channel, _user_channel = _seed_poll_ready_owner(db_session, baseline_established=False)
    _enable_push(db_session, user)
    _add_installation(db_session, user, enabled=False)
    _add_installation(db_session, user, unregistered_at=datetime.now(UTC))
    _add_installation(db_session, user, invalidated_at=datetime.now(UTC))
    _add_installation(db_session, user, token="")
    pending_video = Video(youtube_video_id="pending-video", channel_id=_channel.id, title="Pending", published_at=datetime.now(UTC))
    db_session.add(pending_video)
    db_session.flush()
    db_session.add(NotificationDelivery(user_id=user.id, video_id=pending_video.id, status="pending", attempt_count=0))
    db_session.commit()
    _patch_polling(monkeypatch, video_id="baseline-video")
    monkeypatch.setattr("app.services.mobile_push.httpx.post", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError()))
    client = _run_poll(db_session, _settings(push_enabled=True))

    try:
        baseline = client.post("/internal/run-poll", headers=_headers())
        _patch_polling(monkeypatch, video_id="baseline-video")
        unchanged = client.post("/internal/run-poll", headers=_headers())
    finally:
        app.dependency_overrides.clear()

    assert baseline.status_code == 200
    assert unchanged.status_code == 200
    assert db_session.query(MobilePushDelivery).count() == 0


def test_email_failure_does_not_suppress_push_and_push_failure_preserves_email_status(db_session, monkeypatch) -> None:
    user, _channel, _user_channel = _seed_poll_ready_owner(db_session)
    _enable_push(db_session, user)
    installation = _add_installation(db_session, user)
    db_session.commit()
    _patch_polling(monkeypatch)

    def fail_email(self, payload):  # noqa: ARG001
        raise EmailDeliveryAttemptError("transient email error", retryable=True)

    def fail_push(url, json, headers, timeout):  # noqa: ARG001
        return httpx.Response(200, json={"data": {"status": "error", "message": "temporary provider error"}})

    monkeypatch.setattr(polling_module.EmailDeliveryService, "send_video_notification", fail_email)
    monkeypatch.setattr("app.services.mobile_push.httpx.post", fail_push)
    client = _run_poll(db_session, _settings(push_enabled=True))

    try:
        response = client.post("/internal/run-poll", headers=_headers())
    finally:
        app.dependency_overrides.clear()

    email_delivery = db_session.query(NotificationDelivery).one()
    push_delivery = db_session.query(MobilePushDelivery).one()
    db_session.refresh(installation)

    assert response.status_code == 200
    assert response.json()["run_outcome"] == "success"
    assert email_delivery.status == "pending_retry"
    assert push_delivery.status == "failed"
    assert push_delivery.attempt_count == 1
    assert installation.last_error == "temporary provider error"


def test_provider_exception_records_failed_push_and_poll_still_updates_last_seen(db_session, monkeypatch) -> None:
    user, _channel, user_channel = _seed_poll_ready_owner(db_session)
    _enable_push(db_session, user)
    installation = _add_installation(db_session, user)
    db_session.commit()
    _patch_polling(monkeypatch)

    def raise_provider_error(url, json, headers, timeout):  # noqa: ARG001
        raise RuntimeError(
            "TRACEBACK HTTPS://internal.example.test expo-provider-secret "
            "ExponentPushToken[push-polling-device-token]"
        )

    monkeypatch.setattr("app.services.mobile_push.httpx.post", raise_provider_error)
    client = _run_poll(db_session, _settings(push_enabled=True))

    try:
        response = client.post("/internal/run-poll", headers=_headers())
    finally:
        app.dependency_overrides.clear()

    email_delivery = db_session.query(NotificationDelivery).one()
    push_delivery = db_session.query(MobilePushDelivery).one()
    db_session.refresh(user_channel)
    db_session.refresh(installation)
    combined = f"{push_delivery.last_error} {push_delivery.expo_response} {installation.last_error}"

    assert response.status_code == 200
    assert response.json()["new_videos_detected"] == 1
    assert user_channel.last_seen_video_id == "video-new"
    assert email_delivery.status == "delivered"
    assert push_delivery.status == "failed"
    assert push_delivery.attempt_count == 1
    assert "TRACEBACK" not in combined
    assert "HTTPS://internal.example.test" not in combined
    assert "expo-provider-secret" not in combined
    assert EXPO_TOKEN not in combined


@pytest.mark.parametrize("failure_kind", ["http_500", "malformed_json"])
def test_provider_http_and_malformed_failures_are_recorded(db_session, monkeypatch, failure_kind: str) -> None:
    user, _channel, user_channel = _seed_poll_ready_owner(db_session)
    _enable_push(db_session, user)
    _add_installation(db_session, user)
    db_session.commit()
    _patch_polling(monkeypatch)

    def fake_failure(url, json, headers, timeout):  # noqa: ARG001
        if failure_kind == "http_500":
            return httpx.Response(500, json={"error": "provider unavailable"})
        return httpx.Response(200, content=b"not-json")

    monkeypatch.setattr("app.services.mobile_push.httpx.post", fake_failure)
    client = _run_poll(db_session, _settings(push_enabled=True))

    try:
        response = client.post("/internal/run-poll", headers=_headers())
    finally:
        app.dependency_overrides.clear()

    push_delivery = db_session.query(MobilePushDelivery).one()
    db_session.refresh(user_channel)

    assert response.status_code == 200
    assert user_channel.last_seen_video_id == "video-new"
    assert push_delivery.status == "failed"
    assert push_delivery.attempt_count == 1
    assert push_delivery.expo_status == "provider_error"


@pytest.mark.parametrize(
    ("existing_status", "attempt_count"),
    [
        ("sent", 1),
        ("failed", 1),
        ("invalid_token", 1),
        ("skipped", 1),
        ("pending", 1),
    ],
)
def test_existing_terminal_or_attempted_push_delivery_is_not_retried(
    db_session,
    monkeypatch,
    existing_status: str,
    attempt_count: int,
) -> None:
    user, channel, _user_channel = _seed_poll_ready_owner(db_session)
    _enable_push(db_session, user)
    installation = _add_installation(db_session, user)
    video = Video(
        youtube_video_id="video-new",
        channel_id=channel.id,
        title="Brand New Video",
        published_at=datetime.now(UTC),
    )
    db_session.add(video)
    db_session.flush()
    notification_delivery = NotificationDelivery(user_id=user.id, video_id=video.id, status="delivered", attempt_count=1)
    db_session.add(notification_delivery)
    db_session.flush()
    existing_push = MobilePushDelivery(
        user_id=user.id,
        installation_id=installation.id,
        notification_delivery_id=notification_delivery.id,
        video_id=video.id,
        channel_id=channel.id,
        event_type="new_video",
        status=existing_status,
        attempt_count=attempt_count,
    )
    db_session.add(existing_push)
    db_session.commit()
    _patch_polling(monkeypatch, video_id="video-new")

    def fail_if_called(*args, **kwargs):
        raise AssertionError("Existing terminal or attempted push rows must not be retried")

    monkeypatch.setattr("app.services.mobile_push.httpx.post", fail_if_called)
    client = _run_poll(db_session, _settings(push_enabled=True))

    try:
        response = client.post("/internal/run-poll", headers=_headers())
    finally:
        app.dependency_overrides.clear()

    refreshed_push = db_session.get(MobilePushDelivery, existing_push.id)
    assert response.status_code == 200
    assert db_session.query(MobilePushDelivery).count() == 1
    assert refreshed_push.status == existing_status
    assert refreshed_push.attempt_count == attempt_count


def test_invalid_push_token_marks_installation_and_records_invalid_token(db_session, monkeypatch) -> None:
    user, _channel, _user_channel = _seed_poll_ready_owner(db_session)
    _enable_push(db_session, user)
    installation = _add_installation(db_session, user)
    db_session.commit()
    _patch_polling(monkeypatch)

    def invalid_token(url, json, headers, timeout):  # noqa: ARG001
        return httpx.Response(
            200,
            json={"data": {"status": "error", "message": "Device is not registered", "details": {"error": "DeviceNotRegistered"}}},
        )

    monkeypatch.setattr("app.services.mobile_push.httpx.post", invalid_token)
    client = _run_poll(db_session, _settings(push_enabled=True))

    try:
        response = client.post("/internal/run-poll", headers=_headers())
    finally:
        app.dependency_overrides.clear()

    push_delivery = db_session.query(MobilePushDelivery).one()
    db_session.refresh(installation)

    assert response.status_code == 200
    assert push_delivery.status == "invalid_token"
    assert installation.enabled is False
    assert installation.invalidated_at is not None


def test_quota_block_sends_no_push(db_session, monkeypatch) -> None:
    user, _channel, _user_channel = _seed_poll_ready_owner(db_session)
    _enable_push(db_session, user)
    _add_installation(db_session, user)
    db_session.add(
        polling_module.SyncState(
            user_id=user.id,
            process_type=polling_module.QUOTA_PROCESS,
            state_metadata={
                "usage_day": datetime.now(UTC).date().isoformat(),
                "estimated_units_used_today": 1,
            },
        )
    )
    db_session.commit()
    _patch_polling(monkeypatch)
    monkeypatch.setattr("app.services.mobile_push.httpx.post", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError()))
    client = _run_poll(db_session, _settings(push_enabled=True, quota_budget=1))

    try:
        response = client.post("/internal/run-poll", headers=_headers())
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["quota_blocked"] is True
    assert db_session.query(MobilePushDelivery).count() == 0
