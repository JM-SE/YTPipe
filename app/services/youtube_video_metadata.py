from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import json
import re
from typing import Any

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.channel import Channel
from app.models.oauth_account import OAuthAccount
from app.models.sync_state import SyncState
from app.models.user import User
from app.models.video import Video
from app.services.auth import GoogleOAuthService


SHORTS_TITLE_MARKERS = ("#shorts", "#short", "#ytshorts", "ytshorts")
SHORTS_MAX_DURATION_SECONDS = 60
QUOTA_PROCESS = "quota"
RETRYABLE_YOUTUBE_REASONS = {
    "backendError",
    "internalError",
    "rateLimitExceeded",
    "serviceUnavailable",
    "userRateLimitExceeded",
}
QUOTA_YOUTUBE_REASONS = {"dailyLimitExceeded", "dailyLimitExceededUnreg", "quotaExceeded"}
CREDENTIAL_YOUTUBE_REASONS = {"authError", "forbidden", "insufficientPermissions", "invalidCredentials"}


class YouTubeMetadataError(RuntimeError):
    def __init__(self, code: str, message: str, *, retryable: bool):
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable


@dataclass(frozen=True)
class YouTubeVideoMetadata:
    video_id: str
    channel_id: str
    channel_title: str
    title: str
    published_at: datetime | None
    duration_seconds: int | None
    is_short: bool | None


