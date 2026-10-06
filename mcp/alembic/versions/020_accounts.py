"""Accounts: lachesis_accounts, and fuel per account

The factory may draw on several Claude accounts. Each registered account has a
name, the share of its weekly allowance the factory may use, an optional pause
threshold on its five-hour window, and whether it is the primary account (the
one sprints follow; exactly one per user once any is registered).

Fuel is kept per account: lachesis_fuel gains account_id, unique per account
instead of per user, and the five-hour window's use. An existing report becomes
the report of a primary account named "factory" with a 100 percent share. Usage
reports gain the account a run on a Claude allowance drew on.

Revision ID: 020
Revises: 019
Create Date: 2026-10-06

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "020"
down_revision: str | None = "019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "lachesis_accounts",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column("user_id", sa.UUID(as_uuid=False), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("share_percent", sa.Float(), nullable=False),
        sa.Column("five_hour_pause_percent", sa.Float(), nullable=True),
        sa.Column("is_primary", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "share_percent > 0 AND share_percent <= 100"
            " AND (five_hour_pause_percent IS NULL"
            " OR (five_hour_pause_percent > 0 AND five_hour_pause_percent <= 100))",
            name="lachesis_accounts_percents_check",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["lachesis_users.id"],
            name="lachesis_accounts_user_id_fkey",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("user_id", "name", name="lachesis_accounts_name_key"),
        if_not_exists=True,
    )
    op.create_index(
        "lachesis_accounts_one_primary",
        "lachesis_accounts",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text("is_primary"),
        if_not_exists=True,
    )

    op.add_column("lachesis_fuel", sa.Column("account_id", sa.BigInteger(), nullable=True))
    op.add_column("lachesis_fuel", sa.Column("five_hour_used_percent", sa.Float(), nullable=True))
    op.execute(
        """
        INSERT INTO lachesis_accounts (user_id, name, share_percent, is_primary)
        SELECT user_id, 'factory', 100, true FROM lachesis_fuel
        ON CONFLICT DO NOTHING
        """
    )
    op.execute(
        """
        UPDATE lachesis_fuel f SET account_id = a.id
        FROM lachesis_accounts a
        WHERE a.user_id = f.user_id AND a.is_primary
        """
    )
    op.alter_column("lachesis_fuel", "account_id", nullable=False)
    op.drop_constraint("lachesis_fuel_user_id_key", "lachesis_fuel", type_="unique")
    op.create_unique_constraint("lachesis_fuel_account_id_key", "lachesis_fuel", ["account_id"])
    op.create_foreign_key(
        "lachesis_fuel_account_id_fkey",
        "lachesis_fuel",
        "lachesis_accounts",
        ["account_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_check_constraint(
        "lachesis_fuel_five_hour_check",
        "lachesis_fuel",
        "five_hour_used_percent IS NULL"
        " OR (five_hour_used_percent >= 0 AND five_hour_used_percent <= 100)",
    )

    op.add_column("lachesis_usage_reports", sa.Column("account", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("lachesis_usage_reports", "account")
    op.drop_constraint("lachesis_fuel_five_hour_check", "lachesis_fuel", type_="check")
    op.drop_constraint("lachesis_fuel_account_id_fkey", "lachesis_fuel", type_="foreignkey")
    op.drop_constraint("lachesis_fuel_account_id_key", "lachesis_fuel", type_="unique")
    op.execute(
        """
        DELETE FROM lachesis_fuel f USING lachesis_accounts a
        WHERE a.id = f.account_id AND NOT a.is_primary
        """
    )
    op.create_unique_constraint("lachesis_fuel_user_id_key", "lachesis_fuel", ["user_id"])
    op.drop_column("lachesis_fuel", "five_hour_used_percent")
    op.drop_column("lachesis_fuel", "account_id")
    op.drop_table("lachesis_accounts")
