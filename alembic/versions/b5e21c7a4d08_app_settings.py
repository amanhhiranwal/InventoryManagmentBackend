"""Company profile and other settings, edited from the UI.

Revision ID: b5e21c7a4d08
Revises: a7f42c1d90b3
"""

import sqlalchemy as sa
from alembic import op

revision = "b5e21c7a4d08"
down_revision = "a7f42c1d90b3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "app_setting",
        sa.Column("key", sa.String(length=100), nullable=False),
        sa.Column("value", sa.Text(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("now()"), nullable=True),
        sa.PrimaryKeyConstraint("key"),
    )


def downgrade() -> None:
    op.drop_table("app_setting")
