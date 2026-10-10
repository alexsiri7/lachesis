"""Experiments: lachesis_experiments

Each improvement the factory tries on how it works: the change, the measure it should
move, its value when the experiment started, the target, and the issues that hold its
work. A sprint review judges an open experiment: kept, reverted, or given one more sprint
(extended, at most once), each judgement appended to its results.

Revision ID: 029
Revises: 028
Create Date: 2026-10-10

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision: str = "029"
down_revision: str | None = "028"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "lachesis_experiments",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column("user_id", sa.UUID(as_uuid=False), nullable=False),
        sa.Column("repo_name", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("change", sa.Text(), nullable=False),
        sa.Column("measure", sa.Text(), nullable=False),
        sa.Column("baseline", sa.Double(), nullable=False),
        sa.Column("target", sa.Double(), nullable=False),
        sa.Column("issues", sa.ARRAY(sa.Integer()), nullable=False),
        sa.Column("state", sa.Text(), nullable=False, server_default="open"),
        sa.Column("results", JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["lachesis_users.id"],
            name="lachesis_experiments_user_id_fkey",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "state IN ('open', 'kept', 'reverted')", name="lachesis_experiments_state"
        ),
        if_not_exists=True,
    )


def downgrade() -> None:
    op.drop_table("lachesis_experiments", if_exists=True)
