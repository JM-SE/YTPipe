from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from itertools import islice
from typing import Any, TypedDict, cast

from googleapiclient.discovery import build
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.channel import Channel
from app.models.oauth_account import OAuthAccount
from app.models.sync_state import SyncState
from app.models.user import User
from app.models.user_channel import UserChannel
from app.services.auth import GoogleOAuthService

SUBSCRIPTION_SYNC_PROCESS = "subscription_sync"


@dataclass
class SubscriptionSyncResult:
    imported_channels: int
    created_channels: int
    updated_channels: int
    created_user_channels: int
    updated_user_channels: int


class SubscriptionRecord(TypedDict):
    channel_id: str
    title: str | None


class YouTubeSubscriptionService:
    def __init__(self, auth_service: GoogleOAuthService | Any):
        self.auth_service = auth_service

    def sync_subscriptions(self, session: Session, user: User, oauth_account: OAuthAccount) -> SubscriptionSyncResult:
        sync_state = self._get_or_create_sync_state(session, user.id)
        credentials = self.auth_service.ensure_valid_credentials(session, oauth_account)
        youtube = build("youtube", "v3", credentials=credentials, cache_discovery=False)
        subscriptions = self._fetch_subscriptions(youtube)
        channel_ids = [cast(str, sub["channel_id"]) for sub in subscriptions]
        channels_by_id = self._fetch_channels_by_id(youtube, channel_ids)
        result = self._upsert_subscription_state(session, user.id, subscriptions, channels_by_id)
        sync_state.last_success_at = datetime.now(timezone.utc)
        sync_state.last_error_at = None
        sync_state.last_error_message = None
        sync_state.state_metadata = {
            "imported_channels": result.imported_channels,
            "created_channels": result.created_channels,
            "updated_channels": result.updated_channels,
            "created_user_channels": result.created_user_channels,
            "updated_user_channels": result.updated_user_channels,
        }
        session.flush()
        return result

    def record_sync_error(self, session: Session, user_id: int, message: str) -> None:
        sync_state = self._get_or_create_sync_state(session, user_id)
        sync_state.last_error_at = datetime.now(timezone.utc)
        sync_state.last_error_message = message
        session.flush()

    def _fetch_subscriptions(self, youtube: Any) -> list[SubscriptionRecord]:
        subscriptions: list[SubscriptionRecord] = []
        page_token: str | None = None

        while True:
            response = (
                youtube.subscriptions()
                .list(part="snippet", mine=True, maxResults=50, pageToken=page_token)
                .execute()
            )

            for item in response.get("items", []):
                snippet = item.get("snippet", {})
                resource = snippet.get("resourceId", {})
                channel_id = resource.get("channelId")
                if channel_id:
                    subscriptions.append(
                        {
                            "channel_id": channel_id,
                            "title": snippet.get("title"),
                        }
                    )

            page_token = response.get("nextPageToken")
            if not page_token:
                return subscriptions

    def _fetch_channels_by_id(self, youtube: Any, channel_ids: Iterable[str]) -> dict[str, dict[str, str | None]]:
        result: dict[str, dict[str, str | None]] = {}
        for chunk in self._chunked(list(dict.fromkeys(channel_ids)), size=50):
            if not chunk:
                continue
            response = (
                youtube.channels()
                .list(part="snippet,contentDetails", id=",".join(chunk), maxResults=50)
                .execute()
            )
            for item in response.get("items", []):
                channel_id = item.get("id")
                if not channel_id:
                    continue
                result[channel_id] = {
                    "title": item.get("snippet", {}).get("title"),
                    "uploads_playlist_id": item.get("contentDetails", {})
                    .get("relatedPlaylists", {})
                    .get("uploads"),
                }
        return result

    def _upsert_subscription_state(
        self,
        session: Session,
        user_id: int,
        subscriptions: list[SubscriptionRecord],
        channels_by_id: dict[str, dict[str, str | None]],
    ) -> SubscriptionSyncResult:
        created_channels = 0
        updated_channels = 0
        created_user_channels = 0
        updated_user_channels = 0

        for subscription in subscriptions:
            channel_id = cast(str, subscription["channel_id"])
            details = channels_by_id.get(channel_id, {})
            channel = session.scalar(select(Channel).where(Channel.youtube_channel_id == channel_id))
            if channel is None:
                channel = Channel(youtube_channel_id=channel_id)
                session.add(channel)
                created_channels += 1
            else:
                updated_channels += 1

            channel.title = details.get("title") or subscription.get("title")
            channel.uploads_playlist_id = details.get("uploads_playlist_id")
            session.flush()

            user_channel = session.scalar(
                select(UserChannel).where(
                    UserChannel.user_id == user_id,
                    UserChannel.channel_id == channel.id,
                )
            )
            if user_channel is None:
                user_channel = UserChannel(user_id=user_id, channel_id=channel.id, is_monitored=False)
                session.add(user_channel)
                created_user_channels += 1
            else:
                updated_user_channels += 1

        session.flush()

        return SubscriptionSyncResult(
            imported_channels=len(subscriptions),
            created_channels=created_channels,
            updated_channels=updated_channels,
            created_user_channels=created_user_channels,
            updated_user_channels=updated_user_channels,
        )

    def establish_baselines(self, session: Session, youtube: Any, user_channels: Iterable[UserChannel]) -> int:
        established = 0
        now = datetime.now(timezone.utc)
        for user_channel in user_channels:
            if user_channel.baseline_established_at:
                continue
            latest_video_id = self._fetch_latest_visible_video_id(youtube, user_channel.channel.uploads_playlist_id)
            user_channel.last_seen_video_id = latest_video_id
            user_channel.baseline_established_at = now
            established += 1
        session.flush()
        return established

    def _fetch_latest_visible_video_id(self, youtube: Any, uploads_playlist_id: str | None) -> str | None:
        if not uploads_playlist_id:
            return None
        response = (
            youtube.playlistItems()
            .list(part="contentDetails", playlistId=uploads_playlist_id, maxResults=1)
            .execute()
        )
        items = response.get("items", [])
        if not items:
            return None
        return items[0].get("contentDetails", {}).get("videoId")

    def _get_or_create_sync_state(self, session: Session, user_id: int) -> SyncState:
        sync_state = session.scalar(
            select(SyncState).where(
                SyncState.user_id == user_id,
                SyncState.process_type == SUBSCRIPTION_SYNC_PROCESS,
            )
        )
        if sync_state is None:
            sync_state = SyncState(user_id=user_id, process_type=SUBSCRIPTION_SYNC_PROCESS)
            session.add(sync_state)
            session.flush()
        return sync_state

    @staticmethod
    def _chunked(values: list[str], size: int) -> Iterable[list[str]]:
        iterator = iter(values)
        while chunk := list(islice(iterator, size)):
            yield chunk
