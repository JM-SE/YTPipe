from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.channel import Channel
from app.models.notification_delivery import NotificationDelivery
from app.models.oauth_account import OAuthAccount
from app.models.sync_state import SyncState
from app.models.user import User
from app.models.user_channel import UserChannel
from app.models.video import Video
from app.services.auth import GoogleOAuthService
from app.services.email import EmailDeliveryAttemptError, EmailDeliveryService, EmailNotificationPayload
from app.services.mobile_push import MobilePushService
from app.services.telegram import TelegramDeliveryAttemptError, TelegramDeliveryService, TelegramNotificationPayload
from app.services.transcript import TranscriptService

POLLING_PROCESS = "polling"
QUOTA_PROCESS = "quota"
UNUSABLE_UPLOADS_PLAYLIST_ERROR = "unusable_uploads_playlist"
DEFAULT_DELIVERY_STATUS = "pending"
DELIVERY_PENDING_RETRY_STATUS = "pending_retry"
DELIVERY_DELIVERED_STATUS = "delivered"
DELIVERY_FAILED_STATUS = "failed"


@dataclass
class PollRunSummary:
    run_outcome: str
    channels_processed: int
    channels_failed: int
    baselines_established: int
    new_videos_detected: int
    quota_blocked: bool


@dataclass
class LatestUpload:
    video_id: str | None
    title: str | None
    published_at: datetime | None


