"""add reports_to on users

The manager a user reports to. The role hierarchy says which roles sit
under which; this says which person each user sits under, so two managers
on the same level each see only their own team.

Revision ID: d8b3f6a2c914
Revises: c5d2e8f41a07
Create Date: 2026-09-19 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

# revision identifiers, used by Alembic.
revision: str = "d8b3f6a2c914"
down_revision: Union[str, Sequence[str], None] = "c5d2e8f41a07"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "reports_to_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index("ix_users_reports_to_id", "users", ["reports_to_id"])


def downgrade() -> None:
    op.drop_index("ix_users_reports_to_id", table_name="users")
    op.drop_column("users", "reports_to_id")
