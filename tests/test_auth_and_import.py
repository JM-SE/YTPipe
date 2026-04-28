from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from fastapi import HTTPException
from google.auth.exceptions import RefreshError
import pytest
from sqlalchemy.exc import IntegrityError

import app.services.auth as auth_module
import app.services.subscriptions as subscriptions_module
from app.api.routes.auth import get_db_session, get_settings
from app.core.settings import Settings
from app.main import app
from app.models.channel import Channel
from app.models.notification_delivery import NotificationDelivery
from app.models.oauth_account import OAuthAccount
from app.models.sync_state import SyncState
from app.models.user import User
from app.models.user_channel import UserChannel
from app.models.video import Video
from app.services.auth import GoogleOAuthService
from app.services.subscriptions import SUBSCRIPTION_SYNC_PROCESS, YouTubeSubscriptionService


def test_auth_google_redirects_to_google_authorization_url(monkeypatch) -> None:
    client = TestClient(app)
    monkeypatch.setattr(
        auth_module.GoogleOAuthService,
        "create_authorization_url",
        lambda self: ("https://accounts.google.com/o/oauth2/auth?mocked=1", "nonce-123", "pkce-123"),
    )

    response = client.get("/auth/google", follow_redirects=False)

    assert response.status_code == 302
    assert response.headers["location"] == "https://accounts.google.com/o/oauth2/auth?mocked=1"
    set_cookies = response.headers.get_list("set-cookie")
    assert any("ytpipe_oauth_nonce=nonce-123" in cookie for cookie in set_cookies)
    assert any("ytpipe_oauth_pkce_verifier=pkce-123" in cookie for cookie in set_cookies)


def test_refresh_contract_updates_expired_access_token(db_session, monkeypatch) -> None:
    user = User(email="owner@example.com")
    db_session.add(user)
    db_session.flush()
    oauth_account = OAuthAccount(
        user_id=user.id,
        provider="google",
        access_token="expired-token",
        refresh_token="refresh-token",
        token_expiry=datetime.now(timezone.utc) - timedelta(hours=1),
    )
    db_session.add(oauth_account)
    db_session.flush()

    class FakeCredentials:
        def __init__(self, **kwargs):
            self.token = kwargs["token"]
            self.refresh_token = kwargs["refresh_token"]
            self.expiry = kwargs.get("expiry")
            self.expired = True
            self.valid = False

        def refresh(self, request) -> None:  # noqa: ARG002
            self.token = "fresh-token"
            self.refresh_token = "fresh-refresh-token"
            self.expiry = datetime.now(timezone.utc) + timedelta(hours=1)
            self.expired = False
            self.valid = True

    monkeypatch.setattr(auth_module, "Credentials", FakeCredentials)

    service = GoogleOAuthService(
        auth_module.Settings(
            APP_SECRET_KEY="super-secret",
            GOOGLE_CLIENT_ID="client-id",
            GOOGLE_CLIENT_SECRET="client-secret",
            DATABASE_URL="sqlite://",
        )
    )

    credentials = service.ensure_valid_credentials(db_session, oauth_account)

    assert credentials.token == "fresh-token"
    assert oauth_account.access_token == "fresh-token"
    assert oauth_account.refresh_token == "fresh-refresh-token"
    assert oauth_account.token_expiry is not None


def test_refresh_contract_normalizes_naive_token_expiry_from_database(db_session, monkeypatch) -> None:
    user = User(email="owner@example.com")
    db_session.add(user)
    db_session.flush()
    oauth_account = OAuthAccount(
        user_id=user.id,
        provider="google",
        access_token="still-valid-token",
        refresh_token="refresh-token",
        token_expiry=datetime.now() + timedelta(days=365),
    )
    db_session.add(oauth_account)
    db_session.flush()

    class FakeCredentials:
        def __init__(self, **kwargs):
            self.token = kwargs["token"]
            self.refresh_token = kwargs["refresh_token"]
            self.expiry = kwargs.get("expiry")

        @property
        def expired(self) -> bool:
            return bool(self.expiry and self.expiry <= datetime.now(timezone.utc).replace(tzinfo=None))

        @property
        def valid(self) -> bool:
            return not self.expired

    monkeypatch.setattr(auth_module, "Credentials", FakeCredentials)

    service = GoogleOAuthService(
        auth_module.Settings(
            APP_SECRET_KEY="super-secret",
            GOOGLE_CLIENT_ID="client-id",
            GOOGLE_CLIENT_SECRET="client-secret",
            DATABASE_URL="sqlite://",
        )
    )

    credentials = service.ensure_valid_credentials(db_session, oauth_account)

    assert credentials.expiry is not None
    assert credentials.expiry.tzinfo is None


