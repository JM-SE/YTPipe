"""add mobile push schema settings service

Revision ID: 20260424_0004
Revises: 20260423_0003
Create Date: 2026-04-24 00:00:00

"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260424_0004"
down_revision = "20260423_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "mobile_push_settings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("default_for_monitored_channels", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("first_enabled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id"),
    )

    op.create_table(
        "mobile_push_installations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("installation_id", sa.Uuid(), nullable=False),
        sa.Column("expo_push_token", sa.Text(), nullable=False),
        sa.Column("platform", sa.String(length=20), server_default="unknown", nullable=False),
        sa.Column("app_version", sa.String(length=50), nullable=True),
        sa.Column("build_number", sa.String(length=50), nullable=True),
        sa.Column("device_name", sa.String(length=120), nullable=True),
        sa.Column("enabled", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("registered_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("unregistered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("invalidated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("last_expo_ticket_id", sa.String(length=255), nullable=True),
        sa.Column("last_expo_status", sa.String(length=50), nullable=True),
        sa.Column("last_receipt_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "installation_id", name="uq_mobile_push_installations_user_installation"),
    )
    op.create_index(
        "ix_mobile_push_installations_user_enabled",
        "mobile_push_installations",
        ["user_id", "enabled"],
    )

    op.create_table(
        "mobile_push_channel_preferences",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("channel_id", sa.Integer(), nullable=False),
        sa.Column("push_enabled", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("explicitly_set", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["channel_id"], ["channels.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "channel_id", name="uq_mobile_push_channel_preferences_user_channel"),
    )

    op.create_table(
        "mobile_push_deliveries",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("installation_id", sa.Integer(), nullable=False),
        sa.Column("notification_delivery_id", sa.Integer(), nullable=True),
        sa.Column("video_id", sa.Integer(), nullable=True),
        sa.Column("channel_id", sa.Integer(), nullable=True),
        sa.Column("event_type", sa.String(length=50), nullable=False),
        sa.Column("event_key", sa.String(length=255), nullable=True),
        sa.Column("status", sa.String(length=50), server_default="pending", nullable=False),
        sa.Column("attempt_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("expo_ticket_id", sa.String(length=255), nullable=True),
        sa.Column("expo_status", sa.String(length=50), nullable=True),
        sa.Column("expo_response", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["channel_id"], ["channels.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["installation_id"], ["mobile_push_installations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["notification_delivery_id"], ["notification_deliveries.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["video_id"], ["videos.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_mobile_push_deliveries_notification_installation_present",
        "mobile_push_deliveries",
        ["notification_delivery_id", "installation_id"],
        unique=True,
        postgresql_where=sa.text("notification_delivery_id IS NOT NULL"),
        sqlite_where=sa.text("notification_delivery_id IS NOT NULL"),
    )
    op.create_index(
        "ix_mobile_push_deliveries_test_event_key",
        "mobile_push_deliveries",
        ["user_id", "installation_id", "event_key"],
        unique=True,
        postgresql_where=sa.text("event_type = 'test' AND event_key IS NOT NULL"),
        sqlite_where=sa.text("event_type = 'test' AND event_key IS NOT NULL"),
    )
    op.create_index(
        "ix_mobile_push_deliveries_user_event_created",
        "mobile_push_deliveries",
        ["user_id", "event_type", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_mobile_push_deliveries_user_event_created", table_name="mobile_push_deliveries")
    op.drop_index("ix_mobile_push_deliveries_test_event_key", table_name="mobile_push_deliveries")
    op.drop_index("ix_mobile_push_deliveries_notification_installation_present", table_name="mobile_push_deliveries")
    op.drop_table("mobile_push_deliveries")
    op.drop_table("mobile_push_channel_preferences")
    op.drop_index("ix_mobile_push_installations_user_enabled", table_name="mobile_push_installations")
    op.drop_table("mobile_push_installations")
    op.drop_table("mobile_push_settings")
