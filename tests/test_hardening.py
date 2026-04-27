import pytest
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError

from app.models.channel import Channel
from app.models.notification_delivery import NotificationDelivery
from app.models.oauth_account import OAuthAccount
from app.models.sync_state import SyncState
from app.models.user import User
from app.models.user_channel import UserChannel
from app.models.video import Video


def test_schema_smoke_core_tables_exist(db_session) -> None:
    table_names = set(inspect(db_session.bind).get_table_names())

    assert {
        User.__tablename__,
        OAuthAccount.__tablename__,
        Channel.__tablename__,
        UserChannel.__tablename__,
        Video.__tablename__,
        NotificationDelivery.__tablename__,
        SyncState.__tablename__,
    }.issubset(table_names)


def test_schema_smoke_video_youtube_video_id_is_unique(db_session) -> None:
    channel = Channel(youtube_channel_id="channel-unique-video", title="Channel")
    db_session.add(channel)
    db_session.flush()
    db_session.add(Video(youtube_video_id="video-unique", channel_id=channel.id, title="First"))
    db_session.commit()

    db_session.add(Video(youtube_video_id="video-unique", channel_id=channel.id, title="Duplicate"))
    with pytest.raises(IntegrityError):
        db_session.flush()
    db_session.rollback()


def test_schema_smoke_notification_delivery_user_video_is_unique(db_session) -> None:
    user = User(email="owner@example.com")
    channel = Channel(youtube_channel_id="channel-delivery-unique", title="Channel")
    db_session.add_all([user, channel])
    db_session.flush()
    video = Video(youtube_video_id="delivery-video", channel_id=channel.id, title="Video")
    db_session.add(video)
    db_session.flush()
    db_session.add(NotificationDelivery(user_id=user.id, video_id=video.id, status="pending"))
    db_session.commit()

    db_session.add(NotificationDelivery(user_id=user.id, video_id=video.id, status="pending"))
    with pytest.raises(IntegrityError):
        db_session.flush()
    db_session.rollback()