def test_refresh_contract_normalizes_aware_token_expiry_for_google_credentials(db_session, monkeypatch) -> None:
    user = User(email="owner@example.com")
    db_session.add(user)
    db_session.flush()
    oauth_account = OAuthAccount(
        user_id=user.id,
        provider="google",
        access_token="still-valid-token",
        refresh_token="refresh-token",
        token_expiry=datetime.now(timezone.utc) + timedelta(days=365),
    )
    db_session.add(oauth_account)
    db_session.flush()

    class FakeCredentials:
        def __init__(self, **kwargs):
            self.token = kwargs["token"]
            self.refresh_token = kwargs["refresh_token"]
            self.expiry = kwargs.get("expiry")

        @property
        def expired(self) -> bool:
            return bool(self.expiry and self.expiry <= datetime.now(timezone.utc).replace(tzinfo=None))

        @property
        def valid(self) -> bool:
            return not self.expired

    monkeypatch.setattr(auth_module, "Credentials", FakeCredentials)

    service = GoogleOAuthService(
        auth_module.Settings(
            APP_SECRET_KEY="super-secret",
            GOOGLE_CLIENT_ID="client-id",
            GOOGLE_CLIENT_SECRET="client-secret",
            DATABASE_URL="sqlite://",
        )
    )

    credentials = service.ensure_valid_credentials(db_session, oauth_account)

    assert credentials.expiry is not None
    assert credentials.expiry.tzinfo is None


def test_refresh_failure_requires_manual_reauth(db_session, monkeypatch) -> None:
    user = User(email="owner@example.com")
    db_session.add(user)
    db_session.flush()
    oauth_account = OAuthAccount(
        user_id=user.id,
        provider="google",
        access_token="expired-token",
        refresh_token="refresh-token",
        token_expiry=datetime.now(timezone.utc) - timedelta(hours=1),
    )
    db_session.add(oauth_account)
    db_session.flush()

    class FakeCredentials:
        def __init__(self, **kwargs):
            self.token = kwargs["token"]
            self.refresh_token = kwargs["refresh_token"]
            self.expiry = kwargs.get("expiry")
            self.expired = True
            self.valid = False

        def refresh(self, request) -> None:  # noqa: ARG002
            raise RefreshError("revoked")

    monkeypatch.setattr(auth_module, "Credentials", FakeCredentials)

    service = GoogleOAuthService(
        auth_module.Settings(
            APP_SECRET_KEY="super-secret",
            GOOGLE_CLIENT_ID="client-id",
            GOOGLE_CLIENT_SECRET="client-secret",
            DATABASE_URL="sqlite://",
        )
    )

    with pytest.raises(HTTPException) as exc_info:
        service.ensure_valid_credentials(db_session, oauth_account)
    assert exc_info.value.status_code == 401


def test_exchange_code_rejects_mismatched_browser_nonce(db_session) -> None:
    service = GoogleOAuthService(
        auth_module.Settings(
            APP_SECRET_KEY="super-secret",
            GOOGLE_CLIENT_ID="client-id",
            GOOGLE_CLIENT_SECRET="client-secret",
            DATABASE_URL="sqlite://",
        )
    )
    state, _ = auth_module.create_signed_state("super-secret")

    with pytest.raises(auth_module.AuthStateError) as exc_info:
        service.exchange_code_for_account(
            db_session,
            code="unused",
            state=state,
            cookie_nonce="different",
            cookie_code_verifier="pkce-verifier",
        )
    assert "initiating browser session" in str(exc_info.value)


