"""Per-repo signal that decides when a requirement counts as landed in production

A repo whose deployments GitHub reports can wait for a successful production
deployment before announcing a requirement. A repo whose deployments it does not
report has only the merge to go on, so ``merge`` is the default: it is the claim
that is always true of a merged requirement, and the notification says which
signal produced it, so an unconfigured repo can never announce something as live
when it is only merged.

Revision ID: 007
Revises: 006
Create Date: 2026-09-08

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "007"
down_revision: str | None = "006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "lachesis_repos",
        sa.Column(
            "landing_signal",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'merge'"),
        ),
    )


def downgrade() -> None:
    op.drop_column("lachesis_repos", "landing_signal")
