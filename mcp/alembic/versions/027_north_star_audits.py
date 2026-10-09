"""North-star audits: allow the north-star kind in lachesis_audit_runs

Revision ID: 027
Revises: 026
Create Date: 2026-10-09

"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "027"
down_revision: str | None = "026"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("lachesis_audit_runs_kind", "lachesis_audit_runs", type_="check")
    op.create_check_constraint(
        "lachesis_audit_runs_kind",
        "lachesis_audit_runs",
        "kind IN ('architecture', 'security', 'clean-code', 'tests', 'requirements', 'north-star')",
    )


def downgrade() -> None:
    op.execute("DELETE FROM lachesis_audit_runs WHERE kind = 'north-star'")
    op.drop_constraint("lachesis_audit_runs_kind", "lachesis_audit_runs", type_="check")
    op.create_check_constraint(
        "lachesis_audit_runs_kind",
        "lachesis_audit_runs",
        "kind IN ('architecture', 'security', 'clean-code', 'tests', 'requirements')",
    )
