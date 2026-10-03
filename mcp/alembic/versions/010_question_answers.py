"""Answers to questions: answer, answerer, answered_at and outcome on lachesis_questions

A question is open while answered_at is null. The four columns are set together
when it is answered, and never one without the others.

Revision ID: 010
Revises: 009
Create Date: 2026-10-03

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "010"
down_revision: str | None = "009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("lachesis_questions", sa.Column("answer", sa.Text(), nullable=True))
    op.add_column("lachesis_questions", sa.Column("answerer", sa.Text(), nullable=True))
    op.add_column(
        "lachesis_questions",
        sa.Column("answered_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )
    op.add_column("lachesis_questions", sa.Column("outcome", sa.Text(), nullable=True))
    op.create_check_constraint(
        "lachesis_questions_outcome_check",
        "lachesis_questions",
        "outcome IN ('none', 'standing_rule', 'spec_change', 'new_project')",
    )
    op.create_check_constraint(
        "lachesis_questions_answered_together_check",
        "lachesis_questions",
        "(answered_at IS NULL) = (answer IS NULL) "
        "AND (answered_at IS NULL) = (answerer IS NULL) "
        "AND (answered_at IS NULL) = (outcome IS NULL)",
    )


def downgrade() -> None:
    op.drop_constraint("lachesis_questions_answered_together_check", "lachesis_questions")
    op.drop_constraint("lachesis_questions_outcome_check", "lachesis_questions")
    op.drop_column("lachesis_questions", "outcome")
    op.drop_column("lachesis_questions", "answered_at")
    op.drop_column("lachesis_questions", "answerer")
    op.drop_column("lachesis_questions", "answer")
