"""Milestones: lachesis_milestones and lachesis_milestone_spec_changes

A milestone groups spec changes in a repository. Its state — planned, active,
verifying, done or dropped — is read from the work on every read, so there is
no state column: only the author's acts of starting and dropping are stored.

Membership is a join table rather than an array so a plain unique constraint
can enforce that a spec change belongs to at most one milestone per repository.
Its user_id and repo_name are copied from the milestone row on insert.

The milestone's id is what lachesis_notes and lachesis_questions name as
``milestone``.

Revision ID: 012
Revises: 011
Create Date: 2026-10-03

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "012"
down_revision: str | None = "011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "lachesis_milestones",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column("user_id", sa.UUID(as_uuid=False), nullable=False),
        sa.Column("repo_name", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("goal", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("started_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("dropped_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["lachesis_users.id"],
            name="lachesis_milestones_user_id_fkey",
        ),
        if_not_exists=True,
    )
    op.create_index(
        "lachesis_milestones_user_id_repo_name_idx",
        "lachesis_milestones",
        ["user_id", "repo_name"],
        if_not_exists=True,
    )

    op.create_table(
        "lachesis_milestone_spec_changes",
        sa.Column("milestone_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.UUID(as_uuid=False), nullable=False),
        sa.Column("repo_name", sa.Text(), nullable=False),
        sa.Column("spec_change", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("milestone_id", "spec_change"),
        sa.ForeignKeyConstraint(
            ["milestone_id"],
            ["lachesis_milestones.id"],
            name="lachesis_milestone_spec_changes_milestone_id_fkey",
        ),
        sa.UniqueConstraint(
            "user_id",
            "repo_name",
            "spec_change",
            name="lachesis_milestone_spec_changes_one_milestone",
        ),
        if_not_exists=True,
    )


def downgrade() -> None:
    op.drop_table("lachesis_milestone_spec_changes")
    op.drop_table("lachesis_milestones")
