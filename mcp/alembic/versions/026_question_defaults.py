"""Question defaults: default_answer and default_after on lachesis_questions

A clarification or improvement question may carry a default answer, taken once
``default_after`` passes with no answer; it is then recorded as the answer with the
outcome ``defaulted``.

Revision ID: 026
Revises: 025
Create Date: 2026-10-09

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "026"
down_revision: str | None = "025"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("lachesis_questions", sa.Column("default_answer", sa.Text(), nullable=True))
    op.add_column(
        "lachesis_questions",
        sa.Column("default_after", sa.TIMESTAMP(timezone=True), nullable=True),
    )
    op.create_check_constraint(
        "lachesis_questions_default_check",
        "lachesis_questions",
        "(default_answer IS NULL) = (default_after IS NULL) "
        "AND (default_answer IS NULL OR kind IN ('clarification', 'improvement'))",
    )
    op.drop_constraint("lachesis_questions_outcome_check", "lachesis_questions", type_="check")
    op.create_check_constraint(
        "lachesis_questions_outcome_check",
        "lachesis_questions",
        "outcome IN ('none', 'standing_rule', 'spec_change', 'new_project', 'defaulted')",
    )
    op.create_index(
        "lachesis_questions_default_after_idx",
        "lachesis_questions",
        ["default_after"],
        postgresql_where=sa.text("answered_at IS NULL AND default_after IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("lachesis_questions_default_after_idx", table_name="lachesis_questions")
    op.execute("UPDATE lachesis_questions SET outcome = 'none' WHERE outcome = 'defaulted'")
    op.drop_constraint("lachesis_questions_outcome_check", "lachesis_questions", type_="check")
    op.create_check_constraint(
        "lachesis_questions_outcome_check",
        "lachesis_questions",
        "outcome IN ('none', 'standing_rule', 'spec_change', 'new_project')",
    )
    op.drop_constraint("lachesis_questions_default_check", "lachesis_questions", type_="check")
    op.drop_column("lachesis_questions", "default_after")
    op.drop_column("lachesis_questions", "default_answer")
