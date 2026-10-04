"""Sprint reports: lachesis_sprint_reports

The report on each sprint that ended, stored when it is first made. One row per
account and sprint end: inserting it is the claim that the sprint is reported,
and so that its push notification and finding notes are sent, only once.

Revision ID: 019
Revises: 018
Create Date: 2026-10-04

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision: str = "019"
down_revision: str | None = "018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "lachesis_sprint_reports",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column("user_id", sa.UUID(as_uuid=False), nullable=False),
        sa.Column("sprint_start", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("sprint_end", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("report", JSONB(), nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["lachesis_users.id"],
            name="lachesis_sprint_reports_user_id_fkey",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("user_id", "sprint_end", name="lachesis_sprint_reports_sprint_key"),
        if_not_exists=True,
    )


def downgrade() -> None:
    op.drop_table("lachesis_sprint_reports")
