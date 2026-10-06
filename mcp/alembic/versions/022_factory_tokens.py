"""Factory tokens: lachesis_factory_tokens

Named, revocable keys an unattended script presents as its bearer token to act
as the author. Only the SHA-256 of a key is stored; a revoked key keeps its row,
with revoked_at set, so its name and history stay listed.

Revision ID: 022
Revises: 021
Create Date: 2026-10-06

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "022"
down_revision: str | None = "021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "lachesis_factory_tokens",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column("user_id", sa.UUID(as_uuid=False), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("token_hash", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("last_used_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["lachesis_users.id"],
            name="lachesis_factory_tokens_user_id_fkey",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("token_hash", name="lachesis_factory_tokens_hash_key"),
        if_not_exists=True,
    )
    op.create_index(
        "lachesis_factory_tokens_active_name",
        "lachesis_factory_tokens",
        ["user_id", "name"],
        unique=True,
        postgresql_where=sa.text("revoked_at IS NULL"),
        if_not_exists=True,
    )


def downgrade() -> None:
    op.drop_table("lachesis_factory_tokens")