def test_exchange_code_rejects_missing_pkce_verifier(db_session) -> None:
    service = GoogleOAuthService(
        auth_module.Settings(
            APP_SECRET_KEY="super-secret",
            GOOGLE_CLIENT_ID="client-id",
            GOOGLE_CLIENT_SECRET="client-secret",
            DATABASE_URL="sqlite://",
        )
    )
    state, nonce = auth_module.create_signed_state("super-secret")

    with pytest.raises(auth_module.AuthStateError) as exc_info:
        service.exchange_code_for_account(
            db_session,
            code="unused",
            state=state,
            cookie_nonce=nonce,
            cookie_code_verifier=None,
        )
    assert "PKCE code verifier is missing" in str(exc_info.value)


def test_exchange_code_wraps_google_token_exchange_failures(db_session, monkeypatch) -> None:
    service = GoogleOAuthService(
        auth_module.Settings(
            APP_SECRET_KEY="super-secret",
            GOOGLE_CLIENT_ID="client-id",
            GOOGLE_CLIENT_SECRET="client-secret",
            DATABASE_URL="sqlite://",
        )
    )
    state, nonce = auth_module.create_signed_state("super-secret")

    class FakeFlow:
        credentials = None

        def fetch_token(self, code):  # noqa: ARG002
            raise RuntimeError("bad code")

    monkeypatch.setattr(service, "_create_flow", lambda state=None: FakeFlow())

    with pytest.raises(auth_module.AuthFlowError) as exc_info:
        service.exchange_code_for_account(
            db_session,
            code="unused",
            state=state,
            cookie_nonce=nonce,
            cookie_code_verifier="pkce-verifier",
        )
    assert "code exchange failed" in str(exc_info.value)


def test_auth_callback_rejects_missing_pkce_cookie(db_session) -> None:
    client = TestClient(app)
    settings = Settings(
        APP_SECRET_KEY="super-secret",
        GOOGLE_CLIENT_ID="client-id",
        GOOGLE_CLIENT_SECRET="client-secret",
        DATABASE_URL="sqlite://",
    )
    state, nonce = auth_module.create_signed_state("super-secret")

    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_db_session] = lambda: db_session

    try:
        client.cookies.set(auth_module.OAUTH_STATE_COOKIE_NAME, nonce)
        response = client.get(f"/auth/callback?code=unused&state={state}")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 400
    assert response.json()["detail"] == "OAuth PKCE code verifier is missing from the initiating browser session."


def test_auth_callback_returns_catalog_only_sync_payload(db_session, monkeypatch) -> None:
    client = TestClient(app)
    settings = Settings(
        APP_SECRET_KEY="super-secret",
        GOOGLE_CLIENT_ID="client-id",
        GOOGLE_CLIENT_SECRET="client-secret",
        DATABASE_URL="sqlite://",
    )
    state, nonce = auth_module.create_signed_state("super-secret")

    def fake_exchange_code_for_account(self, session, code, state, cookie_nonce, cookie_code_verifier):  # noqa: ARG001
        user = User(email="owner@example.com")
        session.add(user)
        session.flush()
        oauth_account = OAuthAccount(
            user_id=user.id,
            provider="google",
            access_token="token",
            refresh_token="refresh",
        )
        session.add(oauth_account)
        session.flush()
        return auth_module.OAuthResult(user=user, oauth_account=oauth_account)

    monkeypatch.setattr(auth_module.GoogleOAuthService, "exchange_code_for_account", fake_exchange_code_for_account)

    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_db_session] = lambda: db_session

    try:
        client.cookies.set(auth_module.OAUTH_STATE_COOKIE_NAME, nonce)
        client.cookies.set(auth_module.OAUTH_PKCE_COOKIE_NAME, "pkce-verifier")
        response = client.get(f"/auth/callback?code=unused&state={state}")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    payload = response.json()
    assert payload["subscription_sync_required"] is True
    assert payload["subscription_sync_endpoint"] == "/internal/subscriptions/sync"
    assert "subscription_sync" not in payload


def test_internal_subscription_sync_requires_bearer_token(db_session) -> None:
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
        response = client.post("/internal/subscriptions/sync")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 401


