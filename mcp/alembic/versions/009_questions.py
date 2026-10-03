"""Questions for the author: lachesis_questions

A question is a record of its own rather than only an issue comment, so it
outlives the issues it mentions: nothing here references an issue's state, and
closing one leaves the question open and readable.

Blocked issues are numbers in the question's repository. An issue may appear in
several open questions' ``blocks`` and stays waiting until all are answered.

Revision ID: 009
Revises: 008
Create Date: 2026-10-03

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "009"
down_revision: str | None = "008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "lachesis_questions",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column("user_id", sa.UUID(as_uuid=False), nullable=False),
        sa.Column("repo_name", sa.Text(), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("context", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column(
            "options",
            sa.ARRAY(sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("asker", sa.Text(), nullable=False),
        sa.Column("capability", sa.Text(), nullable=True),
        sa.Column("spec_change", sa.Integer(), nullable=True),
        sa.Column("milestone", sa.Text(), nullable=True),
        sa.Column("issue", sa.Integer(), nullable=True),
        sa.Column(
            "blocks",
            sa.ARRAY(sa.Integer()),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "kind IN ('spec_contradiction', 'large_work', 'maturity', 'improvement', "
            "'clarification')",
            name="lachesis_questions_kind_check",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["lachesis_users.id"],
            name="lachesis_questions_user_id_fkey",
            ondelete="CASCADE",
        ),
        if_not_exists=True,
    )
    op.create_index(
        "lachesis_questions_user_id_repo_name_idx",
        "lachesis_questions",
        ["user_id", "repo_name"],
        if_not_exists=True,
    )


def downgrade() -> None:
    op.drop_table("lachesis_questions")
