from datetime import datetime

from sqlalchemy import JSON, CheckConstraint, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base_class import Base, TimestampMixin


class PipelineStage(TimestampMixin, Base):
    __tablename__ = "pipeline_stages"
    __table_args__ = (
        UniqueConstraint("video_id", "user_id", "stage", name="uq_pipeline_stages_video_user_stage"),
        CheckConstraint(
            "broker_submission_epoch >= 0 AND broker_submission_epoch <= max_attempts",
            name="ck_pipeline_stages_broker_submission_epoch_bounded",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    video_id: Mapped[int] = mapped_column(ForeignKey("videos.id", ondelete="CASCADE"), nullable=False)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    stage: Mapped[str] = mapped_column(String(50), nullable=False)
    status: Mapped[str] = mapped_column(String(50), default="pending", nullable=False)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    # Counts new broker submission generations, not attempts inside a broker
    # task. Epoch zero preserves the pre-Y02d idempotency key.
    broker_submission_epoch: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    # Bounded, sanitized terminal facts for prior broker generations. Payloads
    # and provider bodies are never stored here.
    broker_submission_history: Mapped[list[dict] | None] = mapped_column(JSON, nullable=True)
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    failure_class: Mapped[str | None] = mapped_column(String(50), nullable=True)
    failure_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    quarantined_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reconciliation_status: Mapped[str | None] = mapped_column(String(50), nullable=True)
    next_reconcile_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reconciliation_alerted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reconciliation_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Broker identifiers are opaque protected correlation material. They are
    # intentionally not exposed through the model's API or indexed for lookup.
    broker_task_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    broker_idempotency_key: Mapped[str | None] = mapped_column(String(128), nullable=True)

    video = relationship("Video")
    user = relationship("User")
