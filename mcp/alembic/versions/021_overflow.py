"""Overflow: lachesis_overflow, apart from account fuel

Requesty overflow is a budget of its own: the money spent through Requesty this
sprint against the cap the author set there. It is kept in its own row per user,
changed only by an overflow report, and no longer carried by account fuel reports.
The primary account's current overflow figures become the first overflow report.

Revision ID: 021
Revises: 020
Create Date: 2026-10-06

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "021"
down_revision: str | None = "020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "lachesis_overflow",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column("user_id", sa.UUID(as_uuid=False), nullable=False),
        sa.Column("spent_usd", sa.Float(), nullable=False),
        sa.Column("cap_usd", sa.Float(), nullable=False),
        sa.Column(
            "reported_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "spent_usd >= 0 AND cap_usd >= 0", name="lachesis_overflow_amounts_check"
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["lachesis_users.id"],
            name="lachesis_overflow_user_id_fkey",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("user_id", name="lachesis_overflow_user_id_key"),
        if_not_exists=True,
    )
    op.execute(
        """
        INSERT INTO lachesis_overflow (user_id, spent_usd, cap_usd, reported_at)
        SELECT f.user_id, f.overflow_spent_usd, f.overflow_cap_usd, f.reported_at
        FROM lachesis_fuel f JOIN lachesis_accounts a ON a.id = f.account_id
        WHERE a.is_primary
        ON CONFLICT DO NOTHING
        """
    )
    op.drop_constraint("lachesis_fuel_amounts_check", "lachesis_fuel", type_="check")
    op.drop_column("lachesis_fuel", "overflow_spent_usd")
    op.drop_column("lachesis_fuel", "overflow_cap_usd")
    op.create_check_constraint(
        "lachesis_fuel_amounts_check",
        "lachesis_fuel",
        "weekly_used_percent >= 0 AND weekly_used_percent <= 100",
    )


def downgrade() -> None:
    op.drop_constraint("lachesis_fuel_amounts_check", "lachesis_fuel", type_="check")
    op.add_column(
        "lachesis_fuel",
        sa.Column("overflow_spent_usd", sa.Float(), nullable=False, server_default="0"),
    )
    op.add_column(
        "lachesis_fuel",
        sa.Column("overflow_cap_usd", sa.Float(), nullable=False, server_default="0"),
    )
    op.execute(
        """
        UPDATE lachesis_fuel f SET overflow_spent_usd = o.spent_usd, overflow_cap_usd = o.cap_usd
        FROM lachesis_overflow o WHERE o.user_id = f.user_id
        """
    )
    op.create_check_constraint(
        "lachesis_fuel_amounts_check",
        "lachesis_fuel",
        "weekly_used_percent >= 0 AND weekly_used_percent <= 100"
        " AND overflow_spent_usd >= 0 AND overflow_cap_usd >= 0",
    )
    op.drop_table("lachesis_overflow")
