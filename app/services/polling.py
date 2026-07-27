from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

SHORTS_TITLE_MARKERS = ("#shorts",)
SHORTS_MAX_DURATION_SECONDS = 60
QUOTA_ALERT_THRESHOLDS = (50, 75, 90)

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.channel import Channel
from app.models.mobile_push_delivery import MobilePushDelivery
from app.models.notification_delivery import NotificationDelivery
from app.models.oauth_account import OAuthAccount
from app.models.sync_state import SyncState
from app.models.user import User
from app.models.user_channel import UserChannel
from app.models.video import Video
from app.services.auth import GoogleOAuthService
from app.services.email import EmailDeliveryAttemptError, EmailDeliveryService, EmailNotificationPayload
from app.services.mobile_push import (
    PUSH_DELIVERY_PENDING,
    PUSH_DELIVERY_SKIPPED,
    MobilePushService,
)
from app.services.llama_recovery import LlamaRecoveryService
from app.services.pipeline import SUMMARIZATION_PROCESS, PipelineDrainStats, PipelineService
from app.services.summarization import SummarizationService
from app.services.telegram import TelegramDeliveryService
from app.services.transcript import TranscriptService

POLLING_PROCESS = "polling"
QUOTA_PROCESS = "quota"
RECONCILIATION_PROCESS = "reconciliation"
UNUSABLE_UPLOADS_PLAYLIST_ERROR = "unusable_uploads_playlist"
DEFAULT_DELIVERY_STATUS = "pending"
DELIVERY_PENDING_RETRY_STATUS = "pending_retry"
DELIVERY_DELIVERED_STATUS = "delivered"
DELIVERY_FAILED_STATUS = "failed"
DELIVERY_SKIPPED_STATUS = "skipped"
SHORT_PROCESSING_DISABLED_ERROR = "Short processing is disabled by configuration."

logger = logging.getLogger(__name__)


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


