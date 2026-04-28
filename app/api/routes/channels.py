from __future__ import annotations

from datetime import datetime
from enum import Enum

from fastapi import APIRouter, Body, Depends, HTTPException, Path, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.dependencies import require_admin_bearer_token
from app.db.session import get_db_session
from app.models.channel import Channel
from app.models.user import User
from app.models.user_channel import UserChannel
from app.models.video import Video

router = APIRouter(prefix="/internal/channels", tags=["channels"])


class MonitoringUpdateRequest(BaseModel):
    model_config = ConfigDict(json_schema_extra={"example": {"is_monitored": True}})

    is_monitored: bool


class ChannelRecordResponse(BaseModel):
    channel_id: int
    youtube_channel_id: str
    title: str
    is_monitored: bool
    last_seen_video_id: str | None
    baseline_established_at: str | None
    latest_detected_video: "LatestDetectedVideoSummary | None"


class LatestDetectedVideoSummary(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "youtube_video_id": "abc123",
                "title": "Nuevo video",
                "published_at": "2026-04-28T18:30:00+00:00",
            }
        }
    )

    youtube_video_id: str
    title: str | None
    published_at: str | None


class ChannelListResponse(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "channels": [
                    {
                        "channel_id": 528,
                        "youtube_channel_id": "UCexample",
                        "title": "Example Channel",
                        "is_monitored": True,
                        "last_seen_video_id": "abc123",
                        "baseline_established_at": "2026-04-28T18:20:00+00:00",
                        "latest_detected_video": {
                            "youtube_video_id": "abc123",
                            "title": "Nuevo video",
                            "published_at": "2026-04-28T18:30:00+00:00",
                        },
                    }
                ],
                "pagination": {"limit": 50, "offset": 0, "total": 1},
            }
        }
    )

    channels: list[ChannelRecordResponse]
    pagination: "ChannelPagination"


class ChannelPagination(BaseModel):
    limit: int
    offset: int
    total: int


class ErrorResponse(BaseModel):
    detail: str


class MonitoringFilter(str, Enum):
    monitored = "monitored"
    unmonitored = "unmonitored"
    all = "all"


def _serialize_channel_record(
    user_channel: UserChannel,
    channel: Channel,
    latest_video: Video | None,
) -> ChannelRecordResponse:
    return ChannelRecordResponse(
        channel_id=channel.id,
        youtube_channel_id=channel.youtube_channel_id,
        title=channel.title,
        is_monitored=user_channel.is_monitored,
        last_seen_video_id=user_channel.last_seen_video_id,
        baseline_established_at=_serialize_datetime(user_channel.baseline_established_at),
        latest_detected_video=_serialize_latest_video(latest_video),
    )


def _serialize_datetime(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.isoformat()


def _serialize_latest_video(video: Video | None) -> LatestDetectedVideoSummary | None:
    if video is None:
        return None
    return LatestDetectedVideoSummary(
        youtube_video_id=video.youtube_video_id,
        title=video.title,
        published_at=_serialize_datetime(video.published_at),
    )


def _apply_monitoring_filter(query, monitoring: MonitoringFilter):
    if monitoring == MonitoringFilter.monitored:
        return query.where(UserChannel.is_monitored.is_(True))
    if monitoring == MonitoringFilter.unmonitored:
        return query.where(UserChannel.is_monitored.is_(False))
    return query


def _apply_search_filter(query, search_query: str | None):
    if not search_query:
        return query

    normalized = search_query.strip().lower()
    if not normalized:
        return query

    return query.where(func.lower(Channel.title).contains(normalized))


@router.get(
    "",
    dependencies=[Depends(require_admin_bearer_token)],
    response_model=ChannelListResponse,
    response_model_exclude_none=True,
    summary="List imported channels",
    description=(
        "Returns imported channels with monitoring and baseline state for manual admin/mobile usage. "
        "Defaults to monitored channels only."
    ),
)
def list_channels(
    monitoring: MonitoringFilter = Query(
        default=MonitoringFilter.monitored,
        description="Filter channels by monitoring state.",
        examples=["monitored", "unmonitored", "all"],
    ),
    query: str | None = Query(
        default=None,
        description="Case-insensitive title filter against stored channel records.",
        examples=["news", "music"],
    ),
    limit: int = Query(default=50, ge=1, le=200, description="Page size (1-200)."),
    offset: int = Query(default=0, ge=0, description="Zero-based row offset."),
    session: Session = Depends(get_db_session),
) -> ChannelListResponse:
    user = session.scalar(select(User))
    if user is None:
        return ChannelListResponse(channels=[], pagination=ChannelPagination(limit=limit, offset=offset, total=0))

    base_query = (
        select(UserChannel, Channel)
        .join(Channel, UserChannel.channel_id == Channel.id)
        .where(UserChannel.user_id == user.id)
    )
    base_query = _apply_monitoring_filter(base_query, monitoring)
    base_query = _apply_search_filter(base_query, query)

    total = session.scalar(select(func.count()).select_from(base_query.subquery())) or 0

    rows = session.execute(
        base_query
        .order_by(Channel.title.asc(), Channel.id.asc())
        .limit(limit)
        .offset(offset)
    ).all()

    latest_video_lookup = _load_latest_detected_video_lookup(session, rows)

    return ChannelListResponse(
        channels=[
            _serialize_channel_record(
                user_channel,
                channel,
                latest_video_lookup.get((channel.id, user_channel.last_seen_video_id)),
            )
            for user_channel, channel in rows
        ],
        pagination=ChannelPagination(limit=limit, offset=offset, total=total),
    )


def _load_latest_detected_video_lookup(
    session: Session,
    rows: list[tuple[UserChannel, Channel]],
) -> dict[tuple[int, str], Video]:
    keys = {
        (channel.id, user_channel.last_seen_video_id)
        for user_channel, channel in rows
        if user_channel.last_seen_video_id
    }
    if not keys:
        return {}

    channel_ids = {channel_id for channel_id, _ in keys}
    youtube_video_ids = {video_id for _, video_id in keys}
    videos = session.scalars(
        select(Video).where(Video.channel_id.in_(channel_ids), Video.youtube_video_id.in_(youtube_video_ids))
    ).all()
    return {(video.channel_id, video.youtube_video_id): video for video in videos}


@router.patch(
    "/{channel_id}/monitoring",
    dependencies=[Depends(require_admin_bearer_token)],
    response_model=ChannelRecordResponse,
    response_model_exclude_none=True,
    responses={404: {"model": ErrorResponse, "description": "Channel not found for current user."}},
    summary="Update monitoring state",
    description="Enables or disables monitoring for an imported channel without changing baseline fields.",
)
def update_channel_monitoring(
    channel_id: int = Path(description="Internal channel identifier."),
    payload: MonitoringUpdateRequest = Body(description="Set the target monitoring state."),
    session: Session = Depends(get_db_session),
) -> ChannelRecordResponse:
    user = session.scalar(select(User))
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Channel not found.")

    row = session.execute(
        select(UserChannel, Channel)
        .join(Channel, UserChannel.channel_id == Channel.id)
        .where(UserChannel.user_id == user.id, UserChannel.channel_id == channel_id)
    ).first()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Channel not found.")

    user_channel, channel = row
    user_channel.is_monitored = payload.is_monitored
    session.commit()
    session.refresh(user_channel)

    return _serialize_channel_record(user_channel, channel, latest_video=None)
