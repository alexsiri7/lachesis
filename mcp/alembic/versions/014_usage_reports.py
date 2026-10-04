"""Run usage reports: lachesis_usage_reports

Every run on an issue reports the model it used, its tokens in and out, the
money it spent where the model is paid per token, and its kind of work. A
report always names an issue; cost is null for runs on the Claude allowance.

Revision ID: 014
Revises: 013
Create Date: 2026-10-04

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "014"
down_revision: str | None = "013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "lachesis_usage_reports",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column("user_id", sa.UUID(as_uuid=False), nullable=False),
        sa.Column("repo_name", sa.Text(), nullable=False),
        sa.Column("issue", sa.Integer(), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("tokens_in", sa.BigInteger(), nullable=False),
        sa.Column("tokens_out", sa.BigInteger(), nullable=False),
        sa.Column("cost_usd", sa.Float(), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "kind IN ('triage', 'summarising', 'audit', 'tests', 'implementation', 'spec work')",
            name="lachesis_usage_reports_kind_check",
        ),
        sa.CheckConstraint(
            "tokens_in >= 0 AND tokens_out >= 0 AND (cost_usd IS NULL OR cost_usd >= 0)",
            name="lachesis_usage_reports_amounts_check",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["lachesis_users.id"],
            name="lachesis_usage_reports_user_id_fkey",
            ondelete="CASCADE",
        ),
        if_not_exists=True,
    )


def downgrade() -> None:
    op.drop_table("lachesis_usage_reports")
