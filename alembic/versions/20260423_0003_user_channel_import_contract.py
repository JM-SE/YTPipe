"""align user channel import contract

Revision ID: 20260423_0003
Revises: 20260423_0002
Create Date: 2026-04-23 00:00:01

"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260423_0003"
down_revision = "20260423_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("user_channels", "is_monitored", server_default=sa.false(), existing_type=sa.Boolean())

    user_channels = sa.table(
        "user_channels",
        sa.column("is_monitored", sa.Boolean()),
        sa.column("last_seen_video_id", sa.String(length=128)),
        sa.column("baseline_established_at", sa.DateTime(timezone=True)),
    )
    op.execute(
        user_channels.update().values(
            is_monitored=False,
            last_seen_video_id=None,
            baseline_established_at=None,
        )
    )


def downgrade() -> None:
    op.alter_column("user_channels", "is_monitored", server_default=sa.true(), existing_type=sa.Boolean())
