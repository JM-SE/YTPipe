from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import inspect

from app.core.settings import Settings
from app.models.channel import Channel
from app.models.mobile_push_channel_preference import MobilePushChannelPreference
from app.models.mobile_push_delivery import MobilePushDelivery
from app.models.mobile_push_installation import MobilePushInstallation
from app.models.mobile_push_setting import MobilePushSetting
from app.models.notification_delivery import NotificationDelivery
from app.models.user import User
from app.models.user_channel import UserChannel
from app.models.video import Video
from app.services.mobile_push import (
    PUSH_DELIVERY_PENDING,
    PUSH_DELIVERY_SKIPPED,
    PUSH_EVENT_NEW_VIDEO,
    MobilePushService,
    mask_expo_token,
    normalize_platform,
)


def test_mobile_push_settings_defaults_are_safe() -> None:
    settings = Settings(DATABASE_URL="sqlite://")

    assert settings.push_notifications_enabled is False
    assert settings.expo_push_endpoint == "https://exp.host/--/api/v2/push/send"
    assert settings.expo_push_receipts_enabled is False
    assert settings.expo_push_receipts_endpoint == "https://exp.host/--/api/v2/push/getReceipts"
    assert settings.expo_access_token == ""


def test_mobile_push_schema_tables_constraints_and_indexes_exist(db_session) -> None:
    inspector = inspect(db_session.bind)
    table_names = set(inspector.get_table_names())

    assert {
        MobilePushSetting.__tablename__,
        MobilePushInstallation.__tablename__,
        MobilePushChannelPreference.__tablename__,
        MobilePushDelivery.__tablename__,
    }.issubset(table_names)

    installation_unique_constraints = {
        tuple(constraint["column_names"])
        for constraint in inspector.get_unique_constraints(MobilePushInstallation.__tablename__)
    }
    assert ("user_id", "installation_id") in installation_unique_constraints

    preference_unique_constraints = {
        tuple(constraint["column_names"])
        for constraint in inspector.get_unique_constraints(MobilePushChannelPreference.__tablename__)
    }
    assert ("user_id", "channel_id") in preference_unique_constraints

    installation_indexes = {index["name"] for index in inspector.get_indexes(MobilePushInstallation.__tablename__)}
    assert "ix_mobile_push_installations_user_enabled" in installation_indexes

    delivery_indexes = {index["name"] for index in inspector.get_indexes(MobilePushDelivery.__tablename__)}
    assert "ix_mobile_push_deliveries_user_event_created" in delivery_indexes


def test_mobile_push_service_masks_tokens_and_normalizes_platform() -> None:
    token = "ExponentPushToken[abcdefghijklmnopqrstuvwxyz]"

    masked = mask_expo_token(token)

    assert masked is not None
    assert masked != token
    assert "…" in masked
    assert mask_expo_token(None) is None
    assert mask_expo_token("   ") is None
    assert normalize_platform("IOS") == "ios"
    assert normalize_platform("android") == "android"
    assert normalize_platform("windows") == "unknown"


def test_mobile_push_service_get_or_create_global_settings(db_session) -> None:
    settings = Settings(DATABASE_URL="sqlite://")
    service = MobilePushService(settings)
    user = User(email="owner@example.com")
    db_session.add(user)
    db_session.flush()

    push_settings = service.get_or_create_global_settings(db_session, user.id)
    same_settings = service.get_or_create_global_settings(db_session, user.id)

    assert push_settings.id == same_settings.id
    assert push_settings.enabled is False
    assert push_settings.default_for_monitored_channels is True
    assert push_settings.first_enabled_at is None


def test_mobile_push_service_computes_effective_channel_state(db_session) -> None:
    settings = Settings(DATABASE_URL="sqlite://")
    service = MobilePushService(settings)
    user = User(email="owner@example.com")
    channel = Channel(youtube_channel_id="channel-a", title="A")
    db_session.add_all([user, channel])
    db_session.flush()
    user_channel = UserChannel(user_id=user.id, channel_id=channel.id, is_monitored=True)
    push_settings = MobilePushSetting(user_id=user.id, enabled=True, default_for_monitored_channels=True)
    db_session.add_all([user_channel, push_settings])
    db_session.flush()

    inherited = service.compute_channel_push_state(push_settings, user_channel, None)
    assert inherited.push_eligible is True
    assert inherited.push_enabled is True
    assert inherited.explicitly_set is False
    assert inherited.explicit_push_enabled is None

    explicit_disable = MobilePushChannelPreference(
        user_id=user.id,
        channel_id=channel.id,
        push_enabled=False,
        explicitly_set=True,
    )
    disabled = service.compute_channel_push_state(push_settings, user_channel, explicit_disable)
    assert disabled.push_enabled is False
    assert disabled.explicitly_set is True
    assert disabled.explicit_push_enabled is False

    push_settings.enabled = False
    globally_disabled = service.compute_channel_push_state(push_settings, user_channel, None)
    assert globally_disabled.push_eligible is True
    assert globally_disabled.push_enabled is False

    push_settings.enabled = True
    user_channel.is_monitored = False
    unmonitored = service.compute_channel_push_state(push_settings, user_channel, None)
    assert unmonitored.push_eligible is False
    assert unmonitored.push_enabled is False


