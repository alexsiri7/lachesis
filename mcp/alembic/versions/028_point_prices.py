"""Point prices: an estimate rests on the price of a point of its kind, or of all kinds

An estimate is now an issue's size times the price of a point, measured from past
issues of its kind of work, falling back to the price across all kinds. An estimate
from past issues in the same capability rested on matching history, as one from its
kind does now.

Revision ID: 028
Revises: 027
Create Date: 2026-10-09

"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "028"
down_revision: str | None = "027"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("lachesis_estimates_basis_check", "lachesis_estimates", type_="check")
    op.execute("UPDATE lachesis_estimates SET basis = 'kind' WHERE basis = 'capability'")
    op.create_check_constraint(
        "lachesis_estimates_basis_check",
        "lachesis_estimates",
        "basis IN ('kind', 'all kinds', 'none')",
    )


def downgrade() -> None:
    op.drop_constraint("lachesis_estimates_basis_check", "lachesis_estimates", type_="check")
    op.execute("UPDATE lachesis_estimates SET basis = 'kind' WHERE basis = 'all kinds'")
    op.create_check_constraint(
        "lachesis_estimates_basis_check",
        "lachesis_estimates",
        "basis IN ('capability', 'kind', 'none')",
    )
