"""Backlog ordering and ready gate: lachesis_backlog

Revision ID: 004
Revises: 003
Create Date: 2026-09-07

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "004"
down_revision: str | None = "003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "lachesis_backlog",
        sa.Column(
            "id",
            sa.UUID(as_uuid=False),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("user_id", sa.UUID(as_uuid=False), nullable=False),
        sa.Column("repo_name", sa.Text(), nullable=False),
        sa.Column("issue_number", sa.Integer(), nullable=False),
        # NULL means "no explicit position" — the issue sorts into the tail by
        # issue number, so marking it ready never reorders it.
        sa.Column("position", sa.Integer(), nullable=True),
        sa.Column("ready", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("interrupt", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["lachesis_users.id"],
            name="lachesis_backlog_user_id_fkey",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "user_id",
            "repo_name",
            "issue_number",
            name="lachesis_backlog_user_id_repo_name_issue_number_key",
        ),
        if_not_exists=True,
    )


def downgrade() -> None:
    op.drop_table("lachesis_backlog")
