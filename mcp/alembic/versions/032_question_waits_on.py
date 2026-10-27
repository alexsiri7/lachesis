"""The issues in other repositories each question waits on

A question that waits on issues in other repositories settles itself, releasing the
issues it blocks, once every one of them is closed. Each entry is {"repo", "issue"}.

Revision ID: 032
Revises: 031
Create Date: 2026-10-27

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision: str = "032"
down_revision: str | None = "031"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "lachesis_questions",
        sa.Column(
            "waits_on",
            JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )


def downgrade() -> None:
    op.drop_column("lachesis_questions", "waits_on")
