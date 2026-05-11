from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

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

PUSH_EVENT_NEW_VIDEO = "new_video"
PUSH_EVENT_TEST = "test"

PUSH_DELIVERY_PENDING = "pending"
PUSH_DELIVERY_SENT = "sent"
PUSH_DELIVERY_FAILED = "failed"
PUSH_DELIVERY_SKIPPED = "skipped"
PUSH_DELIVERY_INVALID_TOKEN = "invalid_token"

PUSH_PLATFORM_IOS = "ios"
PUSH_PLATFORM_ANDROID = "android"
PUSH_PLATFORM_UNKNOWN = "unknown"


@dataclass(frozen=True)
class ChannelPushState:
    push_eligible: bool
    push_enabled: bool
    explicitly_set: bool
    explicit_push_enabled: bool | None


@dataclass(frozen=True)
class PushSendResult:
    status: str
    sent: bool
    disabled: bool
    message: str


class MobilePushService:
    """Network-free mobile push foundation for Phase 11A.

    Later phases can reuse these helpers from API endpoints and polling integration.
    This class intentionally does not perform Expo HTTP requests in Phase 11A.
    """

    def __init__(self, settings: Settings):
        self.settings = settings

    def get_or_create_global_settings(self, session: Session, user_id: int) -> MobilePushSetting:
        push_settings = session.scalar(select(MobilePushSetting).where(MobilePushSetting.user_id == user_id))
        if push_settings is None:
            push_settings = MobilePushSetting(
                user_id=user_id,
                enabled=False,
                default_for_monitored_channels=True,
            )
            session.add(push_settings)
            session.flush()
        return push_settings

    def compute_channel_push_state(
        self,
        push_settings: MobilePushSetting,
        user_channel: UserChannel,
        preference: MobilePushChannelPreference | None,
    ) -> ChannelPushState:
        explicit_push_enabled = preference.push_enabled if preference and preference.explicitly_set else None
        inherited_or_explicit = (
            explicit_push_enabled
            if explicit_push_enabled is not None
            else push_settings.default_for_monitored_channels
        )
        push_eligible = bool(user_channel.is_monitored)
        push_enabled = bool(push_settings.enabled and push_eligible and inherited_or_explicit)

        return ChannelPushState(
            push_eligible=push_eligible,
            push_enabled=push_enabled,
            explicitly_set=bool(preference.explicitly_set) if preference else False,
            explicit_push_enabled=explicit_push_enabled,
        )

    def register_installation(
        self,
        session: Session,
        *,
        user_id: int,
        installation_id: UUID,
        expo_push_token: str,
        platform: str = PUSH_PLATFORM_UNKNOWN,
        app_version: str | None = None,
        build_number: str | None = None,
        device_name: str | None = None,
    ) -> MobilePushInstallation:
        now = datetime.now(UTC)
        installation = session.scalar(
            select(MobilePushInstallation).where(
                MobilePushInstallation.user_id == user_id,
                MobilePushInstallation.installation_id == installation_id,
            )
        )
        if installation is None:
            installation = MobilePushInstallation(
                user_id=user_id,
                installation_id=installation_id,
                expo_push_token=expo_push_token,
                registered_at=now,
            )
            session.add(installation)

        installation.expo_push_token = expo_push_token
        installation.platform = normalize_platform(platform)
        installation.app_version = app_version
        installation.build_number = build_number
        installation.device_name = device_name
        installation.enabled = True
        installation.registered_at = now
        installation.last_seen_at = now
        installation.unregistered_at = None
        installation.invalidated_at = None
        session.flush()
        return installation

    def unregister_installation(
        self,
        session: Session,
        *,
        user_id: int,
        installation_id: UUID,
    ) -> MobilePushInstallation | None:
        installation = session.scalar(
            select(MobilePushInstallation).where(
                MobilePushInstallation.user_id == user_id,
                MobilePushInstallation.installation_id == installation_id,
            )
        )
        if installation is None:
            return None

        installation.enabled = False
        installation.unregistered_at = datetime.now(UTC)
        session.flush()
        return installation

    def get_or_create_new_video_delivery(
        self,
        session: Session,
        *,
        notification_delivery: NotificationDelivery,
        installation: MobilePushInstallation,
        video: Video,
        channel: Channel,
    ) -> MobilePushDelivery:
        if notification_delivery.user_id != installation.user_id:
            raise ValueError("Notification delivery and installation must belong to the same user.")
        if notification_delivery.video_id != video.id:
            raise ValueError("Notification delivery must reference the supplied video.")
        if video.channel_id != channel.id:
            raise ValueError("Video must belong to the supplied channel.")

        delivery = session.scalar(
            select(MobilePushDelivery).where(
                MobilePushDelivery.notification_delivery_id == notification_delivery.id,
                MobilePushDelivery.installation_id == installation.id,
            )
        )
        if delivery is None:
            delivery = MobilePushDelivery(
                user_id=notification_delivery.user_id,
                installation_id=installation.id,
                notification_delivery_id=notification_delivery.id,
                video_id=video.id,
                channel_id=channel.id,
                event_type=PUSH_EVENT_NEW_VIDEO,
                status=PUSH_DELIVERY_PENDING,
            )
            try:
                with session.begin_nested():
                    session.add(delivery)
                    session.flush()
            except IntegrityError:
                delivery = session.scalar(
                    select(MobilePushDelivery).where(
                        MobilePushDelivery.notification_delivery_id == notification_delivery.id,
                        MobilePushDelivery.installation_id == installation.id,
                    )
                )
                if delivery is None:
                    raise
        return delivery

    def build_new_video_payload(
        self,
        *,
        installation: MobilePushInstallation,
        user: User,
        channel: Channel,
        video: Video,
        notification_delivery: NotificationDelivery,
    ) -> dict[str, Any]:
        return {
            "to": installation.expo_push_token,
            "title": f"New video from {channel.title or 'Unknown channel'}",
            "body": video.title or video.youtube_video_id,
            "sound": "default",
            "data": {
                "type": PUSH_EVENT_NEW_VIDEO,
                "activity_id": notification_delivery.id,
                "delivery_id": notification_delivery.id,
                "video_id": video.id,
                "channel_id": channel.id,
                "sent_at": datetime.now(UTC).isoformat(),
            },
        }

    def build_test_payload(self, *, installation: MobilePushInstallation) -> dict[str, Any]:
        return {
            "to": installation.expo_push_token,
            "title": "YTPipe test notification",
            "body": "Push notifications are connected.",
            "sound": "default",
            "data": {
                "type": PUSH_EVENT_TEST,
                "sent_at": datetime.now(UTC).isoformat(),
            },
        }

    def send_disabled_result(self) -> PushSendResult:
        if self.settings.push_notifications_enabled:
            return PushSendResult(
                status=PUSH_DELIVERY_PENDING,
                sent=False,
                disabled=False,
                message="Push provider send is reserved for a later phase.",
            )

        return PushSendResult(
            status=PUSH_DELIVERY_SKIPPED,
            sent=False,
            disabled=True,
            message="Push notifications are disabled by configuration.",
        )


def mask_expo_token(token: str | None) -> str | None:
    if token is None:
        return None

    stripped = token.strip()
    if not stripped:
        return None

    if len(stripped) <= 12:
        return f"{stripped[:2]}…{stripped[-2:]}"

    return f"{stripped[:18]}…{stripped[-5:]}"


def normalize_platform(platform: str | None) -> str:
    normalized = (platform or PUSH_PLATFORM_UNKNOWN).strip().lower()
    if normalized in {PUSH_PLATFORM_IOS, PUSH_PLATFORM_ANDROID}:
        return normalized
    return PUSH_PLATFORM_UNKNOWN
