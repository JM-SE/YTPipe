from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base_class import Base, TimestampMixin


class Channel(TimestampMixin, Base):
    __tablename__ = "channels"

    id: Mapped[int] = mapped_column(primary_key=True)
    youtube_channel_id: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    uploads_playlist_id: Mapped[str | None] = mapped_column(String(128), nullable=True)

    user_channels = relationship("UserChannel", back_populates="channel")
    videos = relationship("Video", back_populates="channel")
