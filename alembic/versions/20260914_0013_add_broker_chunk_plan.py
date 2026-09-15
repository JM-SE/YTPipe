"""add durable broker chunk plan progress

Revision ID: 20260914_0013
Revises: 20260909_0012
Create Date: 2026-09-14 00:00:00.000000

The nullable plan is additive. Its downgrade is intentionally a no-op because
removing resumable progress can cause duplicate broker submissions.
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260914_0013"
down_revision = "20260909_0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("pipeline_stages", sa.Column("broker_chunk_plan", sa.JSON(), nullable=True))


def downgrade() -> None:
    pass
