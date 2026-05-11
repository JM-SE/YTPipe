from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, false, true
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base_class import Base, TimestampMixin


class MobilePushSetting(TimestampMixin, Base):
    __tablename__ = "mobile_push_settings"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false(), nullable=False)
    default_for_monitored_channels: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        server_default=true(),
        nullable=False,
    )
    first_enabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    user = relationship("User", back_populates="mobile_push_setting")
