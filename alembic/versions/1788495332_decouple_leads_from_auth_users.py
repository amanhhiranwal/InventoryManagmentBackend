"""Decouple CRM leads from local auth users.

Revision ID: decouple_leads_from_auth_users
Revises: 5b10f5c63db3
"""

from alembic import op
from sqlalchemy import text

revision = "decouple_leads_from_auth_users"
down_revision = "5b10f5c63db3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()

    constraints = [
        "sales_lead_creator_id_fkey",
        "sales_lead_assigned_to_id_fkey",
        "sales_lead_assigned_by_id_fkey",
    ]

    for constraint in constraints:
        exists = connection.execute(
    text(
        """
        SELECT EXISTS (
            SELECT 1
            FROM pg_constraint
            WHERE conname = :constraint
              AND conrelid = 'sales_lead'::regclass
        )
        """
    ),
    {"constraint": constraint},
).scalar()

        if exists:
            op.drop_constraint(
                constraint,
                "sales_lead",
                type_="foreignkey",
            )


def downgrade() -> None:
    op.create_foreign_key(
        "sales_lead_creator_id_fkey",
        "sales_lead",
        "users",
        ["creator_id"],
        ["id"],
    )
    op.create_foreign_key(
        "sales_lead_assigned_to_id_fkey",
        "sales_lead",
        "users",
        ["assigned_to_id"],
        ["id"],
    )
    op.create_foreign_key(
        "sales_lead_assigned_by_id_fkey",
        "sales_lead",
        "users",
        ["assigned_by_id"],
        ["id"],
    )
