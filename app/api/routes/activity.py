from __future__ import annotations

from datetime import datetime
from enum import Enum

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.api.dependencies import require_admin_bearer_token
from app.db.session import get_db_session
from app.models.channel import Channel
from app.models.notification_delivery import NotificationDelivery
from app.models.pipeline_stage import PipelineStage
from app.models.user import User
from app.models.video import Video

YOUTUBE_WATCH_URL_PREFIX = "https://www.youtube.com/watch?v="

router = APIRouter(prefix="/internal", tags=["activity"])


class ActivityStatusFilter(str, Enum):
    all = "all"
    pending = "pending"
    delivered = "delivered"
    pending_retry = "pending_retry"
    failed = "failed"


class ErrorResponse(BaseModel):
    detail: str


class ActivityPagination(BaseModel):
    limit: int
    offset: int
    total: int


class ActivityItemResponse(BaseModel):
    activity_id: int
    delivery_id: int
    video_id: int
    youtube_video_id: str
    youtube_url: str
    video_title: str | None
    channel_id: int
    channel_title: str | None
    delivery_status: str
    detected_at: str | None
    published_at: str | None
    last_attempt_at: str | None
    last_error: str | None


class ActivityListResponse(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "items": [
                    {
                        "activity_id": 101,
                        "delivery_id": 101,
                        "video_id": 42,
                        "youtube_video_id": "abc123",
                        "youtube_url": "https://www.youtube.com/watch?v=abc123",
                        "video_title": "Nuevo video",
                        "channel_id": 10,
                        "channel_title": "Canal ejemplo",
                        "delivery_status": "delivered",
                        "detected_at": "2026-04-29T18:05:00+00:00",
                        "published_at": "2026-04-29T18:00:00+00:00",
                        "last_attempt_at": "2026-04-29T18:06:00+00:00",
                        "last_error": None,
                    }
                ],
                "pagination": {
                    "limit": 50,
                    "offset": 0,
                    "total": 1,
                },
            }
        }
    )

    items: list[ActivityItemResponse]
    pagination: ActivityPagination


class PipelineDiagnosticItemResponse(BaseModel):
    video_id: int
    youtube_video_id: str
    youtube_url: str
    video_title: str | None
    channel_title: str | None
    stage: str
    stage_status: str
    attempt_count: int
    max_attempts: int
    last_attempt_at: str | None
    last_error: str | None


class PipelineDiagnosticsResponse(BaseModel):
    items: list[PipelineDiagnosticItemResponse]
    pagination: ActivityPagination


@router.get(
    "/activity",
    dependencies=[Depends(require_admin_bearer_token)],
    response_model=ActivityListResponse,
    response_model_exclude_none=True,
    responses={401: {"model": ErrorResponse}},
    summary="List mobile activity",
    description=(
        "Returns recent monitored-channel notification activity from stored delivery/video/channel data. "
        "Read-only endpoint with status filtering and pagination."
    ),
)
def list_activity(
    status: ActivityStatusFilter = Query(
        default=ActivityStatusFilter.all,
        description="Filter activity by notification delivery status.",
        examples=["all", "delivered", "pending_retry", "failed"],
    ),
    limit: int = Query(default=50, ge=1, le=200, description="Page size (1-200)."),
    offset: int = Query(default=0, ge=0, description="Zero-based row offset."),
    session: Session = Depends(get_db_session),
) -> ActivityListResponse:
    user = session.scalar(select(User))
    if user is None:
        return ActivityListResponse(items=[], pagination=ActivityPagination(limit=limit, offset=offset, total=0))

    base_query = (
        select(NotificationDelivery, Video, Channel)
        .join(Video, NotificationDelivery.video_id == Video.id)
        .join(Channel, Video.channel_id == Channel.id)
        .where(NotificationDelivery.user_id == user.id)
    )
    if status != ActivityStatusFilter.all:
        base_query = base_query.where(NotificationDelivery.status == status.value)

    total = session.scalar(select(func.count()).select_from(base_query.subquery())) or 0

    sort_timestamp = case(
        (NotificationDelivery.last_attempt_at.is_not(None), NotificationDelivery.last_attempt_at),
        else_=NotificationDelivery.created_at,
    )

    rows = session.execute(
        base_query
        .order_by(sort_timestamp.desc(), NotificationDelivery.created_at.desc(), NotificationDelivery.id.desc())
        .limit(limit)
        .offset(offset)
    ).all()

    return ActivityListResponse(
        items=[_serialize_activity_item(delivery, video, channel) for delivery, video, channel in rows],
        pagination=ActivityPagination(limit=limit, offset=offset, total=total),
    )


@router.get(
    "/pipeline-diagnostics",
    dependencies=[Depends(require_admin_bearer_token)],
    response_model=PipelineDiagnosticsResponse,
    response_model_exclude_none=True,
    responses={401: {"model": ErrorResponse}},
    summary="List pipeline stages needing attention",
)
def list_pipeline_diagnostics(
    limit: int = Query(default=100, ge=1, le=200, description="Page size (1-200)."),
    offset: int = Query(default=0, ge=0, description="Zero-based row offset."),
    session: Session = Depends(get_db_session),
) -> PipelineDiagnosticsResponse:
    user = session.scalar(select(User))
    if user is None:
        return PipelineDiagnosticsResponse(items=[], pagination=ActivityPagination(limit=limit, offset=offset, total=0))

    base_query = (
        select(PipelineStage, Video, Channel)
        .join(Video, PipelineStage.video_id == Video.id)
        .join(Channel, Video.channel_id == Channel.id)
        .where(
            PipelineStage.user_id == user.id,
            PipelineStage.status.in_(("pending", "pending_retry", "failed", "skipped")),
        )
    )
    total = session.scalar(select(func.count()).select_from(base_query.subquery())) or 0
    rows = session.execute(
        base_query.order_by(PipelineStage.last_attempt_at.desc().nullslast(), PipelineStage.id.asc())
        .limit(limit)
        .offset(offset)
    ).all()
    return PipelineDiagnosticsResponse(
        items=[
            PipelineDiagnosticItemResponse(
                video_id=video.id,
                youtube_video_id=video.youtube_video_id,
                youtube_url=f"{YOUTUBE_WATCH_URL_PREFIX}{video.youtube_video_id}",
                video_title=video.title,
                channel_title=channel.title,
                stage=stage.stage,
                stage_status=stage.status,
                attempt_count=stage.attempt_count,
                max_attempts=stage.max_attempts,
                last_attempt_at=_serialize_datetime(stage.last_attempt_at),
                last_error=stage.last_error,
            )
            for stage, video, channel in rows
        ],
        pagination=ActivityPagination(limit=limit, offset=offset, total=total),
    )


def _serialize_activity_item(
    delivery: NotificationDelivery,
    video: Video,
    channel: Channel,
) -> ActivityItemResponse:
    return ActivityItemResponse(
        activity_id=delivery.id,
        delivery_id=delivery.id,
        video_id=video.id,
        youtube_video_id=video.youtube_video_id,
        youtube_url=f"{YOUTUBE_WATCH_URL_PREFIX}{video.youtube_video_id}",
        video_title=video.title,
        channel_id=channel.id,
        channel_title=channel.title,
        delivery_status=delivery.status,
        detected_at=_serialize_datetime(delivery.created_at),
        published_at=_serialize_datetime(video.published_at),
        last_attempt_at=_serialize_datetime(delivery.last_attempt_at),
        last_error=delivery.last_error if delivery.status in {"pending_retry", "failed"} else None,
    )


def _serialize_datetime(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.isoformat()
