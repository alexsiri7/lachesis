"""Ready by default: parking becomes the explicit case

An issue is now eligible for autonomous pickup unless a row explicitly parks it,
so the column default flips and every existing row is readied. The update is
unconditional because a stored ``false`` predates parking semantics — under the
old polarity it only ever meant "never touched by set_backlog_flags", never a
deliberate park. This migration has no GitHub access, so it cannot scope itself
to the issues that are currently open; readying a row whose issue is closed is
harmless, since closed issues drop out of the backlog before the flag is read.

Revision ID: 005
Revises: 004
Create Date: 2026-09-07

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "005"
down_revision: str | None = "004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column("lachesis_backlog", "ready", server_default=sa.text("true"))
    op.execute("UPDATE lachesis_backlog SET ready = true, updated_at = now() WHERE ready = false")


def downgrade() -> None:
    # Only the default is restored: the pre-migration flags carried no author
    # intent to preserve, and under the old code a readied row simply reads as
    # ready until it is parked again.
    op.alter_column("lachesis_backlog", "ready", server_default=sa.text("false"))
