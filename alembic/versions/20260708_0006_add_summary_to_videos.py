"""add summary column to videos

Revision ID: 20260708_0006
Revises: 20260708_0005
Create Date: 2026-07-08 00:00:00

"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260708_0006"
down_revision = "20260708_0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("videos", sa.Column("summary", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("videos", "summary")
