"""add single-user guard

Revision ID: 20260423_0002
Revises: 20260422_0001
Create Date: 2026-04-23 00:00:00

"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260423_0002"
down_revision = "20260422_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("is_owner", sa.Boolean(), server_default=sa.true(), nullable=False),
    )
    op.create_unique_constraint("uq_users_is_owner", "users", ["is_owner"])


def downgrade() -> None:
    op.drop_constraint("uq_users_is_owner", "users", type_="unique")
    op.drop_column("users", "is_owner")
