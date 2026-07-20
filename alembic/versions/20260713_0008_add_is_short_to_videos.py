"""add is_short column to videos

Revision ID: 20260713_0008
Revises: 20260710_0007
Create Date: 2026-07-13 00:00:00

"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260713_0008"
down_revision = "20260710_0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("videos", sa.Column("is_short", sa.Boolean(), nullable=True))


def downgrade() -> None:
    op.drop_column("videos", "is_short")
