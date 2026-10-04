"""Run outcomes: lachesis_run_outcomes

A failure reported against one run: rework after review, a retry, or a later
audit finding. Each row names the usage report of the run it counts against;
rows are never changed.

Revision ID: 017
Revises: 016
Create Date: 2026-10-04

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "017"
down_revision: str | None = "016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "lachesis_run_outcomes",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column("run_id", sa.BigInteger(), nullable=False),
        sa.Column("outcome", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "outcome IN ('rework', 'retry', 'audit_finding')",
            name="lachesis_run_outcomes_outcome_check",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["lachesis_usage_reports.id"],
            name="lachesis_run_outcomes_run_id_fkey",
            ondelete="CASCADE",
        ),
        if_not_exists=True,
    )


def downgrade() -> None:
    op.drop_table("lachesis_run_outcomes")
