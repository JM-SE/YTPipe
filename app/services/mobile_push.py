from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import httpx
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
    invalid_token: bool = False
    expo_status: str | None = None
    expo_ticket_id: str | None = None
    last_attempt_at: datetime | None = None
    http_status_code: int | None = None


class MobilePushService:
    """Mobile push domain helpers shared by API endpoints and polling integration."""

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

    def update_global_settings(
        self,
        session: Session,
        *,
        user_id: int,
        enabled: bool | None = None,
        default_for_monitored_channels: bool | None = None,
    ) -> MobilePushSetting:
        push_settings = self.get_or_create_global_settings(session, user_id)
        was_enabled = push_settings.enabled

        if default_for_monitored_channels is not None:
            push_settings.default_for_monitored_channels = default_for_monitored_channels
        if enabled is not None:
            push_settings.enabled = enabled
            if enabled and not was_enabled and push_settings.first_enabled_at is None:
                push_settings.first_enabled_at = datetime.now(UTC)

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

    def get_installation(
        self,
        session: Session,
        *,
        user_id: int,
        installation_id: UUID,
    ) -> MobilePushInstallation | None:
        return session.scalar(
            select(MobilePushInstallation).where(
                MobilePushInstallation.user_id == user_id,
                MobilePushInstallation.installation_id == installation_id,
            )
        )

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

    def send_test_push(
        self,
        session: Session,
        *,
        installation: MobilePushInstallation,
    ) -> PushSendResult:
        if not self.settings.push_notifications_enabled:
            return self.send_disabled_result()

        if (
            not installation.enabled
            or installation.unregistered_at is not None
            or installation.invalidated_at is not None
            or not installation.expo_push_token
        ):
            return PushSendResult(
                status=PUSH_DELIVERY_SKIPPED,
                sent=False,
                disabled=False,
                message="Installation is not registered for push notifications.",
                http_status_code=409,
            )

        delivery = MobilePushDelivery(
            user_id=installation.user_id,
            installation_id=installation.id,
            event_type=PUSH_EVENT_TEST,
            status=PUSH_DELIVERY_PENDING,
        )
        session.add(delivery)
        session.flush()

        payload = self.build_test_payload(installation=installation)
        return self._send_delivery(
            delivery=delivery,
            installation=installation,
            payload=payload,
            success_message="Test push notification sent.",
        )

    def attempt_new_video_push(
        self,
        session: Session,
        *,
        user: User,
        user_channel: UserChannel,
        channel: Channel,
        video: Video,
        notification_delivery: NotificationDelivery,
    ) -> list[PushSendResult]:
        """Best-effort new-video push fan-out for the polling new-video branch."""
        if not self.settings.push_notifications_enabled:
            return []
        if video.is_short is True and not self.settings.shorts_processing_enabled:
            return []

        try:
            push_settings = self.get_or_create_global_settings(session, user.id)
            if not push_settings.enabled or not user_channel.is_monitored:
                return []

            preference = session.scalar(
                select(MobilePushChannelPreference).where(
                    MobilePushChannelPreference.user_id == user.id,
                    MobilePushChannelPreference.channel_id == channel.id,
                )
            )
            channel_state = self.compute_channel_push_state(push_settings, user_channel, preference)
            if not channel_state.push_enabled:
                return []

            installations = session.scalars(
                select(MobilePushInstallation)
                .where(
                    MobilePushInstallation.user_id == user.id,
                    MobilePushInstallation.enabled.is_(True),
                    MobilePushInstallation.unregistered_at.is_(None),
                    MobilePushInstallation.invalidated_at.is_(None),
                )
                .order_by(MobilePushInstallation.id.asc())
            ).all()

            results: list[PushSendResult] = []
            for installation in installations:
                if not installation.expo_push_token:
                    continue
                try:
                    delivery = self.get_or_create_new_video_delivery(
                        session,
                        notification_delivery=notification_delivery,
                        installation=installation,
                        video=video,
                        channel=channel,
                    )
                    if delivery.status != PUSH_DELIVERY_PENDING or delivery.attempt_count != 0:
                        continue

                    payload = self.build_new_video_payload(
                        installation=installation,
                        user=user,
                        channel=channel,
                        video=video,
                        notification_delivery=notification_delivery,
                    )
                    results.append(
                        self._send_delivery(
                            delivery=delivery,
                            installation=installation,
                            payload=payload,
                            success_message="New-video push notification sent.",
                        )
                    )
                except Exception:  # noqa: BLE001
                    continue

            session.flush()
            return results
        except Exception:  # noqa: BLE001
            return []

    def _send_delivery(
        self,
        *,
        delivery: MobilePushDelivery,
        installation: MobilePushInstallation,
        payload: dict[str, Any],
        success_message: str,
    ) -> PushSendResult:
        now = datetime.now(UTC)
        delivery.attempt_count += 1
        delivery.last_attempt_at = now
        installation.last_attempt_at = now
        session = Session.object_session(delivery)
        if session is not None:
            session.flush()

        try:
            response = httpx.post(
                self.settings.expo_push_endpoint,
                json=payload,
                headers=self._expo_headers(),
                timeout=10.0,
            )
            if response.status_code >= 400:
                return self._mark_delivery_failure(
                    delivery=delivery,
                    installation=installation,
                    message="Push provider request failed.",
                    error=f"Expo provider returned HTTP {response.status_code}.",
                    expo_status="provider_error",
                )
            response_payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            return self._mark_delivery_failure(
                delivery=delivery,
                installation=installation,
                message="Push provider request failed.",
                error=str(exc),
                expo_status="provider_error",
            )
        except Exception as exc:  # noqa: BLE001
            return self._mark_delivery_failure(
                delivery=delivery,
                installation=installation,
                message="Push provider request failed.",
                error=str(exc),
                expo_status="provider_error",
            )

        parsed = _parse_expo_response(response_payload)
        if parsed["ok"]:
            ticket_id = parsed.get("ticket_id")
            delivery.status = PUSH_DELIVERY_SENT
            delivery.last_success_at = now
            delivery.expo_status = "ok"
            delivery.expo_ticket_id = ticket_id
            delivery.expo_response = _safe_expo_response(parsed)
            installation.last_success_at = now
            installation.last_error = None
            installation.last_expo_status = "ok"
            installation.last_expo_ticket_id = ticket_id
            if session is not None:
                session.flush()
            return PushSendResult(
                status=PUSH_DELIVERY_SENT,
                sent=True,
                disabled=False,
                message=success_message,
                expo_status="ok",
                expo_ticket_id=ticket_id,
                last_attempt_at=now,
            )

        invalid_token = parsed.get("error") == "DeviceNotRegistered"
        failure_status = PUSH_DELIVERY_INVALID_TOKEN if invalid_token else PUSH_DELIVERY_FAILED
        message = "Expo push token is no longer registered." if invalid_token else "Push provider rejected the request."
        return self._mark_delivery_failure(
            delivery=delivery,
            installation=installation,
            message=message,
            error=parsed.get("message") or message,
            expo_status=parsed.get("expo_status") or "error",
            expo_ticket_id=parsed.get("ticket_id"),
            invalid_token=invalid_token,
            delivery_status=failure_status,
        )

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
            http_status_code=409,
        )

    def _expo_headers(self) -> dict[str, str] | None:
        access_token = self.settings.expo_access_token.strip()
        if not access_token:
            return None
        return {"Authorization": f"Bearer {access_token}"}

    def _sanitize_provider_error(self, message: str | None) -> str:
        return _sanitize_provider_error(
            message,
            sensitive_values=(
                self.settings.expo_access_token,
                self.settings.expo_push_endpoint,
                self.settings.internal_api_bearer_token,
                self.settings.mobile_api_bearer_token,
            ),
        )

    def _mark_delivery_failure(
        self,
        *,
        delivery: MobilePushDelivery,
        installation: MobilePushInstallation,
        message: str,
        error: str,
        expo_status: str,
        expo_ticket_id: str | None = None,
        invalid_token: bool = False,
        delivery_status: str = PUSH_DELIVERY_FAILED,
    ) -> PushSendResult:
        now = delivery.last_attempt_at or datetime.now(UTC)
        sanitized_error = self._sanitize_provider_error(error)
        delivery.status = delivery_status
        delivery.last_error = sanitized_error
        delivery.expo_status = expo_status
        delivery.expo_ticket_id = expo_ticket_id
        delivery.expo_response = {
            "status": expo_status,
            "message": sanitized_error,
            "error": "DeviceNotRegistered" if invalid_token else None,
        }
        installation.last_error = sanitized_error
        installation.last_expo_status = expo_status
        installation.last_expo_ticket_id = expo_ticket_id
        if invalid_token:
            installation.enabled = False
            installation.invalidated_at = datetime.now(UTC)
        delivery.last_attempt_at = now
        installation.last_attempt_at = now
        session = Session.object_session(delivery)
        if session is not None:
            session.flush()
        return PushSendResult(
            status=delivery_status,
            sent=False,
            disabled=False,
            message=message,
            invalid_token=invalid_token,
            expo_status=expo_status,
            expo_ticket_id=expo_ticket_id,
            last_attempt_at=now,
            http_status_code=502,
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


def _parse_expo_response(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {"ok": False, "expo_status": "malformed", "message": "Malformed Expo response."}

    data = payload.get("data")
    if isinstance(data, list):
        entry = data[0] if data else None
    else:
        entry = data

    if not isinstance(entry, dict):
        return {"ok": False, "expo_status": "malformed", "message": "Malformed Expo response."}

    expo_status = str(entry.get("status") or "")
    ticket_id = entry.get("id") if isinstance(entry.get("id"), str) else None
    details = entry.get("details") if isinstance(entry.get("details"), dict) else {}
    error = details.get("error") if isinstance(details.get("error"), str) else None
    message = entry.get("message") if isinstance(entry.get("message"), str) else "Expo push request failed."

    if expo_status == "ok":
        return {"ok": True, "expo_status": expo_status, "ticket_id": ticket_id}

    return {
        "ok": False,
        "expo_status": expo_status or "error",
        "ticket_id": ticket_id,
        "message": _sanitize_provider_error(message),
        "error": error,
    }


def _safe_expo_response(parsed: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": parsed.get("expo_status"),
        "ticket_id": parsed.get("ticket_id"),
    }


def _sanitize_provider_error(message: str | None, sensitive_values: tuple[str, ...] = ()) -> str:
    if not message:
        return "Push provider request failed."
    sanitized = message.replace("\n", " ").replace("\r", " ")
    normalized = sanitized.lower()
    unsafe_markers = ("bearer ", "exponentpushtoken[", "traceback", "stack trace", "http://", "https://")
    for marker in unsafe_markers:
        if marker in normalized:
            return "Push provider request failed."
    for value in sensitive_values:
        stripped = value.strip() if value else ""
        if stripped and stripped in sanitized:
            return "Push provider request failed."
    return sanitized[:300]
