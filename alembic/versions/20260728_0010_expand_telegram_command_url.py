"""expand stored Telegram command URL bound

Revision ID: 20260728_0010
Revises: 20260728_0009
Create Date: 2026-07-28 00:00:00

"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260728_0010"
down_revision = "20260728_0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "telegram_command_requests",
        "submitted_url",
        existing_type=sa.String(length=2048),
        type_=sa.String(length=4096),
    )


def downgrade() -> None:
    op.alter_column(
        "telegram_command_requests",
        "submitted_url",
        existing_type=sa.String(length=4096),
        type_=sa.String(length=2048),
    )