def test_mobile_push_service_register_unregister_and_delivery_idempotency(db_session) -> None:
    settings = Settings(DATABASE_URL="sqlite://")
    service = MobilePushService(settings)
    user = User(email="owner@example.com")
    channel = Channel(youtube_channel_id="channel-a", title="A")
    db_session.add_all([user, channel])
    db_session.flush()
    video = Video(youtube_video_id="video-a", channel_id=channel.id, title="Video A", published_at=datetime.now(UTC))
    db_session.add(video)
    db_session.flush()
    notification_delivery = NotificationDelivery(user_id=user.id, video_id=video.id, status="delivered")
    db_session.add(notification_delivery)
    db_session.flush()

    installation_id = uuid4()
    first_installation = service.register_installation(
        db_session,
        user_id=user.id,
        installation_id=installation_id,
        expo_push_token="ExponentPushToken[first]",
        platform="ios",
        app_version="1.0.0",
        build_number="1",
        device_name="Phone",
    )
    second_installation = service.register_installation(
        db_session,
        user_id=user.id,
        installation_id=installation_id,
        expo_push_token="ExponentPushToken[second]",
        platform="android",
    )

    assert first_installation.id == second_installation.id
    assert second_installation.expo_push_token == "ExponentPushToken[second]"
    assert second_installation.platform == "android"
    assert second_installation.enabled is True
    assert second_installation.unregistered_at is None

    delivery = service.get_or_create_new_video_delivery(
        db_session,
        notification_delivery=notification_delivery,
        installation=second_installation,
        video=video,
        channel=channel,
    )
    same_delivery = service.get_or_create_new_video_delivery(
        db_session,
        notification_delivery=notification_delivery,
        installation=second_installation,
        video=video,
        channel=channel,
    )

    assert delivery.id == same_delivery.id
    assert delivery.event_type == PUSH_EVENT_NEW_VIDEO
    assert delivery.status == PUSH_DELIVERY_PENDING

    unregistered = service.unregister_installation(db_session, user_id=user.id, installation_id=installation_id)
    assert unregistered is not None
    assert unregistered.enabled is False
    assert unregistered.unregistered_at is not None
    assert service.unregister_installation(db_session, user_id=user.id, installation_id=uuid4()) is None


def test_mobile_push_service_payload_builders_and_disabled_result_avoid_secret_data(db_session) -> None:
    settings = Settings(
        DATABASE_URL="sqlite://",
        PUSH_NOTIFICATIONS_ENABLED=False,
        INTERNAL_API_BEARER_TOKEN="internal-secret",
        MOBILE_API_BEARER_TOKEN="mobile-secret",
        EXPO_ACCESS_TOKEN="expo-provider-secret",
    )
    service = MobilePushService(settings)
    user = User(email="owner@example.com")
    channel = Channel(youtube_channel_id="channel-a", title="Channel A")
    db_session.add_all([user, channel])
    db_session.flush()
    video = Video(youtube_video_id="video-a", channel_id=channel.id, title="Video A")
    db_session.add(video)
    db_session.flush()
    notification_delivery = NotificationDelivery(user_id=user.id, video_id=video.id, status="delivered")
    installation = MobilePushInstallation(
        user_id=user.id,
        installation_id=uuid4(),
        expo_push_token="ExponentPushToken[super-secret-device-token]",
        registered_at=datetime.now(UTC),
    )
    db_session.add_all([notification_delivery, installation])
    db_session.flush()

    new_video_payload = service.build_new_video_payload(
        installation=installation,
        user=user,
        channel=channel,
        video=video,
        notification_delivery=notification_delivery,
    )
    test_payload = service.build_test_payload(installation=installation)

    for payload in (new_video_payload, test_payload):
        data_text = str(payload["data"])
        assert "internal-secret" not in data_text
        assert "mobile-secret" not in data_text
        assert "expo-provider-secret" not in data_text
        assert installation.expo_push_token not in data_text

    disabled_result = service.send_disabled_result()
    assert disabled_result.status == PUSH_DELIVERY_SKIPPED
    assert disabled_result.sent is False
    assert disabled_result.disabled is True