def test_internal_subscription_sync_returns_catalog_counts(db_session, monkeypatch) -> None:
    client = TestClient(app)
    settings = Settings(
        APP_SECRET_KEY="super-secret",
        INTERNAL_API_BEARER_TOKEN="internal-secret",
        GOOGLE_CLIENT_ID="client-id",
        GOOGLE_CLIENT_SECRET="client-secret",
        DATABASE_URL="sqlite://",
    )

    user = User(email="owner@example.com")
    db_session.add(user)
    db_session.flush()
    oauth_account = OAuthAccount(
        user_id=user.id,
        provider="google",
        access_token="token",
        refresh_token="refresh",
    )
    db_session.add(oauth_account)
    db_session.commit()

    def fake_sync_subscriptions(self, session, user, oauth_account):  # noqa: ARG001
        return subscriptions_module.SubscriptionSyncResult(
            imported_channels=2,
            created_channels=1,
            updated_channels=1,
            created_user_channels=1,
            updated_user_channels=1,
        )

    monkeypatch.setattr(subscriptions_module.YouTubeSubscriptionService, "sync_subscriptions", fake_sync_subscriptions)

    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_db_session] = lambda: db_session

    try:
        response = client.post(
            "/internal/subscriptions/sync",
            headers={"Authorization": "Bearer internal-secret"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["subscription_sync"] == {
        "imported_channels": 2,
        "created_channels": 1,
        "updated_channels": 1,
        "created_user_channels": 1,
        "updated_user_channels": 1,
    }


def test_internal_subscription_sync_accepts_mobile_bearer_token(db_session, monkeypatch) -> None:
    client = TestClient(app)
    settings = Settings(
        APP_SECRET_KEY="super-secret",
        INTERNAL_API_BEARER_TOKEN="internal-secret",
        MOBILE_API_BEARER_TOKEN="mobile-secret",
        GOOGLE_CLIENT_ID="client-id",
        GOOGLE_CLIENT_SECRET="client-secret",
        DATABASE_URL="sqlite://",
    )

    user = User(email="owner@example.com")
    db_session.add(user)
    db_session.flush()
    oauth_account = OAuthAccount(
        user_id=user.id,
        provider="google",
        access_token="token",
        refresh_token="refresh",
    )
    db_session.add(oauth_account)
    db_session.commit()

    def fake_sync_subscriptions(self, session, user, oauth_account):  # noqa: ARG001
        return subscriptions_module.SubscriptionSyncResult(
            imported_channels=1,
            created_channels=1,
            updated_channels=0,
            created_user_channels=1,
            updated_user_channels=0,
        )

    monkeypatch.setattr(subscriptions_module.YouTubeSubscriptionService, "sync_subscriptions", fake_sync_subscriptions)

    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_db_session] = lambda: db_session

    try:
        response = client.post(
            "/internal/subscriptions/sync",
            headers={"Authorization": "Bearer mobile-secret"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200


def test_internal_channels_list_accepts_mobile_bearer_token(db_session) -> None:
    client = TestClient(app)
    settings = Settings(
        APP_SECRET_KEY="super-secret",
        INTERNAL_API_BEARER_TOKEN="internal-secret",
        MOBILE_API_BEARER_TOKEN="mobile-secret",
        GOOGLE_CLIENT_ID="client-id",
        GOOGLE_CLIENT_SECRET="client-secret",
        DATABASE_URL="sqlite://",
    )
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_db_session] = lambda: db_session

    try:
        response = client.get(
            "/internal/channels",
            headers={"Authorization": "Bearer mobile-secret"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200


def test_internal_channels_list_requires_bearer_token(db_session) -> None:
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
        response = client.get("/internal/channels")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 401


def test_internal_channels_list_returns_imported_channels_and_monitoring_state(db_session) -> None:
    client = TestClient(app)
    settings = Settings(
        APP_SECRET_KEY="super-secret",
        INTERNAL_API_BEARER_TOKEN="internal-secret",
        GOOGLE_CLIENT_ID="client-id",
        GOOGLE_CLIENT_SECRET="client-secret",
        DATABASE_URL="sqlite://",
    )

    user = User(email="owner@example.com")
    channel_a = Channel(youtube_channel_id="channel-aaa", title="Alpha Channel", uploads_playlist_id="uploads-a")
    channel_b = Channel(youtube_channel_id="channel-bbb", title="Beta Channel", uploads_playlist_id="uploads-b")
    db_session.add_all([user, channel_a, channel_b])
    db_session.flush()
    db_session.add_all(
        [
            UserChannel(
                user_id=user.id,
                channel_id=channel_b.id,
                is_monitored=False,
            ),
            UserChannel(
                user_id=user.id,
                channel_id=channel_a.id,
                is_monitored=True,
                last_seen_video_id="video-123",
                baseline_established_at=datetime(2026, 4, 23, 12, 0, tzinfo=timezone.utc),
            ),
        ]
    )
    db_session.commit()

    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_db_session] = lambda: db_session

    try:
        response = client.get(
            "/internal/channels",
            headers={"Authorization": "Bearer internal-secret"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json() == {
        "channels": [
            {
                "channel_id": channel_a.id,
                "youtube_channel_id": "channel-aaa",
                "title": "Alpha Channel",
                "is_monitored": True,
                "last_seen_video_id": "video-123",
                "baseline_established_at": "2026-04-23T12:00:00",
            }
        ],
        "pagination": {
            "limit": 50,
            "offset": 0,
            "total": 1,
        },
    }


def test_internal_channels_list_filters_unmonitored_and_query(db_session) -> None:
    client = TestClient(app)
    settings = Settings(
        APP_SECRET_KEY="super-secret",
        INTERNAL_API_BEARER_TOKEN="internal-secret",
        GOOGLE_CLIENT_ID="client-id",
        GOOGLE_CLIENT_SECRET="client-secret",
        DATABASE_URL="sqlite://",
    )

    user = User(email="owner@example.com")
    channel_a = Channel(youtube_channel_id="channel-aaa", title="Alpha Channel", uploads_playlist_id="uploads-a")
    channel_b = Channel(youtube_channel_id="channel-bbb", title="Beta Channel", uploads_playlist_id="uploads-b")
    db_session.add_all([user, channel_a, channel_b])
    db_session.flush()
    db_session.add_all(
        [
            UserChannel(user_id=user.id, channel_id=channel_a.id, is_monitored=True),
            UserChannel(user_id=user.id, channel_id=channel_b.id, is_monitored=False),
        ]
    )
    db_session.commit()

    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_db_session] = lambda: db_session

    try:
        unmonitored_response = client.get(
            "/internal/channels?monitoring=unmonitored&query=beta",
            headers={"Authorization": "Bearer internal-secret"},
        )
        all_response = client.get(
            "/internal/channels?monitoring=all&query=channel",
            headers={"Authorization": "Bearer internal-secret"},
        )
    finally:
        app.dependency_overrides.clear()

    assert unmonitored_response.status_code == 200
    assert unmonitored_response.json()["pagination"] == {"limit": 50, "offset": 0, "total": 1}
    assert [item["title"] for item in unmonitored_response.json()["channels"]] == ["Beta Channel"]

    assert all_response.status_code == 200
    assert all_response.json()["pagination"] == {"limit": 50, "offset": 0, "total": 2}
    assert [item["title"] for item in all_response.json()["channels"]] == ["Alpha Channel", "Beta Channel"]


def test_internal_channels_list_pagination_and_latest_video_summary(db_session) -> None:
    client = TestClient(app)
    settings = Settings(
        APP_SECRET_KEY="super-secret",
        INTERNAL_API_BEARER_TOKEN="internal-secret",
        GOOGLE_CLIENT_ID="client-id",
        GOOGLE_CLIENT_SECRET="client-secret",
        DATABASE_URL="sqlite://",
    )

    user = User(email="owner@example.com")
    channel_a = Channel(youtube_channel_id="channel-aaa", title="Alpha", uploads_playlist_id="uploads-a")
    channel_b = Channel(youtube_channel_id="channel-bbb", title="Beta", uploads_playlist_id="uploads-b")
    db_session.add_all([user, channel_a, channel_b])
    db_session.flush()
    db_session.add_all(
        [
            UserChannel(
                user_id=user.id,
                channel_id=channel_a.id,
                is_monitored=True,
                last_seen_video_id="video-aaa",
                baseline_established_at=datetime(2026, 4, 23, 12, 0, tzinfo=timezone.utc),
            ),
            UserChannel(user_id=user.id, channel_id=channel_b.id, is_monitored=True),
        ]
    )
    db_session.flush()
    db_session.add(
        Video(
            youtube_video_id="video-aaa",
            channel_id=channel_a.id,
            title="Alpha latest",
            published_at=datetime(2026, 4, 24, 10, 0, tzinfo=timezone.utc),
        )
    )
    db_session.commit()

    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_db_session] = lambda: db_session

    try:
        response = client.get(
            "/internal/channels?monitoring=all&limit=1&offset=0",
            headers={"Authorization": "Bearer internal-secret"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    payload = response.json()
    assert payload["pagination"] == {"limit": 1, "offset": 0, "total": 2}
    assert len(payload["channels"]) == 1
    assert payload["channels"][0]["title"] == "Alpha"
    assert payload["channels"][0]["latest_detected_video"] == {
        "youtube_video_id": "video-aaa",
        "title": "Alpha latest",
        "published_at": "2026-04-24T10:00:00",
    }


def test_internal_channel_monitoring_patch_toggles_false_to_true(db_session) -> None:
    client = TestClient(app)
    settings = Settings(
        APP_SECRET_KEY="super-secret",
        INTERNAL_API_BEARER_TOKEN="internal-secret",
        GOOGLE_CLIENT_ID="client-id",
        GOOGLE_CLIENT_SECRET="client-secret",
        DATABASE_URL="sqlite://",
    )

    user = User(email="owner@example.com")
    channel = Channel(youtube_channel_id="channel-123", title="Example Channel", uploads_playlist_id="uploads-123")
    db_session.add_all([user, channel])
    db_session.flush()
    user_channel = UserChannel(user_id=user.id, channel_id=channel.id, is_monitored=False)
    db_session.add(user_channel)
    db_session.commit()

    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_db_session] = lambda: db_session

    try:
        response = client.patch(
            f"/internal/channels/{channel.id}/monitoring",
            headers={"Authorization": "Bearer internal-secret"},
            json={"is_monitored": True},
        )
    finally:
        app.dependency_overrides.clear()

    db_session.refresh(user_channel)

    assert response.status_code == 200
    assert response.json() == {
        "channel_id": channel.id,
        "youtube_channel_id": "channel-123",
        "title": "Example Channel",
        "is_monitored": True,
    }
    assert user_channel.is_monitored is True
    assert user_channel.last_seen_video_id is None
    assert user_channel.baseline_established_at is None


def test_internal_channel_monitoring_patch_toggles_true_to_false(db_session) -> None:
    client = TestClient(app)
    settings = Settings(
        APP_SECRET_KEY="super-secret",
        INTERNAL_API_BEARER_TOKEN="internal-secret",
        GOOGLE_CLIENT_ID="client-id",
        GOOGLE_CLIENT_SECRET="client-secret",
        DATABASE_URL="sqlite://",
    )

    baseline_established_at = datetime(2026, 4, 23, 12, 0, tzinfo=timezone.utc)
    user = User(email="owner@example.com")
    channel = Channel(youtube_channel_id="channel-123", title="Example Channel", uploads_playlist_id="uploads-123")
    db_session.add_all([user, channel])
    db_session.flush()
    user_channel = UserChannel(
        user_id=user.id,
        channel_id=channel.id,
        is_monitored=True,
        last_seen_video_id="video-123",
        baseline_established_at=baseline_established_at,
    )
    db_session.add(user_channel)
    db_session.commit()

    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_db_session] = lambda: db_session

    try:
        response = client.patch(
            f"/internal/channels/{channel.id}/monitoring",
            headers={"Authorization": "Bearer internal-secret"},
            json={"is_monitored": False},
        )
    finally:
        app.dependency_overrides.clear()

    db_session.refresh(user_channel)

    assert response.status_code == 200
    assert response.json() == {
        "channel_id": channel.id,
        "youtube_channel_id": "channel-123",
        "title": "Example Channel",
        "is_monitored": False,
        "last_seen_video_id": "video-123",
        "baseline_established_at": "2026-04-23T12:00:00",
    }
    assert user_channel.is_monitored is False
    assert user_channel.last_seen_video_id == "video-123"
    assert user_channel.baseline_established_at == baseline_established_at.replace(tzinfo=None)


def test_internal_channel_monitoring_patch_returns_404_for_unknown_channel_id(db_session) -> None:
    client = TestClient(app)
    settings = Settings(
        APP_SECRET_KEY="super-secret",
        INTERNAL_API_BEARER_TOKEN="internal-secret",
        GOOGLE_CLIENT_ID="client-id",
        GOOGLE_CLIENT_SECRET="client-secret",
        DATABASE_URL="sqlite://",
    )

    user = User(email="owner@example.com")
    db_session.add(user)
    db_session.commit()

    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_db_session] = lambda: db_session

    try:
        response = client.patch(
            "/internal/channels/999/monitoring",
            headers={"Authorization": "Bearer internal-secret"},
            json={"is_monitored": True},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 404
    assert response.json()["detail"] == "Channel not found."


def test_single_user_guard_is_enforced_by_database(db_session) -> None:
    db_session.add(User(email="owner@example.com"))
    db_session.flush()

    db_session.add(User(email="other@example.com"))
    with pytest.raises(IntegrityError):
        db_session.flush()
    db_session.rollback()


def test_subscription_import_creates_catalog_records_without_monitoring_or_baseline(db_session, monkeypatch) -> None:
    user = User(email="owner@example.com")
    db_session.add(user)
    db_session.flush()
    oauth_account = OAuthAccount(
        user_id=user.id,
        provider="google",
        access_token="token",
        refresh_token="refresh",
        token_expiry=datetime.now(timezone.utc) + timedelta(hours=1),
    )
    db_session.add(oauth_account)
    db_session.flush()

    class FakeRequest:
        def __init__(self, payload):
            self.payload = payload

        def execute(self):
            return self.payload

    class FakeSubscriptionsResource:
        def list(self, **kwargs):  # noqa: ARG002
            return FakeRequest(
                {
                    "items": [
                        {
                            "snippet": {
                                "title": "Example Channel",
                                "resourceId": {"channelId": "channel-123"},
                            }
                        }
                    ]
                }
            )

    class FakeChannelsResource:
        def list(self, **kwargs):  # noqa: ARG002
            return FakeRequest(
                {
                    "items": [
                        {
                            "id": "channel-123",
                            "snippet": {"title": "Example Channel"},
                            "contentDetails": {
                                "relatedPlaylists": {"uploads": "uploads-123"}
                            },
                        }
                    ]
                }
            )

    class FakeYouTube:
        def subscriptions(self):
            return FakeSubscriptionsResource()

        def channels(self):
            return FakeChannelsResource()

        def playlistItems(self):
            raise AssertionError("playlistItems baseline fetch should not run during subscription import")

    monkeypatch.setattr(subscriptions_module, "build", lambda *args, **kwargs: FakeYouTube())

    class FakeAuthService:
        def ensure_valid_credentials(self, session, account):  # noqa: ARG002
            return object()

    service = YouTubeSubscriptionService(FakeAuthService())

    result = service.sync_subscriptions(db_session, user=user, oauth_account=oauth_account)

    channel = db_session.query(Channel).one()
    user_channel = db_session.query(UserChannel).one()
    sync_state = db_session.query(SyncState).filter_by(process_type=SUBSCRIPTION_SYNC_PROCESS).one()

    assert result.imported_channels == 1
    assert result.created_channels == 1
    assert result.created_user_channels == 1
    assert result.updated_channels == 0
    assert result.updated_user_channels == 0
    assert channel.youtube_channel_id == "channel-123"
    assert channel.uploads_playlist_id == "uploads-123"
    assert user_channel.is_monitored is False
    assert user_channel.last_seen_video_id is None
    assert user_channel.baseline_established_at is None
    assert sync_state.last_success_at is not None
    assert sync_state.state_metadata == {
        "imported_channels": 1,
        "created_channels": 1,
        "updated_channels": 0,
        "created_user_channels": 1,
        "updated_user_channels": 0,
    }
    assert db_session.query(Video).count() == 0
    assert db_session.query(NotificationDelivery).count() == 0


def test_subscription_import_preserves_existing_user_channel_monitoring_state(db_session, monkeypatch) -> None:
    user = User(email="owner@example.com")
    db_session.add(user)
    db_session.flush()
    oauth_account = OAuthAccount(
        user_id=user.id,
        provider="google",
        access_token="token",
        refresh_token="refresh",
        token_expiry=datetime.now(timezone.utc) + timedelta(hours=1),
    )
    channel = Channel(
        youtube_channel_id="channel-123",
        title="Old Channel",
        uploads_playlist_id="uploads-old",
    )
    db_session.add(channel)
    db_session.flush()
    user_channel = UserChannel(
        user_id=user.id,
        channel_id=channel.id,
        is_monitored=True,
        last_seen_video_id="stale-video",
        baseline_established_at=datetime.now(timezone.utc),
    )
    db_session.add(user_channel)
    db_session.add(oauth_account)
    db_session.flush()

    class FakeRequest:
        def __init__(self, payload):
            self.payload = payload

        def execute(self):
            return self.payload

    class FakeSubscriptionsResource:
        def list(self, **kwargs):  # noqa: ARG002
            return FakeRequest(
                {
                    "items": [
                        {
                            "snippet": {
                                "title": "Example Channel",
                                "resourceId": {"channelId": "channel-123"},
                            }
                        }
                    ]
                }
            )

    class FakeChannelsResource:
        def list(self, **kwargs):  # noqa: ARG002
            return FakeRequest(
                {
                    "items": [
                        {
                            "id": "channel-123",
                            "snippet": {"title": "Example Channel"},
                            "contentDetails": {
                                "relatedPlaylists": {"uploads": "uploads-123"}
                            },
                        }
                    ]
                }
            )

    class FakeYouTube:
        def subscriptions(self):
            return FakeSubscriptionsResource()

        def channels(self):
            return FakeChannelsResource()

        def playlistItems(self):
            raise AssertionError("playlistItems baseline fetch should not run during subscription import")

    monkeypatch.setattr(subscriptions_module, "build", lambda *args, **kwargs: FakeYouTube())

    class FakeAuthService:
        def ensure_valid_credentials(self, session, account):  # noqa: ARG002
            return object()

    service = YouTubeSubscriptionService(FakeAuthService())

    result = service.sync_subscriptions(db_session, user=user, oauth_account=oauth_account)

    channel = db_session.query(Channel).one()
    user_channel = db_session.query(UserChannel).one()
    sync_state = db_session.query(SyncState).filter_by(process_type=SUBSCRIPTION_SYNC_PROCESS).one()

    assert result.imported_channels == 1
    assert result.created_channels == 0
    assert result.updated_channels == 1
    assert result.created_user_channels == 0
    assert result.updated_user_channels == 1
    assert channel.title == "Example Channel"
    assert channel.uploads_playlist_id == "uploads-123"
    assert user_channel.is_monitored is True
    assert user_channel.last_seen_video_id == "stale-video"
    assert user_channel.baseline_established_at is not None
    assert sync_state.last_success_at is not None
    assert db_session.query(Video).count() == 0
    assert db_session.query(NotificationDelivery).count() == 0


def test_user_channel_defaults_to_not_monitored(db_session) -> None:
    user = User(email="owner@example.com")
    channel = Channel(youtube_channel_id="channel-123")
    db_session.add_all([user, channel])
    db_session.flush()

    user_channel = UserChannel(user_id=user.id, channel_id=channel.id)
    db_session.add(user_channel)
    db_session.flush()

    assert user_channel.is_monitored is False
    assert user_channel.last_seen_video_id is None
    assert user_channel.baseline_established_at is None
