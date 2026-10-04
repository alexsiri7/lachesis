"""Token estimates: lachesis_estimates

The current estimate of each issue in tokens, made from the measured usage of
past issues of the same kind and capability, with its confidence, its basis
and how many past issues it rests on. One row per issue, replaced by every
estimate; tokens is null when there is no history to estimate from.

Revision ID: 016
Revises: 015
Create Date: 2026-10-04

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "016"
down_revision: str | None = "015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "lachesis_estimates",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column("user_id", sa.UUID(as_uuid=False), nullable=False),
        sa.Column("repo_name", sa.Text(), nullable=False),
        sa.Column("issue", sa.Integer(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("capability", sa.Text(), nullable=True),
        sa.Column("tokens", sa.BigInteger(), nullable=True),
        sa.Column("confidence", sa.Text(), nullable=False),
        sa.Column("basis", sa.Text(), nullable=False),
        sa.Column("samples", sa.Integer(), nullable=False),
        sa.Column(
            "estimated_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "kind IN ('triage', 'summarising', 'audit', 'tests', 'implementation', 'spec work')",
            name="lachesis_estimates_kind_check",
        ),
        sa.CheckConstraint(
            "confidence IN ('low', 'normal')",
            name="lachesis_estimates_confidence_check",
        ),
        sa.CheckConstraint(
            "basis IN ('capability', 'kind', 'none')",
            name="lachesis_estimates_basis_check",
        ),
        sa.CheckConstraint(
            "samples >= 0 AND (tokens IS NULL OR tokens >= 0)",
            name="lachesis_estimates_amounts_check",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["lachesis_users.id"],
            name="lachesis_estimates_user_id_fkey",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("user_id", "repo_name", "issue", name="lachesis_estimates_issue_key"),
        if_not_exists=True,
    )


def downgrade() -> None:
    op.drop_table("lachesis_estimates")
