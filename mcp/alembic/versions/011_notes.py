"""Work notes: lachesis_notes

Agents leave notes on the work rather than messaging each other, so a note is
attached to exactly one issue or milestone in a repository. Milestones have no
table yet, so a milestone is named by its identifier, as in lachesis_questions.

Notes are append-only: a correction is a new note. As with lachesis_events, the
app connects as the table's owner, so triggers enforce it in the database.

Revision ID: 011
Revises: 010
Create Date: 2026-10-03

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "011"
down_revision: str | None = "010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "lachesis_notes",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column("user_id", sa.UUID(as_uuid=False), nullable=False),
        sa.Column("repo_name", sa.Text(), nullable=False),
        sa.Column("issue", sa.Integer(), nullable=True),
        sa.Column("milestone", sa.Text(), nullable=True),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("type", sa.Text(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "type IN ('handoff', 'finding', 'progress', 'decision', 'report')",
            name="lachesis_notes_type_check",
        ),
        sa.CheckConstraint(
            "(issue IS NULL) <> (milestone IS NULL)",
            name="lachesis_notes_one_subject_check",
        ),
        # No ON DELETE CASCADE: a cascade is a delete, which the trigger below refuses.
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["lachesis_users.id"],
            name="lachesis_notes_user_id_fkey",
        ),
        if_not_exists=True,
    )
    op.create_index(
        "lachesis_notes_user_id_repo_name_idx",
        "lachesis_notes",
        ["user_id", "repo_name"],
        if_not_exists=True,
    )

    op.execute(
        """
        CREATE OR REPLACE FUNCTION lachesis_notes_immutable() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'lachesis_notes is append-only: % is not allowed', TG_OP;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER lachesis_notes_no_update_or_delete
        BEFORE UPDATE OR DELETE ON lachesis_notes
        FOR EACH ROW EXECUTE FUNCTION lachesis_notes_immutable()
        """
    )
    op.execute(
        """
        CREATE TRIGGER lachesis_notes_no_truncate
        BEFORE TRUNCATE ON lachesis_notes
        FOR EACH STATEMENT EXECUTE FUNCTION lachesis_notes_immutable()
        """
    )


def downgrade() -> None:
    op.drop_table("lachesis_notes")
    op.execute("DROP FUNCTION IF EXISTS lachesis_notes_immutable()")
