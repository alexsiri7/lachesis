"""Clean code audits: allow the clean-code kind in lachesis_audit_runs

Revision ID: 025
Revises: 024
Create Date: 2026-10-09

"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "025"
down_revision: str | None = "024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("lachesis_audit_runs_kind", "lachesis_audit_runs", type_="check")
    op.create_check_constraint(
        "lachesis_audit_runs_kind",
        "lachesis_audit_runs",
        "kind IN ('architecture', 'security', 'clean-code', 'tests', 'requirements')",
    )


def downgrade() -> None:
    op.drop_constraint("lachesis_audit_runs_kind", "lachesis_audit_runs", type_="check")
    op.create_check_constraint(
        "lachesis_audit_runs_kind",
        "lachesis_audit_runs",
        "kind IN ('architecture', 'security', 'tests', 'requirements')",
    )
