"""ntfy configuration and notification dedupe records

Revision ID: 006
Revises: 005
Create Date: 2026-09-08

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "006"
down_revision: str | None = "005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # A sibling table rather than columns on lachesis_users: the auth path upserts
    # that row on every request and returns it with RETURNING *, so the ntfy token
    # would ride along into contexts that have no business holding a secret.
    op.create_table(
        "lachesis_ntfy_config",
        sa.Column(
            "id",
            sa.UUID(as_uuid=False),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("user_id", sa.UUID(as_uuid=False), nullable=False),
        sa.Column("server", sa.Text(), nullable=False, server_default=sa.text("'https://ntfy.sh'")),
        # Empty topic is the inert state — configured rows are never deleted, they
        # are cleared, so an empty string carries "feature off" rather than NULL.
        sa.Column("topic", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("token", sa.Text(), nullable=False, server_default=sa.text("''")),
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
            name="lachesis_ntfy_config_user_id_fkey",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("user_id", name="lachesis_ntfy_config_user_id_key"),
        if_not_exists=True,
    )

    op.create_table(
        "lachesis_notifications",
        sa.Column(
            "id",
            sa.UUID(as_uuid=False),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("user_id", sa.UUID(as_uuid=False), nullable=False),
        sa.Column("repo_name", sa.Text(), nullable=False),
        # Requirement ids are zero-padded strings ('026'), not integers.
        sa.Column("requirement_id", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["lachesis_users.id"],
            name="lachesis_notifications_user_id_fkey",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "user_id",
            "repo_name",
            "requirement_id",
            name="lachesis_notifications_user_id_repo_name_requirement_id_key",
        ),
        if_not_exists=True,
    )


def downgrade() -> None:
    op.drop_table("lachesis_notifications")
    op.drop_table("lachesis_ntfy_config")
