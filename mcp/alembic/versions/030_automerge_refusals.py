"""Automerge refusals: lachesis_automerge_refusals

Why automerge could not be enabled on each Lachesis pull request it was refused for, so
that a later read can name the reason that pull request will not merge by itself.

Revision ID: 030
Revises: 029
Create Date: 2026-10-16

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "030"
down_revision: str | None = "029"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "lachesis_automerge_refusals",
        sa.Column("user_id", sa.UUID(as_uuid=False), nullable=False),
        sa.Column("repo_name", sa.Text(), nullable=False),
        sa.Column("pr_number", sa.Integer(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["lachesis_users.id"],
            name="lachesis_automerge_refusals_user_id_fkey",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "user_id", "repo_name", "pr_number", name="lachesis_automerge_refusals_pkey"
        ),
        if_not_exists=True,
    )


def downgrade() -> None:
    op.drop_table("lachesis_automerge_refusals", if_exists=True)
