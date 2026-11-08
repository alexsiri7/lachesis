"""Retire parking: drop lachesis_backlog.ready and ready_since

Nothing holds an issue back by hand any more, so every parked issue becomes eligible.
Dropping the flag unparks them all; the count per repository is logged first.

Revision ID: 033
Revises: 032
Create Date: 2026-11-08

"""

from __future__ import annotations

import logging
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "033"
down_revision: str | None = "032"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

logger = logging.getLogger("alembic.runtime.migration")


def upgrade() -> None:
    parked = op.get_bind().execute(
        sa.text(
            "SELECT repo_name, count(*) FROM lachesis_backlog "
            "WHERE NOT ready GROUP BY repo_name ORDER BY repo_name"
        )
    )
    for repo_name, count in parked:
        logger.info("Unparked %s issues in %s", count, repo_name)
    op.drop_column("lachesis_backlog", "ready_since")
    op.drop_column("lachesis_backlog", "ready")


def downgrade() -> None:
    op.add_column(
        "lachesis_backlog",
        sa.Column("ready", sa.Boolean(), nullable=False, server_default=sa.text("true")),
    )
    op.add_column(
        "lachesis_backlog",
        sa.Column("ready_since", sa.TIMESTAMP(timezone=True), nullable=True),
    )
