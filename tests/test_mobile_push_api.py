from __future__ import annotations

from uuid import uuid4

import httpx
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.settings import Settings, get_settings
from app.db.session import get_db_session
from app.main import app
from app.models.channel import Channel
from app.models.mobile_push_channel_preference import MobilePushChannelPreference
from app.models.mobile_push_delivery import MobilePushDelivery
from app.models.mobile_push_installation import MobilePushInstallation
from app.models.mobile_push_setting import MobilePushSetting
from app.models.user import User
from app.models.user_channel import UserChannel


MOBILE_TOKEN = "mobile-secret"
INTERNAL_TOKEN = "internal-secret"
EXPO_TOKEN = "ExponentPushToken[super-secret-device-token]"


def _settings(push_enabled: bool = False) -> Settings:
    return Settings(
        APP_SECRET_KEY="super-secret",
        INTERNAL_API_BEARER_TOKEN=INTERNAL_TOKEN,
        MOBILE_API_BEARER_TOKEN=MOBILE_TOKEN,
        DATABASE_URL="sqlite://",
        PUSH_NOTIFICATIONS_ENABLED=push_enabled,
        EXPO_ACCESS_TOKEN="expo-provider-secret" if push_enabled else "",
    )


def _mobile_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {MOBILE_TOKEN}"}


def _client(db_session, push_enabled: bool = False) -> TestClient:
    app.dependency_overrides[get_settings] = lambda: _settings(push_enabled)
    app.dependency_overrides[get_db_session] = lambda: db_session
    return TestClient(app)


def _seed_owner(db_session) -> User:
    user = User(email="owner@example.com")
    db_session.add(user)
    db_session.flush()
    return user


def _seed_channels(db_session, user: User):
    alpha = Channel(youtube_channel_id="UC-alpha", title="Alpha Channel")
    beta = Channel(youtube_channel_id="UC-beta", title="Beta Channel")
    db_session.add_all([alpha, beta])
    db_session.flush()
    db_session.add_all(
        [
            UserChannel(user_id=user.id, channel_id=alpha.id, is_monitored=True),
            UserChannel(user_id=user.id, channel_id=beta.id, is_monitored=False),
        ]
    )
    db_session.flush()
    return alpha, beta


def test_mobile_push_endpoints_require_mobile_token_only(db_session) -> None:
    _seed_owner(db_session)
    client = _client(db_session)
    installation_id = uuid4()

    try:
        assert client.get(f"/internal/mobile-push/status?installation_id={installation_id}").status_code == 401
        assert (
            client.get(
                f"/internal/mobile-push/status?installation_id={installation_id}",
                headers={"Authorization": f"Bearer {INTERNAL_TOKEN}"},
            ).status_code
            == 401
        )
        assert (
            client.get(
                f"/internal/mobile-push/status?installation_id={installation_id}",
                headers=_mobile_headers(),
            ).status_code
            == 200
        )
    finally:
        app.dependency_overrides.clear()


def test_every_mobile_push_endpoint_rejects_internal_token(db_session) -> None:
    user = _seed_owner(db_session)
    alpha, _beta = _seed_channels(db_session, user)
    installation_id = uuid4()
    db_session.add(
        MobilePushInstallation(
            user_id=user.id,
            installation_id=installation_id,
            expo_push_token=EXPO_TOKEN,
        )
    )
    db_session.commit()
    client = _client(db_session)
    internal_headers = {"Authorization": f"Bearer {INTERNAL_TOKEN}"}

    requests = [
        lambda: client.get(f"/internal/mobile-push/status?installation_id={installation_id}", headers=internal_headers),
        lambda: client.post(
            "/internal/mobile-push/register",
            headers=internal_headers,
            json={"installation_id": str(installation_id), "expo_push_token": EXPO_TOKEN},
        ),
        lambda: client.delete(f"/internal/mobile-push/installations/{installation_id}", headers=internal_headers),
        lambda: client.patch("/internal/mobile-push/settings", headers=internal_headers, json={"enabled": True}),
        lambda: client.get("/internal/mobile-push/channel-preferences", headers=internal_headers),
        lambda: client.patch(
            f"/internal/mobile-push/channels/{alpha.id}",
            headers=internal_headers,
            json={"push_enabled": True},
        ),
        lambda: client.post(
            "/internal/mobile-push/test",
            headers=internal_headers,
            json={"installation_id": str(installation_id)},
        ),
    ]

    try:
        statuses = [request().status_code for request in requests]
    finally:
        app.dependency_overrides.clear()

    assert statuses == [401, 401, 401, 401, 401, 401, 401]


