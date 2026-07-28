from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base_class import Base, TimestampMixin


class TelegramCommandRequest(TimestampMixin, Base):
    __tablename__ = "telegram_command_requests"
    __table_args__ = (
        UniqueConstraint("telegram_update_id", name="uq_telegram_command_requests_update_id"),
        Index(
            "ix_telegram_command_requests_processing_queue",
            "status",
            "next_attempt_at",
            "created_at",
        ),
        Index(
            "ix_telegram_command_requests_reply_queue",
            "reply_status",
            "reply_next_attempt_at",
            "created_at",
        ),
        Index("ix_telegram_command_requests_video_id", "video_id"),
        CheckConstraint("attempt_count >= 0", name="ck_telegram_command_requests_attempt_count_nonnegative"),
        CheckConstraint("max_attempts >= 0", name="ck_telegram_command_requests_max_attempts_nonnegative"),
        CheckConstraint(
            "reply_attempt_count >= 0",
            name="ck_telegram_command_requests_reply_attempt_count_nonnegative",
        ),
        CheckConstraint(
            "reply_max_attempts >= 0",
            name="ck_telegram_command_requests_reply_max_attempts_nonnegative",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    telegram_update_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    telegram_chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    telegram_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    telegram_message_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    command: Mapped[str] = mapped_column(String(50), nullable=False, default="summary")
    submitted_url: Mapped[str | None] = mapped_column(String(4096), nullable=True)
    youtube_video_id: Mapped[str | None] = mapped_column(String(11), nullable=True)
    video_id: Mapped[int | None] = mapped_column(
        ForeignKey("videos.id", ondelete="SET NULL"),
        nullable=True,
    )
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="pending")
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    processing_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    lease_token: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    acknowledgment_message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    reply_status: Mapped[str] = mapped_column(String(30), nullable=False, default="pending")
    reply_attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    reply_max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    reply_next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reply_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reply_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    telegram_reply_message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    video = relationship("Video")
