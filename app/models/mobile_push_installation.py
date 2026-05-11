from datetime import datetime
from uuid import UUID

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, Text, UniqueConstraint, func, true
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import Uuid

from app.db.base_class import Base, TimestampMixin


class MobilePushInstallation(TimestampMixin, Base):
    __tablename__ = "mobile_push_installations"
    __table_args__ = (
        UniqueConstraint("user_id", "installation_id", name="uq_mobile_push_installations_user_installation"),
        Index("ix_mobile_push_installations_user_enabled", "user_id", "enabled"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    installation_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    expo_push_token: Mapped[str] = mapped_column(Text, nullable=False)
    platform: Mapped[str] = mapped_column(String(20), default="unknown", server_default="unknown", nullable=False)
    app_version: Mapped[str | None] = mapped_column(String(50), nullable=True)
    build_number: Mapped[str | None] = mapped_column(String(50), nullable=True)
    device_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default=true(), nullable=False)
    registered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    unregistered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    invalidated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_expo_ticket_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    last_expo_status: Mapped[str | None] = mapped_column(String(50), nullable=True)
    last_receipt_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    user = relationship("User", back_populates="mobile_push_installations")
    push_deliveries = relationship("MobilePushDelivery", back_populates="installation", passive_deletes=True)