class YouTubeVideoMetadataService:
    def __init__(
        self,
        auth_service: GoogleOAuthService,
        *,
        daily_quota_budget: int,
        safety_stop_enabled: bool,
    ):
        self.auth_service = auth_service
        self.daily_quota_budget = max(0, daily_quota_budget)
        self.safety_stop_enabled = safety_stop_enabled

    def resolve_and_upsert(
        self,
        session: Session,
        *,
        user: User,
        oauth_account: OAuthAccount,
        youtube_video_id: str,
    ) -> Video:
        quota_state, quota_context = self._prepare_quota(session, user.id)
        if quota_context["quota_blocked"]:
            raise YouTubeMetadataError("quota_blocked", "YouTube quota safety stop is active.", retryable=True)

        try:
            credentials = self.auth_service.ensure_valid_credentials(session, oauth_account)
        except HTTPException as exc:
            if exc.status_code == status.HTTP_401_UNAUTHORIZED:
                raise YouTubeMetadataError(
                    "google_reauth_required",
                    "Stored Google credentials require re-authentication.",
                    retryable=False,
                ) from exc
            raise
        quota_context["estimated_units_used_today"] += 1
        quota_context["last_run_estimated_units"] = 1
        quota_context["quota_blocked"] = (
            self.safety_stop_enabled
            and quota_context["estimated_units_used_today"] >= self.daily_quota_budget
        )
        self._write_quota(quota_state, quota_context)

        try:
            youtube = build("youtube", "v3", credentials=credentials, cache_discovery=False)
            response = youtube.videos().list(
                part="snippet,contentDetails",
                id=youtube_video_id,
            ).execute()
        except HttpError as exc:
            raise self._classify_http_error(exc) from exc
        except Exception as exc:  # noqa: BLE001
            raise YouTubeMetadataError(
                "metadata_transport_failed",
                "YouTube metadata lookup failed temporarily.",
                retryable=True,
            ) from exc

        metadata = self._parse_metadata(response, youtube_video_id)
        channel = session.scalar(select(Channel).where(Channel.youtube_channel_id == metadata.channel_id))
        if channel is None:
            channel = Channel(
                youtube_channel_id=metadata.channel_id,
                title=metadata.channel_title,
                uploads_playlist_id=None,
            )
            session.add(channel)
            session.flush()
        else:
            if channel.title is None:
                channel.title = metadata.channel_title

        video = session.scalar(select(Video).where(Video.youtube_video_id == metadata.video_id))
        if video is None:
            video = Video(
                youtube_video_id=metadata.video_id,
                channel_id=channel.id,
                title=metadata.title,
                published_at=metadata.published_at,
                is_short=metadata.is_short,
            )
            session.add(video)
        else:
            if video.title is None:
                video.title = metadata.title
            if video.published_at is None:
                video.published_at = metadata.published_at
            if video.is_short is None:
                video.is_short = metadata.is_short
        session.flush()
        return video

    def _prepare_quota(self, session: Session, user_id: int) -> tuple[SyncState, dict[str, Any]]:
        now = datetime.now(UTC)
        state = session.scalar(
            select(SyncState).with_for_update().where(
                SyncState.user_id == user_id,
                SyncState.process_type == QUOTA_PROCESS,
            )
        )
        if state is None:
            state = SyncState(user_id=user_id, process_type=QUOTA_PROCESS)
            try:
                with session.begin_nested():
                    session.add(state)
                    session.flush()
            except IntegrityError:
                state = session.scalar(
                    select(SyncState)
                    .with_for_update()
                    .where(
                        SyncState.user_id == user_id,
                        SyncState.process_type == QUOTA_PROCESS,
                    )
                )
                if state is None:
                    raise

        existing = state.state_metadata or {}
        usage_day = now.date().isoformat()
        used = existing.get("estimated_units_used_today", 0)
        if existing.get("usage_day") != usage_day or not isinstance(used, int):
            used = 0
        context = {
            "usage_day": usage_day,
            "daily_quota_budget": self.daily_quota_budget,
            "estimated_units_used_today": used,
            "last_run_estimated_units": 0,
            "quota_blocked": self.safety_stop_enabled and used >= self.daily_quota_budget,
        }
        return state, context

    def _write_quota(self, state: SyncState, context: dict[str, Any]) -> None:
        state.state_metadata = {
            **(state.state_metadata or {}),
            "usage_day": context["usage_day"],
            "daily_quota_budget": context["daily_quota_budget"],
            "estimated_units_used_today": context["estimated_units_used_today"],
            "last_run_estimated_units": context["last_run_estimated_units"],
            "quota_blocked": context["quota_blocked"],
            "safety_stop_active": context["quota_blocked"],
            "safety_stop_enabled": self.safety_stop_enabled,
        }

    @classmethod
    def _parse_metadata(cls, payload: Any, requested_video_id: str) -> YouTubeVideoMetadata:
        if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
            raise YouTubeMetadataError("malformed_metadata", "YouTube returned an invalid metadata payload.", retryable=True)
        items = payload["items"]
        if not items:
            raise YouTubeMetadataError("video_not_found", "The YouTube video is not accessible.", retryable=False)

        item = items[0]
        snippet = item.get("snippet") if isinstance(item, dict) else None
        details = item.get("contentDetails") if isinstance(item, dict) else None
        if not isinstance(snippet, dict) or not isinstance(details, dict):
            raise YouTubeMetadataError("malformed_metadata", "YouTube returned incomplete video metadata.", retryable=True)
        channel_id = snippet.get("channelId")
        channel_title = snippet.get("channelTitle")
        title = snippet.get("title")
        if not all(isinstance(value, str) and value for value in (channel_id, channel_title, title)):
            raise YouTubeMetadataError("malformed_metadata", "YouTube returned incomplete video metadata.", retryable=True)

        duration_value = details.get("duration")
        if not isinstance(duration_value, str):
            raise YouTubeMetadataError("malformed_metadata", "YouTube returned incomplete video metadata.", retryable=True)
        duration_seconds = parse_iso8601_duration_seconds(duration_value)
        if duration_seconds is None:
            raise YouTubeMetadataError("malformed_metadata", "YouTube returned an invalid video duration.", retryable=True)
        published_at = parse_youtube_datetime(snippet.get("publishedAt"))
        return YouTubeVideoMetadata(
            video_id=requested_video_id,
            channel_id=channel_id,
            channel_title=channel_title,
            title=title,
            published_at=published_at,
            duration_seconds=duration_seconds,
            is_short=classify_short(title, duration_seconds),
        )

    @staticmethod
    def _classify_http_error(exc: HttpError) -> YouTubeMetadataError:
        status_code = getattr(exc.resp, "status", None)
        reason = _youtube_error_reason(exc)
        if status_code == 401 or reason in CREDENTIAL_YOUTUBE_REASONS:
            return YouTubeMetadataError(
                "google_reauth_required",
                "Stored Google credentials require re-authentication.",
                retryable=False,
            )
        if reason in QUOTA_YOUTUBE_REASONS:
            return YouTubeMetadataError("quota_exceeded", "YouTube quota is exhausted.", retryable=True)
        if status_code == 429 or status_code is not None and status_code >= 500 or reason in RETRYABLE_YOUTUBE_REASONS:
            return YouTubeMetadataError(
                "metadata_retryable_failure",
                "YouTube metadata lookup failed temporarily.",
                retryable=True,
            )
        if status_code in {400, 403, 404}:
            return YouTubeMetadataError("video_not_accessible", "The YouTube video is not accessible.", retryable=False)
        return YouTubeMetadataError("metadata_provider_rejected", "YouTube rejected the metadata request.", retryable=False)


def classify_short(title: str | None, duration_seconds: int | None) -> bool | None:
    if title and title_indicates_short(title):
        return True
    if duration_seconds is None:
        return None
    return duration_seconds <= SHORTS_MAX_DURATION_SECONDS


def title_indicates_short(title: str) -> bool:
    lower_title = title.lower()
    return any(marker in lower_title for marker in SHORTS_TITLE_MARKERS)


def parse_iso8601_duration_seconds(value: str) -> int | None:
    match = re.match(r"^PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?$", value)
    if not match or not any(match.groups()):
        return None
    hours = int(match.group(1) or 0)
    minutes = int(match.group(2) or 0)
    seconds = int(match.group(3) or 0)
    return hours * 3600 + minutes * 60 + seconds


def parse_youtube_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise YouTubeMetadataError("malformed_metadata", "YouTube returned an invalid publication timestamp.", retryable=True) from exc
    return parsed.astimezone(UTC)


def _youtube_error_reason(exc: HttpError) -> str | None:
    try:
        content = exc.content.decode("utf-8") if isinstance(exc.content, bytes) else exc.content
        payload = json.loads(content)
    except (AttributeError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    errors = payload.get("error", {}).get("errors", [])
    if not isinstance(errors, list) or not errors:
        return None
    reason = errors[0].get("reason")
    return reason if isinstance(reason, str) else None
