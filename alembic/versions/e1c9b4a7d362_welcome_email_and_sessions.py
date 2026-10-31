"""The welcome email sent once from User Management, and signing out everywhere.

Revision ID: e1c9b4a7d362
Revises: d4e7a2c9f150
"""

import sqlalchemy as sa
from alembic import op

revision = "e1c9b4a7d362"
down_revision = "d4e7a2c9f150"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("session_version", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column("users", sa.Column("welcome_email_sent_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "welcome_email_sent_at")
    op.drop_column("users", "session_version")
