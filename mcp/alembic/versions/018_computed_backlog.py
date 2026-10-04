"""Computed backlog order: lachesis_backlog.ready_since, lachesis_focus

The backlog order is computed, so the hand-set position goes. ready_since records
when an issue was last unparked, so ageing counts from then rather than from its
creation. lachesis_focus holds the repositories and milestones the author has
focused on; a repository-level focus has no milestone.

Revision ID: 018
Revises: 017
Create Date: 2026-10-04

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "018"
down_revision: str | None = "017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "lachesis_backlog",
        sa.Column("ready_since", sa.TIMESTAMP(timezone=True), nullable=True),
    )
    op.drop_column("lachesis_backlog", "position")
    op.create_table(
        "lachesis_focus",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column("user_id", sa.UUID(as_uuid=False), nullable=False),
        sa.Column("repo_name", sa.Text(), nullable=False),
        sa.Column("milestone_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["lachesis_users.id"],
            name="lachesis_focus_user_id_fkey",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["milestone_id"],
            ["lachesis_milestones.id"],
            name="lachesis_focus_milestone_id_fkey",
            ondelete="CASCADE",
        ),
        if_not_exists=True,
    )
    # A plain unique constraint would treat every NULL milestone as distinct, letting a
    # repository be focused twice.
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS lachesis_focus_target_key "
        "ON lachesis_focus (user_id, repo_name, COALESCE(milestone_id, 0))"
    )


def downgrade() -> None:
    op.drop_table("lachesis_focus")
    op.add_column("lachesis_backlog", sa.Column("position", sa.Integer(), nullable=True))
    op.drop_column("lachesis_backlog", "ready_since")