@dataclass
class ReconciliationSummary:
    channels_processed: int
    channels_failed: int
    videos_discovered: int
    videos_processed: int
    channel_errors: list[dict[str, Any]]


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
        shorts_processing_enabled: bool = True,
        mobile_push_service: MobilePushService | None = None,
        telegram_service: TelegramDeliveryService | None = None,
        transcript_service: TranscriptService | None = None,
        summarization_service: SummarizationService | None = None,
        pipeline_service: PipelineService | None = None,
        pipeline_drain_pause_seconds: float = 60.0,
        llama_recovery_service: LlamaRecoveryService | None = None,
    ):
        self.auth_service = auth_service
        self.email_service = email_service
        self.daily_quota_budget = max(0, daily_quota_budget)
        self.safety_stop_enabled = safety_stop_enabled
        self.shorts_processing_enabled = shorts_processing_enabled
        self.mobile_push_service = mobile_push_service
        self.telegram_service = telegram_service
        self.transcript_service = transcript_service
        self.summarization_service = summarization_service
        self.pipeline_service = pipeline_service
        self.pipeline_drain_pause_seconds = max(0.0, pipeline_drain_pause_seconds)
        self.llama_recovery_service = llama_recovery_service

    def run_poll(self, session: Session, user: User, oauth_account: OAuthAccount) -> PollRunSummary:
        now = datetime.now(UTC)
        quota_state = self._get_or_create_sync_state(session, user.id, QUOTA_PROCESS)
        polling_state = self._get_or_create_sync_state(session, user.id, POLLING_PROCESS)
        summarization_state = self._get_or_create_sync_state(session, user.id, SUMMARIZATION_PROCESS)
        self._load_summary_circuit_state(self.pipeline_service, summarization_state)
        self._deliver_pending_summary_recovery_alert(summarization_state)
        self._skip_disabled_short_work(session, user)

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

        self._attempt_summary_recovery(session, user, summarization_state)
        self._drain_pending_pipeline_videos(session, user)
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
                self._attempt_detect_and_mark_short(session, youtube, video, quota_context)
                if not self._should_process_video(video):
                    user_channel.last_seen_video_id = latest_upload.video_id
                    new_videos_detected += 1
                    channels_processed += 1
                    continue
                if self.pipeline_service is not None:
                    self.pipeline_service.create_stages_for_video(session, user.id, video.id)
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

        # Persist newly detected videos and stages before their sequential drain.
        # This keeps an interruption resumable without skipping newer uploads.
        session.flush()
        self._drain_pending_pipeline_videos(session, user)
        self._finalize_summary_circuit(session, user, summarization_state, now)

        run_outcome = self._resolve_run_outcome(channels_processed, channels_failed)
        summary = PollRunSummary(
            run_outcome=run_outcome,
            channels_processed=channels_processed,
            channels_failed=channels_failed,
            baselines_established=baselines_established,
            new_videos_detected=new_videos_detected,
            quota_blocked=False,
        )

        self._check_and_send_quota_alert(session, user, quota_context)
        self._write_quota_state(quota_state, quota_context, now)
        self._write_polling_state(polling_state, summary, channel_errors, now)
        session.flush()
        return summary

    def reconcile_missing_uploads(
        self,
        session: Session,
        user: User,
        oauth_account: OAuthAccount,
        *,
        max_pages_per_channel: int,
        process_recovered: bool,
        drain_pause_seconds: float | None = None,
    ) -> ReconciliationSummary:
        """Recover uploads after each durable marker without changing normal polling."""
        now = datetime.now(UTC)
        quota_state = self._get_or_create_sync_state(session, user.id, QUOTA_PROCESS)
        summarization_state = self._get_or_create_sync_state(session, user.id, SUMMARIZATION_PROCESS)
        self._load_summary_circuit_state(self.pipeline_service, summarization_state)
        self._deliver_pending_summary_recovery_alert(summarization_state)
        quota_context = self._build_quota_context(quota_state, now)
        self._skip_disabled_short_work(session, user)
        credentials = self.auth_service.ensure_valid_credentials(session, oauth_account)
        youtube = build("youtube", "v3", credentials=credentials, cache_discovery=False)
        monitored_rows = session.execute(
            select(UserChannel, Channel)
            .join(Channel, UserChannel.channel_id == Channel.id)
            .where(UserChannel.user_id == user.id, UserChannel.is_monitored.is_(True))
            .order_by(UserChannel.id.asc())
        ).all()

        channel_errors: list[dict[str, Any]] = []
        channels_processed = 0
        channels_failed = 0
        videos_discovered = 0

        for user_channel, channel in monitored_rows:
            if user_channel.baseline_established_at is None or not user_channel.last_seen_video_id:
                channel_errors.append(self._reconciliation_error(channel, "missing_baseline", "Channel has no durable baseline."))
                channels_failed += 1
                continue

            try:
                uploads, marker_found = self._fetch_uploads_since_marker(
                    youtube,
                    channel,
                    user_channel.last_seen_video_id,
                    max_pages=max_pages_per_channel,
                    quota_context=quota_context,
                )
                if not marker_found:
                    channel_errors.append(
                        self._reconciliation_error(
                            channel,
                            "marker_not_found",
                            "Stored channel marker was not found within the reconciliation page limit.",
                        )
                    )
                    channels_failed += 1
                    continue

                # The playlist is newest-first. Persist the entire window before
                # attempting content work so an interruption is safely resumable.
                for upload in reversed(uploads):
                    video = self._get_or_create_video(session, channel.id, upload)
                    self._attempt_detect_and_mark_short(session, youtube, video, quota_context)
                    if self.pipeline_service is not None and self._should_process_video(video):
                        self.pipeline_service.create_stages_for_video(session, user.id, video.id)

                if uploads:
                    user_channel.last_seen_video_id = uploads[0].video_id
                    videos_discovered += len(uploads)
                channels_processed += 1
            except Exception as exc:  # noqa: BLE001
                logger.exception("Reconciliation failed for channel %s", channel.youtube_channel_id)
                channel_errors.append(self._reconciliation_error(channel, "reconciliation_failed", str(exc)))
                channels_failed += 1

        session.flush()

        self._check_and_send_quota_alert(session, user, quota_context)
        self._write_quota_state(quota_state, quota_context, now)

        videos_processed = 0
        if process_recovered:
            session.commit()
            try:
                self._attempt_summary_recovery(session, user, summarization_state)
                drain_stats = self._drain_pending_pipeline_videos(
                    session,
                    user,
                    pause_seconds=drain_pause_seconds,
                )
                videos_processed = drain_stats.videos_processed
                self._finalize_summary_circuit(session, user, summarization_state, now)
            except Exception as exc:
                reconciliation_state = self._get_or_create_sync_state(session, user.id, RECONCILIATION_PROCESS)
                reconciliation_state.last_error_at = datetime.now(UTC)
                reconciliation_state.last_error_message = f"Reconciliation drain failed: {exc}"
                session.commit()
                raise
        else:
            self._finalize_summary_circuit(session, user, summarization_state, now)

        reconciliation_state = self._get_or_create_sync_state(session, user.id, RECONCILIATION_PROCESS)
        reconciliation_state.state_metadata = {
            "channels_processed": channels_processed,
            "channels_failed": channels_failed,
            "videos_discovered": videos_discovered,
            "videos_processed": videos_processed,
            "channel_errors": channel_errors,
        }
        if channel_errors:
            reconciliation_state.last_error_at = now
            reconciliation_state.last_error_message = f"{len(channel_errors)} channel(s) need reconciliation review."
        else:
            reconciliation_state.last_success_at = now
            reconciliation_state.last_error_at = None
            reconciliation_state.last_error_message = None
        session.flush()
        return ReconciliationSummary(
            channels_processed=channels_processed,
            channels_failed=channels_failed,
            videos_discovered=videos_discovered,
            videos_processed=videos_processed,
            channel_errors=channel_errors,
        )

    def record_polling_error(self, session: Session, user_id: int, message: str) -> None:
        polling_state = self._get_or_create_sync_state(session, user_id, POLLING_PROCESS)
        polling_state.last_error_at = datetime.now(UTC)
        polling_state.last_error_message = message
        session.flush()

    @staticmethod
    def _load_summary_circuit_state(
        pipeline_service: PipelineService | None,
        summarization_state: SyncState,
    ) -> None:
        if pipeline_service is None:
            return
        metadata = summarization_state.state_metadata or {}
        pipeline_service.summary_paused = bool(metadata.get("paused", False))
        pipeline_service.summary_pause_reason = metadata.get("last_error")
        failed_video_id = metadata.get("failed_video_id")
        pipeline_service.summary_pause_video_id = failed_video_id if isinstance(failed_video_id, int) else None

    def _attempt_summary_recovery(
        self,
        session: Session,
        user: User,
        summarization_state: SyncState,
    ) -> None:
        if self.pipeline_service is None or not self.pipeline_service.summary_paused:
            return
        result = self.pipeline_service.attempt_summary_recovery(session, user)
        if result is False:
            logger.warning("Summary recovery inference failed; keeping circuit open.")
        elif result is True:
            logger.info("Summary recovery inference succeeded; closing circuit.")
        else:
            logger.info("Summary recovery deferred: no transcript-ready summary is pending.")

    def _finalize_summary_circuit(
        self,
        session: Session,
        user: User,
        summarization_state: SyncState,
        now: datetime,
    ) -> None:
        if self.pipeline_service is None:
            return

        previous = summarization_state.state_metadata or {}
        was_paused = bool(previous.get("paused", False))
        is_paused = self.pipeline_service.summary_paused
        metadata = dict(previous)
        metadata["paused"] = is_paused

        if is_paused:
            reason = self.pipeline_service.summary_pause_reason or "Unknown summarization failure."
            metadata["last_error"] = reason
            metadata["last_failure_at"] = now.isoformat()
            if not was_paused:
                metadata["incident_started_at"] = now.isoformat()
                metadata["alert_sent"] = False

            restart_message = "Automatic llama.cpp restart is not configured."
            if self._restart_allowed(metadata, now):
                result = self.llama_recovery_service.restart() if self.llama_recovery_service else None
                if result is not None:
                    metadata["restart_attempted_at"] = now.isoformat()
                    metadata["restart_succeeded"] = result.succeeded
                    restart_message = result.reason
            elif metadata.get("restart_attempted_at"):
                restart_message = "llama.cpp restart is waiting for the configured cooldown."

            if not metadata.get("alert_sent", False):
                if self._send_summary_alert(session, user, reason, restart_message):
                    metadata["alert_sent"] = True

            summarization_state.last_error_at = now
            summarization_state.last_error_message = reason
        else:
            if self.pipeline_service.summary_recovery_succeeded or metadata.get("recovery_pending_alert"):
                if self._send_summary_recovery_alert():
                    metadata["recovery_pending_alert"] = False
            metadata["alert_sent"] = False
            metadata["paused"] = False
            summarization_state.last_success_at = now
            summarization_state.last_error_at = None
            summarization_state.last_error_message = None

        summarization_state.state_metadata = metadata
        session.flush()

    def _restart_allowed(self, metadata: dict[str, Any], now: datetime) -> bool:
        if self.llama_recovery_service is None or not self.llama_recovery_service.enabled:
            return False
        last_attempt = metadata.get("restart_attempted_at")
        if not last_attempt:
            return True
        try:
            elapsed = (now - datetime.fromisoformat(last_attempt)).total_seconds()
        except (TypeError, ValueError):
            return True
        return elapsed >= self.llama_recovery_service.cooldown_seconds

    def _send_summary_alert(
        self,
        session: Session,
        user: User,
        reason: str,
        restart_message: str,
    ) -> bool:
        if self.telegram_service is None or not self.telegram_service.enabled:
            return True

        video_id = self.pipeline_service.summary_pause_video_id if self.pipeline_service else None
        video = session.get(Video, video_id) if video_id is not None else None
        title = video.title if video and video.title else (video.youtube_video_id if video else "video pendiente")
        channel = session.get(Channel, video.channel_id) if video else None
        channel_title = channel.title if channel and channel.title else "Canal desconocido"
        message = (
            "⚠️ YTPipe pausó los resúmenes\n\n"
            f"Video: {title}\n"
            f"Canal: {channel_title}\n"
            "Etapa: resumen\n"
            f"Causa: {reason}\n\n"
            "Las transcripciones continuarán guardándose.\n"
            "Los resúmenes pendientes se recuperarán automáticamente.\n\n"
            f"Reinicio de llama.cpp: {restart_message}"
        )
        try:
            self.telegram_service.send_message(message[:3800])
        except Exception:  # noqa: BLE001
            logger.exception("Could not send summary circuit Telegram alert.")
            return False
        return True

    def _send_summary_recovery_alert(self) -> bool:
        if self.telegram_service is None or not self.telegram_service.enabled:
            return True
        try:
            self.telegram_service.send_message(
                "YTPipe recuperado: llama.cpp volvió a completar una inferencia. "
                "Se reanudó el procesamiento de resúmenes pendientes."
            )
        except Exception:  # noqa: BLE001
            logger.exception("Could not send summary recovery Telegram alert.")
            return False
        return True

    def _deliver_pending_summary_recovery_alert(self, summarization_state: SyncState) -> None:
        metadata = summarization_state.state_metadata or {}
        if not metadata.get("recovery_pending_alert"):
            return
        if self._send_summary_recovery_alert():
            updated = dict(metadata)
            updated["recovery_pending_alert"] = False
            summarization_state.state_metadata = updated

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

    def _fetch_uploads_since_marker(
        self,
        youtube: Any,
        channel: Channel,
        marker: str,
        *,
        max_pages: int,
        quota_context: dict[str, Any],
    ) -> tuple[list[LatestUpload], bool]:
        if not channel.uploads_playlist_id:
            raise PollingChannelError(
                UNUSABLE_UPLOADS_PLAYLIST_ERROR,
                "Channel does not expose a usable uploads playlist.",
            )

        page_token: str | None = None
        uploads: list[LatestUpload] = []
        for _ in range(max(1, max_pages)):
            if quota_context["quota_blocked"]:
                raise PollingChannelError("quota_blocked", "YouTube quota safety stop is active.")
            quota_context["last_run_estimated_units"] += 1
            quota_context["estimated_units_used_today"] += 1
            quota_context["quota_blocked"] = (
                self.safety_stop_enabled
                and quota_context["estimated_units_used_today"] >= self.daily_quota_budget
            )
            quota_context["safety_stop_active"] = quota_context["quota_blocked"]
            response = (
                youtube.playlistItems()
                .list(
                    part="snippet,contentDetails",
                    playlistId=channel.uploads_playlist_id,
                    maxResults=50,
                    pageToken=page_token,
                )
                .execute()
            )
            for item in response.get("items", []):
                snippet = item.get("snippet", {})
                content_details = item.get("contentDetails", {})
                video_id = content_details.get("videoId")
                if video_id == marker:
                    return uploads, True
                if video_id:
                    uploads.append(
                        LatestUpload(
                            video_id=video_id,
                            title=snippet.get("title"),
                            published_at=self._parse_youtube_datetime(snippet.get("publishedAt")),
                        )
                    )
            page_token = response.get("nextPageToken")
            if not page_token:
                break
        return uploads, False

    @staticmethod
    def _reconciliation_error(channel: Channel, code: str, message: str) -> dict[str, Any]:
        return {
            "channel_id": channel.id,
            "youtube_channel_id": channel.youtube_channel_id,
            "title": channel.title,
            "error_code": code,
            "error_message": message,
        }

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

    def _skip_disabled_short_deliveries(self, session: Session, user: User) -> None:
        if self.shorts_processing_enabled:
            return

        pending_rows = session.execute(
            select(NotificationDelivery, Video)
            .join(Video, NotificationDelivery.video_id == Video.id)
            .where(
                NotificationDelivery.user_id == user.id,
                NotificationDelivery.status.in_([
                    DEFAULT_DELIVERY_STATUS,
                    DELIVERY_PENDING_RETRY_STATUS,
                ]),
                Video.is_short.is_(True),
            )
        ).all()

        for delivery, _video in pending_rows:
            delivery.status = DELIVERY_SKIPPED_STATUS
            delivery.last_error = SHORT_PROCESSING_DISABLED_ERROR

    def _skip_disabled_short_push_deliveries(self, session: Session, user: User) -> None:
        if self.shorts_processing_enabled:
            return

        pending_rows = session.execute(
            select(MobilePushDelivery, Video)
            .join(Video, MobilePushDelivery.video_id == Video.id)
            .where(
                MobilePushDelivery.user_id == user.id,
                MobilePushDelivery.status == PUSH_DELIVERY_PENDING,
                Video.is_short.is_(True),
            )
        ).all()

        for delivery, _video in pending_rows:
            delivery.status = PUSH_DELIVERY_SKIPPED
            delivery.last_error = SHORT_PROCESSING_DISABLED_ERROR

    def _skip_disabled_short_work(self, session: Session, user: User) -> None:
        if self.pipeline_service is not None:
            self.pipeline_service.skip_disabled_short_work(session, user)
        self._skip_disabled_short_deliveries(session, user)
        self._skip_disabled_short_push_deliveries(session, user)

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

    def _drain_pending_pipeline_videos(
        self,
        session: Session,
        user: User,
        *,
        pause_seconds: float | None = None,
    ) -> PipelineDrainStats:
        if self.pipeline_service is None:
            return PipelineDrainStats(0, 0, 0, 0, 0, 0)
        try:
            stats = self.pipeline_service.drain_pending_videos(
                session=session,
                user=user,
                pause_seconds=self.pipeline_drain_pause_seconds if pause_seconds is None else pause_seconds,
            )
            return stats
        except Exception:
            logger.exception("Pending pipeline video drain failed.")
            raise

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

    def _should_process_video(self, video: Video) -> bool:
        return self.shorts_processing_enabled or video.is_short is not True

    def _build_quota_context(self, quota_state: SyncState, now: datetime) -> dict[str, Any]:
        existing = quota_state.state_metadata or {}
        day = now.date().isoformat()
        existing_day = existing.get("usage_day")
        estimated_used_today = int(existing.get("estimated_units_used_today", 0)) if existing_day == day else 0
        quota_blocked = self.safety_stop_enabled and estimated_used_today >= self.daily_quota_budget

        raw_alerts = existing.get("quota_alerts_sent")
        if existing_day == day and isinstance(raw_alerts, dict):
            quota_alerts_sent = raw_alerts
        else:
            quota_alerts_sent = {"day": day, "thresholds": []}

        return {
            "usage_day": day,
            "daily_quota_budget": self.daily_quota_budget,
            "estimated_units_used_today": estimated_used_today,
            "last_run_estimated_units": 0,
            "safety_stop_enabled": self.safety_stop_enabled,
            "safety_stop_active": quota_blocked,
            "safety_stop_triggered_at": now.isoformat() if quota_blocked else existing.get("safety_stop_triggered_at"),
            "quota_blocked": quota_blocked,
            "quota_alerts_sent": quota_alerts_sent,
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
            "quota_alerts_sent": quota_context.get("quota_alerts_sent", {"day": quota_context["usage_day"], "thresholds": []}),
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

    def _attempt_detect_and_mark_short(
        self,
        session: Session,
        youtube: Any,
        video: Video,
        quota_context: dict[str, Any],
    ) -> None:
        if video.is_short is not None:
            return

        if video.title and self._title_indicates_short(video.title):
            video.is_short = True
            session.flush()
            return

        if not video.youtube_video_id:
            return

        try:
            duration_seconds = self._fetch_video_duration_seconds(
                youtube, video.youtube_video_id, quota_context
            )
        except Exception:  # noqa: BLE001
            return

        if duration_seconds is None:
            return

        video.is_short = duration_seconds <= SHORTS_MAX_DURATION_SECONDS
        session.flush()

    @staticmethod
    def _title_indicates_short(title: str) -> bool:
        lower_title = title.lower()
        return any(marker in lower_title for marker in SHORTS_TITLE_MARKERS)

    def _fetch_video_duration_seconds(
        self,
        youtube: Any,
        youtube_video_id: str,
        quota_context: dict[str, Any],
    ) -> int | None:
        quota_context["last_run_estimated_units"] += 1
        quota_context["estimated_units_used_today"] += 1

        response = (
            youtube.videos()
            .list(part="contentDetails", id=youtube_video_id)
            .execute()
        )
        items = response.get("items", [])
        if not items:
            return None

        duration = items[0].get("contentDetails", {}).get("duration")
        if not duration:
            return None

        return self._parse_iso8601_duration_seconds(duration)

    @staticmethod
    def _parse_iso8601_duration_seconds(value: str) -> int | None:
        match = re.match(r"^PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?$", value)
        if not match:
            return None
        hours = int(match.group(1) or 0)
        minutes = int(match.group(2) or 0)
        seconds = int(match.group(3) or 0)
        return hours * 3600 + minutes * 60 + seconds

    def _check_and_send_quota_alert(
        self,
        session: Session,
        user: User,
        quota_context: dict[str, Any],
    ) -> None:
        daily_budget = quota_context.get("daily_quota_budget", 0)
        used_today = quota_context.get("estimated_units_used_today", 0)
        if daily_budget <= 0:
            return

        usage_percent = int((used_today / daily_budget) * 100)
        if self.telegram_service is None or not self.telegram_service.enabled:
            return

        now = datetime.now(UTC)
        day = now.date().isoformat()

        quota_alerts_sent = quota_context.get("quota_alerts_sent")
        if not isinstance(quota_alerts_sent, dict) or quota_alerts_sent.get("day") != day:
            quota_alerts_sent = {"day": day, "thresholds": []}

        for threshold in QUOTA_ALERT_THRESHOLDS:
            if usage_percent >= threshold and threshold not in quota_alerts_sent["thresholds"]:
                try:
                    self.telegram_service.send_message(
                        self._build_quota_alert_message(used_today, daily_budget, threshold)
                    )
                    quota_alerts_sent["thresholds"].append(threshold)
                except Exception:  # noqa: BLE001
                    continue

        quota_context["quota_alerts_sent"] = quota_alerts_sent

    @staticmethod
    def _build_quota_alert_message(used_today: int, daily_budget: int, threshold: int) -> str:
        if threshold >= 90:
            emoji = "🔴"
            extra = "El polling se detendra pronto. Revisa canales monitoreados o espera al reset diario."
        elif threshold >= 75:
            emoji = "⚠️"
            extra = "Si llegas al 100%, el polling se detendra hasta manana."
        else:
            emoji = "ℹ️"
            extra = "Vas por la mitad del presupuesto diario."

        return (
            f"{emoji} Quota alert: {threshold}% usada\n\n"
            f"YouTube API quota usage: {used_today}/{daily_budget} units hoy.\n\n"
            f"{extra}"
        )
