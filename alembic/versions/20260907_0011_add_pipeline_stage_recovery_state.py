"""add durable recovery state to pipeline stages

Revision ID: 20260907_0011
Revises: 20260728_0010
Create Date: 2026-09-07 00:00:00.000000

The downgrade is intentionally a no-op. Removing these columns would delete
retry, quarantine, and broker-correlation evidence without an explicit data
retention decision.
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260907_0011"
down_revision = "20260728_0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "pipeline_stages",
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "pipeline_stages",
        sa.Column("failure_class", sa.String(length=50), nullable=True),
    )
    op.add_column(
        "pipeline_stages",
        sa.Column("failure_code", sa.String(length=100), nullable=True),
    )
    op.add_column(
        "pipeline_stages",
        sa.Column("quarantined_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "pipeline_stages",
        sa.Column("broker_task_id", sa.String(length=128), nullable=True),
    )
    op.add_column(
        "pipeline_stages",
        sa.Column("broker_idempotency_key", sa.String(length=128), nullable=True),
    )
    op.add_column(
        "pipeline_stages",
        sa.Column("reconciliation_status", sa.String(length=50), nullable=True),
    )
    op.add_column(
        "pipeline_stages",
        sa.Column("next_reconcile_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "pipeline_stages",
        sa.Column("reconciliation_alerted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "pipeline_stages",
        sa.Column("reconciliation_reason", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    # Deliberately retain all added columns and their data. A destructive
    # rollback requires an explicit retention/data-migration decision.
    pass
