"""Budget questions: allow kind budget on lachesis_questions

A budget question asks the author to raise the spending cap and blocks no issue.

Revision ID: 013
Revises: 012
Create Date: 2026-10-04

"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "013"
down_revision: str | None = "012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("lachesis_questions_kind_check", "lachesis_questions")
    op.create_check_constraint(
        "lachesis_questions_kind_check",
        "lachesis_questions",
        "kind IN ('spec_contradiction', 'large_work', 'maturity', 'improvement', 'budget', "
        "'clarification')",
    )


def downgrade() -> None:
    op.drop_constraint("lachesis_questions_kind_check", "lachesis_questions")
    op.create_check_constraint(
        "lachesis_questions_kind_check",
        "lachesis_questions",
        "kind IN ('spec_contradiction', 'large_work', 'maturity', 'improvement', 'clarification')",
    )
