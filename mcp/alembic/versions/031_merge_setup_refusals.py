"""Per-repo record of the settings that could not be configured for unattended merges

Each registered repository is configured so its pull requests merge without a human;
whatever GitHub refused is kept, setting by setting with the reason, so the dashboard
can report the repository as a Lachesis fault until a later attempt succeeds.

Revision ID: 031
Revises: 030
Create Date: 2026-10-27

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision: str = "031"
down_revision: str | None = "030"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "lachesis_repos",
        sa.Column(
            "merge_setup_refusals",
            JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )


def downgrade() -> None:
    op.drop_column("lachesis_repos", "merge_setup_refusals")
