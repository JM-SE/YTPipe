"""add broker submission retry generation epoch

Revision ID: 20260909_0012
Revises: 20260907_0011
Create Date: 2026-09-09 00:00:00.000000

The downgrade is intentionally a no-op. Removing the discriminator would make
it unsafe to determine whether a retry belongs to the old broker task.
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260909_0012"
down_revision = "20260907_0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        # SQLite cannot ALTER TABLE to add a standalone constraint. Alembic's
        # batch recreate preserves the existing rows while adding the same
        # column, history store, and bound constraint.
        with op.batch_alter_table("pipeline_stages", recreate="always") as batch:
            batch.add_column(
                sa.Column("broker_submission_epoch", sa.Integer(), nullable=False, server_default="0")
            )
            batch.add_column(sa.Column("broker_submission_history", sa.JSON(), nullable=True))
            batch.create_check_constraint(
                "ck_pipeline_stages_broker_submission_epoch_bounded",
                "broker_submission_epoch >= 0 AND broker_submission_epoch <= max_attempts",
            )
        return

    op.add_column(
        "pipeline_stages",
        sa.Column("broker_submission_epoch", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column("pipeline_stages", sa.Column("broker_submission_history", sa.JSON(), nullable=True))
    op.create_check_constraint(
        "ck_pipeline_stages_broker_submission_epoch_bounded",
        "pipeline_stages",
        "broker_submission_epoch >= 0 AND broker_submission_epoch <= max_attempts",
    )


def downgrade() -> None:
    # Deliberately retain the discriminator and its evidence. A destructive
    # rollback requires an explicit compatibility/data-retention decision.
    pass
