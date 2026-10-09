"""Issue sizes: lachesis_issue_sizes

The size of each issue in Fibonacci points (1, 2, 3, 5, 8 or 13), or 21 for an issue
judged larger than 13, which is never planned until it is split. One row per issue,
replaced when the issue is resized.

Revision ID: 023
Revises: 022
Create Date: 2026-10-09

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "023"
down_revision: str | None = "022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "lachesis_issue_sizes",
        sa.Column("user_id", sa.UUID(as_uuid=False), nullable=False),
        sa.Column("repo_name", sa.Text(), nullable=False),
        sa.Column("issue", sa.Integer(), nullable=False),
        sa.Column("size", sa.Integer(), nullable=False),
        sa.Column(
            "sized_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["lachesis_users.id"],
            name="lachesis_issue_sizes_user_id_fkey",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("user_id", "repo_name", "issue", name="lachesis_issue_sizes_pkey"),
        sa.CheckConstraint("size IN (1, 2, 3, 5, 8, 13, 21)", name="lachesis_issue_sizes_size"),
        if_not_exists=True,
    )


def downgrade() -> None:
    op.drop_table("lachesis_issue_sizes", if_exists=True)
