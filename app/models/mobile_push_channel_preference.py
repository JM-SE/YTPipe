from sqlalchemy import Boolean, ForeignKey, UniqueConstraint, true
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base_class import Base, TimestampMixin


class MobilePushChannelPreference(TimestampMixin, Base):
    __tablename__ = "mobile_push_channel_preferences"
    __table_args__ = (UniqueConstraint("user_id", "channel_id", name="uq_mobile_push_channel_preferences_user_channel"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    channel_id: Mapped[int] = mapped_column(ForeignKey("channels.id", ondelete="CASCADE"), nullable=False)
    push_enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default=true(), nullable=False)
    explicitly_set: Mapped[bool] = mapped_column(Boolean, default=True, server_default=true(), nullable=False)

    user = relationship("User", back_populates="mobile_push_channel_preferences")
    channel = relationship("Channel", back_populates="mobile_push_channel_preferences")
