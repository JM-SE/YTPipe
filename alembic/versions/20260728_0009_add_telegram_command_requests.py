"""add durable Telegram command request queue

Revision ID: 20260728_0009
Revises: 20260713_0008
Create Date: 2026-07-28 00:00:00

"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260728_0009"
down_revision = "20260713_0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "telegram_command_requests",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("telegram_update_id", sa.BigInteger(), nullable=False),
        sa.Column("telegram_chat_id", sa.BigInteger(), nullable=False),
        sa.Column("telegram_user_id", sa.BigInteger(), nullable=False),
        sa.Column("telegram_message_id", sa.BigInteger(), nullable=False),
        sa.Column("command", sa.String(length=50), nullable=False, server_default="summary"),
        sa.Column("submitted_url", sa.String(length=4096), nullable=True),
        sa.Column("youtube_video_id", sa.String(length=11), nullable=True),
        sa.Column("video_id", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=30), nullable=False, server_default="pending"),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("processing_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_token", sa.String(length=64), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("acknowledgment_message_id", sa.BigInteger(), nullable=True),
        sa.Column("reply_status", sa.String(length=30), nullable=False, server_default="pending"),
        sa.Column("reply_attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("reply_max_attempts", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("reply_next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reply_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reply_sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("telegram_reply_message_id", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["video_id"], ["videos.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("telegram_update_id", name="uq_telegram_command_requests_update_id"),
        sa.CheckConstraint(
            "attempt_count >= 0",
            name="ck_telegram_command_requests_attempt_count_nonnegative",
        ),
        sa.CheckConstraint(
            "max_attempts >= 0",
            name="ck_telegram_command_requests_max_attempts_nonnegative",
        ),
        sa.CheckConstraint(
            "reply_attempt_count >= 0",
            name="ck_telegram_command_requests_reply_attempt_count_nonnegative",
        ),
        sa.CheckConstraint(
            "reply_max_attempts >= 0",
            name="ck_telegram_command_requests_reply_max_attempts_nonnegative",
        ),
    )
    op.create_index(
        "ix_telegram_command_requests_processing_queue",
        "telegram_command_requests",
        ["status", "next_attempt_at", "created_at"],
    )
    op.create_index(
        "ix_telegram_command_requests_reply_queue",
        "telegram_command_requests",
        ["reply_status", "reply_next_attempt_at", "created_at"],
    )
    op.create_index("ix_telegram_command_requests_video_id", "telegram_command_requests", ["video_id"])


def downgrade() -> None:
    op.drop_index("ix_telegram_command_requests_video_id", table_name="telegram_command_requests")
    op.drop_index("ix_telegram_command_requests_reply_queue", table_name="telegram_command_requests")
    op.drop_index("ix_telegram_command_requests_processing_queue", table_name="telegram_command_requests")
    op.drop_table("telegram_command_requests")