def test_register_status_and_unregister_are_idempotent_and_mask_tokens(db_session) -> None:
    _seed_owner(db_session)
    client = _client(db_session)
    installation_id = uuid4()

    try:
        first = client.post(
            "/internal/mobile-push/register",
            headers=_mobile_headers(),
            json={
                "installation_id": str(installation_id),
                "expo_push_token": EXPO_TOKEN,
                "platform": "ios",
                "app_version": "1.0.0",
                "build_number": "1",
                "device_name": "Phone",
            },
        )
        second = client.post(
            "/internal/mobile-push/register",
            headers=_mobile_headers(),
            json={
                "installation_id": str(installation_id),
                "expo_push_token": "ExponentPushToken[rotated-secret-device-token]",
                "platform": "android",
            },
        )
        status_response = client.get(
            f"/internal/mobile-push/status?installation_id={installation_id}",
            headers=_mobile_headers(),
        )
        unregister = client.delete(
            f"/internal/mobile-push/installations/{installation_id}",
            headers=_mobile_headers(),
        )
        unregister_again = client.delete(
            f"/internal/mobile-push/installations/{uuid4()}",
            headers=_mobile_headers(),
        )
    finally:
        app.dependency_overrides.clear()

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["token_masked"] != EXPO_TOKEN
    assert second.json()["platform"] == "android"
    assert "rotated-secret-device-token" not in str(second.json())

    status_payload = status_response.json()
    assert status_payload["installation"]["registered"] is True
    assert status_payload["installation"]["token_masked"] != "ExponentPushToken[rotated-secret-device-token]"
    assert "rotated-secret-device-token" not in str(status_payload)

    assert unregister.json()["registered"] is True
    assert unregister.json()["enabled"] is False
    assert unregister_again.json() == {
        "registered": False,
        "installation_id": str(unregister_again.json()["installation_id"]),
        "enabled": False,
        "unregistered_at": None,
    }


def test_settings_first_enabled_at_only_set_once_and_channel_preferences(db_session) -> None:
    user = _seed_owner(db_session)
    alpha, beta = _seed_channels(db_session, user)
    client = _client(db_session)

    try:
        initial = client.get("/internal/mobile-push/channel-preferences", headers=_mobile_headers())
        enabled = client.patch(
            "/internal/mobile-push/settings",
            headers=_mobile_headers(),
            json={"enabled": True},
        )
        first_enabled_at = enabled.json()["first_enabled_at"]
        disabled = client.patch(
            "/internal/mobile-push/settings",
            headers=_mobile_headers(),
            json={"enabled": False, "default_for_monitored_channels": False},
        )
        reenabled = client.patch(
            "/internal/mobile-push/settings",
            headers=_mobile_headers(),
            json={"enabled": True},
        )
        explicit_disable = client.patch(
            f"/internal/mobile-push/channels/{alpha.id}",
            headers=_mobile_headers(),
            json={"push_enabled": False},
        )
        unmonitored = client.patch(
            f"/internal/mobile-push/channels/{beta.id}",
            headers=_mobile_headers(),
            json={"push_enabled": True},
        )
        unknown = client.patch(
            "/internal/mobile-push/channels/99999",
            headers=_mobile_headers(),
            json={"push_enabled": True},
        )
        all_channels = client.get("/internal/mobile-push/channel-preferences?monitoring=all&query=beta", headers=_mobile_headers())
    finally:
        app.dependency_overrides.clear()

    assert initial.status_code == 200
    assert [item["channel_id"] for item in initial.json()["channels"]] == [alpha.id]
    assert enabled.json()["enabled"] is True
    assert enabled.json()["monitored_channels_effectively_enabled_count"] == 1
    assert first_enabled_at is not None
    assert disabled.json()["enabled"] is False
    assert reenabled.json()["first_enabled_at"] == first_enabled_at
    assert reenabled.json()["monitored_channels_effectively_enabled_count"] == 0
    assert explicit_disable.status_code == 200
    assert explicit_disable.json()["preference"] == {"explicitly_set": True, "push_enabled": False}
    assert explicit_disable.json()["push_enabled"] is False
    assert unmonitored.status_code == 409
    assert unknown.status_code == 404
    assert all_channels.json()["pagination"]["total"] == 1
    assert all_channels.json()["channels"][0]["channel_id"] == beta.id


