"""OAuth persistence: lachesis_oauth_clients and lachesis_refresh_tokens

Revision ID: 003
Revises: 002
Create Date: 2026-05-17

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision: str = "003"
down_revision: str | None = "001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "lachesis_oauth_clients",
        sa.Column("client_id", sa.Text(), primary_key=True, nullable=False),
        sa.Column("data", JSONB(), nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        if_not_exists=True,
    )

    op.create_table(
        "lachesis_refresh_tokens",
        sa.Column("token", sa.Text(), primary_key=True, nullable=False),
        sa.Column("data", JSONB(), nullable=False),
        sa.Column("expires_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        if_not_exists=True,
    )


def downgrade() -> None:
    op.drop_table("lachesis_refresh_tokens")
    op.drop_table("lachesis_oauth_clients")
