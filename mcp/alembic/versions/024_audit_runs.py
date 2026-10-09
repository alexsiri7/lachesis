"""Audit runs: lachesis_audit_runs

One row per audit run: the repository, the audit kind (architecture, security, tests,
requirements), the capability for an audit that covers one, the model that ran it,
when it finished, its outcome (clean, findings, failed), the issues it filed and the
tokens it used. Append-only: the history of every audit is read from it.

Revision ID: 024
Revises: 023
Create Date: 2026-10-09

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "024"
down_revision: str | None = "023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "lachesis_audit_runs",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column("user_id", sa.UUID(as_uuid=False), nullable=False),
        sa.Column("repo_name", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("capability", sa.Text(), nullable=True),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("finished_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("outcome", sa.Text(), nullable=False),
        sa.Column(
            "issues",
            postgresql.ARRAY(sa.Integer()),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
        sa.Column("tokens_in", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("tokens_out", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column(
            "reported_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["lachesis_users.id"],
            name="lachesis_audit_runs_user_id_fkey",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "kind IN ('architecture', 'security', 'tests', 'requirements')",
            name="lachesis_audit_runs_kind",
        ),
        sa.CheckConstraint(
            "outcome IN ('clean', 'findings', 'failed')", name="lachesis_audit_runs_outcome"
        ),
        if_not_exists=True,
    )
    op.create_index(
        "lachesis_audit_runs_lookup",
        "lachesis_audit_runs",
        ["user_id", "repo_name", "kind", "finished_at"],
        if_not_exists=True,
    )


def downgrade() -> None:
    op.drop_table("lachesis_audit_runs", if_exists=True)
