from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.dependencies import require_internal_bearer_token
from app.db.session import get_db_session
from app.models.channel import Channel
from app.models.user import User
from app.models.user_channel import UserChannel

router = APIRouter(prefix="/internal/channels", tags=["internal"])


class MonitoringUpdateRequest(BaseModel):
    is_monitored: bool


def _serialize_channel_record(user_channel: UserChannel, channel: Channel) -> dict[str, int | str | bool | None]:
    return {
        "channel_id": channel.id,
        "youtube_channel_id": channel.youtube_channel_id,
        "title": channel.title,
        "is_monitored": user_channel.is_monitored,
        "last_seen_video_id": user_channel.last_seen_video_id,
        "baseline_established_at": _serialize_datetime(user_channel.baseline_established_at),
    }


def _serialize_datetime(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.isoformat()


@router.get("", dependencies=[Depends(require_internal_bearer_token)])
def list_channels(session: Session = Depends(get_db_session)) -> dict[str, list[dict[str, int | str | bool | None]]]:
    user = session.scalar(select(User))
    if user is None:
        return {"channels": []}

    rows = session.execute(
        select(UserChannel, Channel)
        .join(Channel, UserChannel.channel_id == Channel.id)
        .where(UserChannel.user_id == user.id)
        .order_by(Channel.title.asc(), Channel.id.asc())
    ).all()

    return {"channels": [_serialize_channel_record(user_channel, channel) for user_channel, channel in rows]}


@router.patch("/{channel_id}/monitoring", dependencies=[Depends(require_internal_bearer_token)])
def update_channel_monitoring(
    channel_id: int,
    payload: MonitoringUpdateRequest,
    session: Session = Depends(get_db_session),
) -> dict[str, int | str | bool | None]:
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

    return _serialize_channel_record(user_channel, channel)
