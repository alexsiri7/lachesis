"""Fuel: lachesis_fuel

The latest Claude allowance status and overflow spend reported for the account:
the share of the weekly allowance used, when it resets, until when a shorter
limit is in force, and this week's Requesty overflow spending against the cap
the author set there. One row per account, replaced by every report.

Revision ID: 015
Revises: 014
Create Date: 2026-10-04

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "015"
down_revision: str | None = "014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "lachesis_fuel",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column("user_id", sa.UUID(as_uuid=False), nullable=False),
        sa.Column("weekly_used_percent", sa.Float(), nullable=False),
        sa.Column("weekly_resets_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("shorter_limit_until", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("overflow_spent_usd", sa.Float(), nullable=False),
        sa.Column("overflow_cap_usd", sa.Float(), nullable=False),
        sa.Column(
            "reported_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "weekly_used_percent >= 0 AND weekly_used_percent <= 100"
            " AND overflow_spent_usd >= 0 AND overflow_cap_usd >= 0",
            name="lachesis_fuel_amounts_check",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["lachesis_users.id"],
            name="lachesis_fuel_user_id_fkey",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("user_id", name="lachesis_fuel_user_id_key"),
        if_not_exists=True,
    )


def downgrade() -> None:
    op.drop_table("lachesis_fuel")