def test_channel_preference_update_existing_channel_without_user_relation_returns_409(db_session) -> None:
    user = _seed_owner(db_session)
    _seed_channels(db_session, user)
    orphan = Channel(youtube_channel_id="UC-orphan", title="Orphan Channel")
    db_session.add(orphan)
    db_session.commit()
    client = _client(db_session)

    try:
        response = client.patch(
            f"/internal/mobile-push/channels/{orphan.id}",
            headers=_mobile_headers(),
            json={"push_enabled": True},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 409


def test_test_push_disabled_returns_409_and_makes_no_network_call(db_session, monkeypatch) -> None:
    user = _seed_owner(db_session)
    installation_id = uuid4()
    installation = MobilePushInstallation(
        user_id=user.id,
        installation_id=installation_id,
        expo_push_token=EXPO_TOKEN,
    )
    db_session.add(installation)
    db_session.commit()
    client = _client(db_session, push_enabled=False)

    def fail_if_called(*args, **kwargs):
        raise AssertionError("Expo network call should not happen when push is disabled")

    monkeypatch.setattr("app.services.mobile_push.httpx.post", fail_if_called)

    try:
        response = client.post(
            "/internal/mobile-push/test",
            headers=_mobile_headers(),
            json={"installation_id": str(installation_id)},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 409
    assert "disabled" in response.json()["detail"].lower()


def test_test_push_success_records_delivery_and_sanitizes_response(db_session, monkeypatch) -> None:
    user = _seed_owner(db_session)
    installation_id = uuid4()
    installation = MobilePushInstallation(
        user_id=user.id,
        installation_id=installation_id,
        expo_push_token=EXPO_TOKEN,
    )
    db_session.add(installation)
    db_session.commit()
    captured: dict[str, object] = {}
    client = _client(db_session, push_enabled=True)

    def fake_post(url, json, headers, timeout):
        captured.update({"url": url, "json": json, "headers": headers, "timeout": timeout})
        return httpx.Response(200, json={"data": {"status": "ok", "id": "ticket-123"}})

    monkeypatch.setattr("app.services.mobile_push.httpx.post", fake_post)

    try:
        response = client.post(
            "/internal/mobile-push/test",
            headers=_mobile_headers(),
            json={"installation_id": str(installation_id)},
        )
    finally:
        app.dependency_overrides.clear()

    payload = response.json()
    db_session.refresh(installation)
    delivery = db_session.scalar(select(MobilePushDelivery).where(MobilePushDelivery.event_type == "test"))

    assert response.status_code == 200
    assert payload["sent"] is True
    assert payload["expo_ticket_id"] == "ticket-123"
    assert EXPO_TOKEN not in str(payload)
    assert captured["json"]["to"] == EXPO_TOKEN
    assert EXPO_TOKEN not in str(captured["json"]["data"])
    assert captured["headers"] == {"Authorization": "Bearer expo-provider-secret"}
    assert delivery is not None
    assert delivery.status == "sent"
    assert delivery.expo_ticket_id == "ticket-123"
    assert installation.last_success_at is not None
    assert installation.last_expo_status == "ok"


def test_test_push_invalid_token_marks_installation_and_records_safe_failure(db_session, monkeypatch) -> None:
    user = _seed_owner(db_session)
    installation_id = uuid4()
    installation = MobilePushInstallation(
        user_id=user.id,
        installation_id=installation_id,
        expo_push_token=EXPO_TOKEN,
    )
    db_session.add(installation)
    db_session.commit()
    client = _client(db_session, push_enabled=True)

    def fake_post(url, json, headers, timeout):
        return httpx.Response(
            200,
            json={
                "data": {
                    "status": "error",
                    "message": "Device is not registered",
                    "details": {"error": "DeviceNotRegistered"},
                }
            },
        )

    monkeypatch.setattr("app.services.mobile_push.httpx.post", fake_post)

    try:
        response = client.post(
            "/internal/mobile-push/test",
            headers=_mobile_headers(),
            json={"installation_id": str(installation_id)},
        )
    finally:
        app.dependency_overrides.clear()

    db_session.refresh(installation)
    delivery = db_session.scalar(select(MobilePushDelivery).where(MobilePushDelivery.event_type == "test"))

    assert response.status_code == 502
    assert EXPO_TOKEN not in str(response.json())
    assert installation.enabled is False
    assert installation.invalidated_at is not None
    assert delivery is not None
    assert delivery.status == "invalid_token"
    assert EXPO_TOKEN not in str(delivery.expo_response)


def test_test_push_provider_error_sanitizes_secret_diagnostics(db_session, monkeypatch) -> None:
    user = _seed_owner(db_session)
    installation_id = uuid4()
    installation = MobilePushInstallation(
        user_id=user.id,
        installation_id=installation_id,
        expo_push_token=EXPO_TOKEN,
    )
    db_session.add(installation)
    db_session.commit()
    client = _client(db_session, push_enabled=True)

    def fake_post(url, json, headers, timeout):
        return httpx.Response(
            200,
            json={
                "data": {
                    "status": "error",
                    "message": "failed with expo-provider-secret at https://internal.example.test Traceback ...",
                }
            },
        )

    monkeypatch.setattr("app.services.mobile_push.httpx.post", fake_post)

    try:
        response = client.post(
            "/internal/mobile-push/test",
            headers=_mobile_headers(),
            json={"installation_id": str(installation_id)},
        )
    finally:
        app.dependency_overrides.clear()

    db_session.refresh(installation)
    delivery = db_session.scalar(select(MobilePushDelivery).where(MobilePushDelivery.event_type == "test"))
    combined = f"{response.json()} {installation.last_error} {delivery.last_error} {delivery.expo_response}"

    assert response.status_code == 502
    assert "expo-provider-secret" not in combined
    assert "https://internal.example.test" not in combined
    assert "Traceback" not in combined
    assert EXPO_TOKEN not in combined


def test_existing_mobile_endpoints_remain_unchanged(db_session) -> None:
    user = _seed_owner(db_session)
    _seed_channels(db_session, user)
    client = _client(db_session)

    try:
        channels_response = client.get("/internal/channels", headers={"Authorization": f"Bearer {INTERNAL_TOKEN}"})
        activity_response = client.get("/internal/activity", headers={"Authorization": f"Bearer {INTERNAL_TOKEN}"})
    finally:
        app.dependency_overrides.clear()

    assert channels_response.status_code == 200
    assert {
        "channel_id",
        "youtube_channel_id",
        "title",
        "is_monitored",
    }.issubset(set(channels_response.json()["channels"][0]))
    assert activity_response.status_code == 200


def test_status_unknown_installation_returns_null_metadata(db_session) -> None:
    _seed_owner(db_session)
    unknown_id = uuid4()
    client = _client(db_session)

    try:
        response = client.get(
            f"/internal/mobile-push/status?installation_id={unknown_id}",
            headers=_mobile_headers(),
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["installation"] == {
        "registered": False,
        "installation_id": str(unknown_id),
        "enabled": False,
        "platform": None,
        "app_version": None,
        "build_number": None,
        "device_name": None,
        "token_masked": None,
        "registered_at": None,
        "last_seen_at": None,
        "unregistered_at": None,
        "invalidated_at": None,
        "last_attempt_at": None,
        "last_success_at": None,
        "last_error": None,
        "last_expo_ticket_id": None,
        "last_expo_status": None,
    }
