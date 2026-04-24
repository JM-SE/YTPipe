from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, UniqueConstraint, false
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base_class import Base, TimestampMixin


class UserChannel(TimestampMixin, Base):
    __tablename__ = "user_channels"
    __table_args__ = (UniqueConstraint("user_id", "channel_id", name="uq_user_channels_user_channel"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    channel_id: Mapped[int] = mapped_column(ForeignKey("channels.id", ondelete="CASCADE"), nullable=False)
    is_monitored: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        server_default=false(),
        nullable=False,
    )
    last_seen_video_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    baseline_established_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    user = relationship("User", back_populates="user_channels")
    channel = relationship("Channel", back_populates="user_channels")