class PollingChannelError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class YouTubePollingService:
    def __init__(
        self,
        auth_service: GoogleOAuthService,
        email_service: EmailDeliveryService,
        daily_quota_budget: int,
        safety_stop_enabled: bool,
        mobile_push_service: MobilePushService | None = None,
        telegram_service: TelegramDeliveryService | None = None,
        transcript_service: TranscriptService | None = None,
    ):
        self.auth_service = auth_service
        self.email_service = email_service
        self.daily_quota_budget = max(0, daily_quota_budget)
        self.safety_stop_enabled = safety_stop_enabled
        self.mobile_push_service = mobile_push_service
        self.telegram_service = telegram_service
        self.transcript_service = transcript_service

    def run_poll(self, session: Session, user: User, oauth_account: OAuthAccount) -> PollRunSummary:
        now = datetime.now(UTC)
        quota_state = self._get_or_create_sync_state(session, user.id, QUOTA_PROCESS)
        polling_state = self._get_or_create_sync_state(session, user.id, POLLING_PROCESS)

        quota_context = self._build_quota_context(quota_state, now)
        if quota_context["quota_blocked"]:
            summary = PollRunSummary(
                run_outcome="blocked",
                channels_processed=0,
                channels_failed=0,
                baselines_established=0,
                new_videos_detected=0,
                quota_blocked=True,
            )
            self._write_quota_state(quota_state, quota_context, now)
            self._write_polling_state(polling_state, summary, [], now)
            session.flush()
            return summary

        credentials = self.auth_service.ensure_valid_credentials(session, oauth_account)
        youtube = build("youtube", "v3", credentials=credentials, cache_discovery=False)

        monitored_rows = session.execute(
            select(UserChannel, Channel)
            .join(Channel, UserChannel.channel_id == Channel.id)
            .where(UserChannel.user_id == user.id, UserChannel.is_monitored.is_(True))
            .order_by(UserChannel.id.asc())
        ).all()

        channels_processed = 0
        channels_failed = 0
        baselines_established = 0
        new_videos_detected = 0
        channel_errors: list[dict[str, Any]] = []

        self._process_pending_initial_deliveries(session, user)
        self._process_pending_retry_deliveries(session, user)

        for user_channel, channel in monitored_rows:
            try:
                if channel.uploads_playlist_id:
                    quota_context["last_run_estimated_units"] += 1
                    quota_context["estimated_units_used_today"] += 1

                latest_upload = self._fetch_latest_upload(youtube, channel)

                if user_channel.baseline_established_at is None:
                    user_channel.last_seen_video_id = latest_upload.video_id
                    user_channel.baseline_established_at = now
                    baselines_established += 1
                    channels_processed += 1
                    continue

                if latest_upload.video_id is None or latest_upload.video_id == user_channel.last_seen_video_id:
                    channels_processed += 1
                    continue

                video = self._get_or_create_video(session, channel.id, latest_upload)
                self._attempt_fetch_and_store_transcript(session, video)
                delivery = self._get_or_create_delivery(session, user.id, video.id)
                self._attempt_initial_delivery_send(
                    delivery=delivery,
                    user=user,
                    channel=channel,
                    video=video,
                )
                self._attempt_new_video_push(
                    session=session,
                    user=user,
                    user_channel=user_channel,
                    channel=channel,
                    video=video,
                    delivery=delivery,
                )
                user_channel.last_seen_video_id = latest_upload.video_id
                new_videos_detected += 1
                channels_processed += 1
            except PollingChannelError as exc:
                channels_failed += 1
                channel_errors.append(
                    {
                        "channel_id": channel.id,
                        "youtube_channel_id": channel.youtube_channel_id,
                        "title": channel.title,
                        "error_code": exc.code,
                        "error_message": exc.message,
                    }
                )
            except Exception as exc:  # noqa: BLE001
                channels_failed += 1
                channel_errors.append(
                    {
                        "channel_id": channel.id,
                        "youtube_channel_id": channel.youtube_channel_id,
                        "title": channel.title,
                        "error_code": "channel_poll_failed",
                        "error_message": str(exc),
                    }
                )

        run_outcome = self._resolve_run_outcome(channels_processed, channels_failed)
        summary = PollRunSummary(
            run_outcome=run_outcome,
            channels_processed=channels_processed,
            channels_failed=channels_failed,
            baselines_established=baselines_established,
            new_videos_detected=new_videos_detected,
            quota_blocked=False,
        )

        self._write_quota_state(quota_state, quota_context, now)
        self._write_polling_state(polling_state, summary, channel_errors, now)
        session.flush()
        return summary

    def record_polling_error(self, session: Session, user_id: int, message: str) -> None:
        polling_state = self._get_or_create_sync_state(session, user_id, POLLING_PROCESS)
        polling_state.last_error_at = datetime.now(UTC)
        polling_state.last_error_message = message
        session.flush()

    def _fetch_latest_upload(self, youtube: Any, channel: Channel) -> LatestUpload:
        uploads_playlist_id = channel.uploads_playlist_id
        if not uploads_playlist_id:
            raise PollingChannelError(
                UNUSABLE_UPLOADS_PLAYLIST_ERROR,
                "Channel does not expose a usable uploads playlist.",
            )

        try:
            response = (
                youtube.playlistItems()
                .list(part="snippet,contentDetails", playlistId=uploads_playlist_id, maxResults=1)
                .execute()
            )
        except HttpError as exc:
            if self._is_unusable_uploads_playlist_error(exc):
                raise PollingChannelError(
                    UNUSABLE_UPLOADS_PLAYLIST_ERROR,
                    "Channel uploads playlist is not usable for polling.",
                ) from exc
            raise

        items = response.get("items", [])
        if not items:
            return LatestUpload(video_id=None, title=None, published_at=None)

        item = items[0]
        snippet = item.get("snippet", {})
        content_details = item.get("contentDetails", {})

        return LatestUpload(
            video_id=content_details.get("videoId"),
            title=snippet.get("title"),
            published_at=self._parse_youtube_datetime(snippet.get("publishedAt")),
        )

    def _get_or_create_video(self, session: Session, channel_id: int, latest_upload: LatestUpload) -> Video:
        if not latest_upload.video_id:
            raise ValueError("Cannot create canonical video without a video id.")

        video = session.scalar(select(Video).where(Video.youtube_video_id == latest_upload.video_id))
        if video is None:
            video = Video(
                youtube_video_id=latest_upload.video_id,
                channel_id=channel_id,
                title=latest_upload.title,
                published_at=latest_upload.published_at,
            )
            session.add(video)
            session.flush()
            return video

        if video.title is None and latest_upload.title is not None:
            video.title = latest_upload.title
        if video.published_at is None and latest_upload.published_at is not None:
            video.published_at = latest_upload.published_at
        return video

    def _get_or_create_delivery(self, session: Session, user_id: int, video_id: int) -> NotificationDelivery:
        delivery = session.scalar(
            select(NotificationDelivery).where(
                NotificationDelivery.user_id == user_id,
                NotificationDelivery.video_id == video_id,
            )
        )
        if delivery is None:
            delivery = NotificationDelivery(
                user_id=user_id,
                video_id=video_id,
                status=DEFAULT_DELIVERY_STATUS,
            )
            session.add(delivery)
            session.flush()
        return delivery

    def _process_pending_retry_deliveries(self, session: Session, user: User) -> None:
        retry_rows = session.execute(
            select(NotificationDelivery, Video, Channel)
            .join(Video, NotificationDelivery.video_id == Video.id)
            .join(Channel, Video.channel_id == Channel.id)
            .where(
                NotificationDelivery.user_id == user.id,
                NotificationDelivery.status == DELIVERY_PENDING_RETRY_STATUS,
                NotificationDelivery.attempt_count == 1,
            )
            .order_by(NotificationDelivery.id.asc())
        ).all()

        for delivery, video, channel in retry_rows:
            self._attempt_delivery_send(
                delivery=delivery,
                user=user,
                channel=channel,
                video=video,
                is_retry=True,
            )

    def _process_pending_initial_deliveries(self, session: Session, user: User) -> None:
        pending_rows = session.execute(
            select(NotificationDelivery, Video, Channel)
            .join(Video, NotificationDelivery.video_id == Video.id)
            .join(Channel, Video.channel_id == Channel.id)
            .where(
                NotificationDelivery.user_id == user.id,
                NotificationDelivery.status == DEFAULT_DELIVERY_STATUS,
                NotificationDelivery.attempt_count == 0,
            )
            .order_by(NotificationDelivery.id.asc())
        ).all()

        for delivery, video, channel in pending_rows:
            self._attempt_delivery_send(
                delivery=delivery,
                user=user,
                channel=channel,
                video=video,
                is_retry=False,
            )

    def _attempt_initial_delivery_send(
        self,
        delivery: NotificationDelivery,
        user: User,
        channel: Channel,
        video: Video,
    ) -> None:
        if delivery.status != DEFAULT_DELIVERY_STATUS:
            return

        self._attempt_delivery_send(
            delivery=delivery,
            user=user,
            channel=channel,
            video=video,
            is_retry=False,
        )

    def _attempt_new_video_push(
        self,
        *,
        session: Session,
        user: User,
        user_channel: UserChannel,
        channel: Channel,
        video: Video,
        delivery: NotificationDelivery,
    ) -> None:
        if self.mobile_push_service is None:
            return

        try:
            self.mobile_push_service.attempt_new_video_push(
                session,
                user=user,
                user_channel=user_channel,
                channel=channel,
                video=video,
                notification_delivery=delivery,
            )
        except Exception:  # noqa: BLE001
            return

    def _attempt_fetch_and_store_transcript(
        self,
        session: Session,
        video: Video,
    ) -> None:
        if self.transcript_service is None:
            return
        if video.transcript is not None:
            return

        try:
            transcript = self.transcript_service.fetch_transcript(video.youtube_video_id)
            if transcript:
                video.transcript = transcript
                session.flush()
        except Exception:  # noqa: BLE001
            return

    def _attempt_new_video_telegram(
        self,
        *,
        channel: Channel,
        video: Video,
    ) -> None:
        if self.telegram_service is None:
            return

        transcript_saved = video.transcript is not None
        transcript_word_count = len(video.transcript.split()) if video.transcript else 0

        try:
            self.telegram_service.send_video_notification(
                TelegramNotificationPayload(
                    channel_title=channel.title,
                    video_title=video.title,
                    youtube_video_id=video.youtube_video_id,
                    transcript_saved=transcript_saved,
                    transcript_word_count=transcript_word_count,
                )
            )
        except TelegramDeliveryAttemptError:
            return

    def _attempt_delivery_send(
        self,
        delivery: NotificationDelivery,
        user: User,
        channel: Channel,
        video: Video,
        *,
        is_retry: bool,
    ) -> None:
        youtube_video_id = video.youtube_video_id
        attempted_at = datetime.now(UTC)

        payload = EmailNotificationPayload(
            recipient_email=user.email,
            channel_title=channel.title,
            video_title=video.title,
            youtube_video_id=youtube_video_id,
        )

        try:
            self.email_service.send_video_notification(payload)
        except EmailDeliveryAttemptError as exc:
            delivery.attempt_count += 1
            delivery.last_attempt_at = attempted_at
            delivery.last_error = exc.message
            if is_retry:
                delivery.status = DELIVERY_FAILED_STATUS
            else:
                delivery.status = DELIVERY_PENDING_RETRY_STATUS if exc.retryable else DELIVERY_FAILED_STATUS
            return

        delivery.attempt_count += 1
        delivery.last_attempt_at = attempted_at
        delivery.last_error = None
        delivery.status = DELIVERY_DELIVERED_STATUS

        self._attempt_new_video_telegram(channel=channel, video=video)

    def _build_quota_context(self, quota_state: SyncState, now: datetime) -> dict[str, Any]:
        existing = quota_state.state_metadata or {}
        day = now.date().isoformat()
        existing_day = existing.get("usage_day")
        estimated_used_today = int(existing.get("estimated_units_used_today", 0)) if existing_day == day else 0
        quota_blocked = self.safety_stop_enabled and estimated_used_today >= self.daily_quota_budget

        return {
            "usage_day": day,
            "daily_quota_budget": self.daily_quota_budget,
            "estimated_units_used_today": estimated_used_today,
            "last_run_estimated_units": 0,
            "safety_stop_enabled": self.safety_stop_enabled,
            "safety_stop_active": quota_blocked,
            "safety_stop_triggered_at": now.isoformat() if quota_blocked else existing.get("safety_stop_triggered_at"),
            "quota_blocked": quota_blocked,
        }

    def _write_quota_state(self, quota_state: SyncState, quota_context: dict[str, Any], now: datetime) -> None:
        quota_state.last_success_at = now
        quota_state.last_error_at = None
        quota_state.last_error_message = None
        quota_state.state_metadata = {
            "daily_quota_budget": quota_context["daily_quota_budget"],
            "estimated_units_used_today": quota_context["estimated_units_used_today"],
            "last_run_estimated_units": quota_context["last_run_estimated_units"],
            "safety_stop_active": quota_context["safety_stop_active"],
            "safety_stop_enabled": quota_context["safety_stop_enabled"],
            "safety_stop_triggered_at": quota_context["safety_stop_triggered_at"],
            "usage_day": quota_context["usage_day"],
        }

    def _write_polling_state(
        self,
        polling_state: SyncState,
        summary: PollRunSummary,
        channel_errors: list[dict[str, Any]],
        now: datetime,
    ) -> None:
        polling_state.state_metadata = {
            "run_outcome": summary.run_outcome,
            "channels_processed": summary.channels_processed,
            "channels_failed": summary.channels_failed,
            "baselines_established": summary.baselines_established,
            "new_videos_detected": summary.new_videos_detected,
            "quota_blocked": summary.quota_blocked,
            "channel_errors": channel_errors,
        }

        if summary.run_outcome in {"success", "partial_success"}:
            polling_state.last_success_at = now

        if summary.run_outcome in {"failed", "partial_success"}:
            polling_state.last_error_at = now
            polling_state.last_error_message = (
                f"{summary.channels_failed} channel(s) failed during polling run."
                if summary.channels_failed
                else "Polling run completed with a non-success outcome."
            )
        else:
            polling_state.last_error_at = None
            polling_state.last_error_message = None

    def _get_or_create_sync_state(self, session: Session, user_id: int, process_type: str) -> SyncState:
        sync_state = session.scalar(
            select(SyncState).where(
                SyncState.user_id == user_id,
                SyncState.process_type == process_type,
            )
        )
        if sync_state is None:
            sync_state = SyncState(user_id=user_id, process_type=process_type)
            session.add(sync_state)
            session.flush()
        return sync_state

    @staticmethod
    def _resolve_run_outcome(channels_processed: int, channels_failed: int) -> str:
        if channels_failed == 0:
            return "success"
        if channels_processed > 0:
            return "partial_success"
        return "failed"

    @staticmethod
    def _is_unusable_uploads_playlist_error(exc: HttpError) -> bool:
        if exc.resp.status != 404:
            return False

        try:
            payload = json.loads(exc.content.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError, AttributeError):
            return False

        errors = payload.get("error", {}).get("errors", [])
        return any(err.get("reason") == "playlistNotFound" for err in errors)

    @staticmethod
    def _parse_youtube_datetime(value: str | None) -> datetime | None:
        if not value:
            return None
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.astimezone(UTC)
