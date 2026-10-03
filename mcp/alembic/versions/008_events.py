"""Append-only event log: lachesis_events

Every change to the factory's records leaves a row here, so readers can follow
changes from a cursor instead of polling every record. The serial id is that
cursor: it only grows, so "after id N" is a stable position in the history.

Rows are immutable. The app connects as the role that owns the table, and an
owner's UPDATE and DELETE rights cannot be revoked, so triggers enforce it in
the database itself.

Revision ID: 008
Revises: 007
Create Date: 2026-10-03

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "008"
down_revision: str | None = "007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "lachesis_events",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column("user_id", sa.UUID(as_uuid=False), nullable=False),
        sa.Column("repo_name", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("subject", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        # No ON DELETE CASCADE: a cascade is a delete, and the trigger below would
        # refuse it anyway — deleting a user who has history fails either way.
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["lachesis_users.id"],
            name="lachesis_events_user_id_fkey",
        ),
        if_not_exists=True,
    )
    op.create_index(
        "lachesis_events_user_id_id_idx",
        "lachesis_events",
        ["user_id", "id"],
        if_not_exists=True,
    )

    op.execute(
        """
        CREATE OR REPLACE FUNCTION lachesis_events_immutable() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'lachesis_events is append-only: % is not allowed', TG_OP;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER lachesis_events_no_update_or_delete
        BEFORE UPDATE OR DELETE ON lachesis_events
        FOR EACH ROW EXECUTE FUNCTION lachesis_events_immutable()
        """
    )
    op.execute(
        """
        CREATE TRIGGER lachesis_events_no_truncate
        BEFORE TRUNCATE ON lachesis_events
        FOR EACH STATEMENT EXECUTE FUNCTION lachesis_events_immutable()
        """
    )


def downgrade() -> None:
    op.drop_table("lachesis_events")
    op.execute("DROP FUNCTION IF EXISTS lachesis_events_immutable()")
