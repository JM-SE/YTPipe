from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Index, Integer, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base_class import Base, TimestampMixin


class MobilePushDelivery(TimestampMixin, Base):
    __tablename__ = "mobile_push_deliveries"
    __table_args__ = (
        Index(
            "ix_mobile_push_deliveries_notification_installation_present",
            "notification_delivery_id",
            "installation_id",
            unique=True,
            sqlite_where=text("notification_delivery_id IS NOT NULL"),
            postgresql_where=text("notification_delivery_id IS NOT NULL"),
        ),
        Index(
            "ix_mobile_push_deliveries_test_event_key",
            "user_id",
            "installation_id",
            "event_key",
            unique=True,
            sqlite_where=text("event_type = 'test' AND event_key IS NOT NULL"),
            postgresql_where=text("event_type = 'test' AND event_key IS NOT NULL"),
        ),
        Index("ix_mobile_push_deliveries_user_event_created", "user_id", "event_type", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    installation_id: Mapped[int] = mapped_column(
        ForeignKey("mobile_push_installations.id", ondelete="CASCADE"),
        nullable=False,
    )
    notification_delivery_id: Mapped[int | None] = mapped_column(
        ForeignKey("notification_deliveries.id", ondelete="SET NULL"),
        nullable=True,
    )
    video_id: Mapped[int | None] = mapped_column(ForeignKey("videos.id", ondelete="SET NULL"), nullable=True)
    channel_id: Mapped[int | None] = mapped_column(ForeignKey("channels.id", ondelete="SET NULL"), nullable=True)
    event_type: Mapped[str] = mapped_column(String(50), nullable=False)
    event_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(50), default="pending", server_default="pending", nullable=False)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    expo_ticket_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    expo_status: Mapped[str | None] = mapped_column(String(50), nullable=True)
    expo_response: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    user = relationship("User", back_populates="mobile_push_deliveries")
    installation = relationship("MobilePushInstallation", back_populates="push_deliveries")
    notification_delivery = relationship("NotificationDelivery", back_populates="mobile_push_deliveries")
    video = relationship("Video", back_populates="mobile_push_deliveries")
    channel = relationship("Channel", back_populates="mobile_push_deliveries")
