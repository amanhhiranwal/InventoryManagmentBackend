"""Decouple locations from local companies.

Revision ID: decouple_locations_companies
Revises: 5b10f5c63db3
"""

from alembic import op
from sqlalchemy import text


revision = "decouple_locations_companies"
down_revision = "5b10f5c63db3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()

    exists = connection.execute(
        text(
            """
            SELECT EXISTS (
                SELECT 1
                FROM pg_constraint
                WHERE conname = :constraint
                  AND conrelid = 'locations'::regclass
            )
            """
        ),
        {"constraint": "locations_company_id_fkey"},
    ).scalar()

    if exists:
        op.drop_constraint(
            "locations_company_id_fkey",
            "locations",
            type_="foreignkey",
        )


def downgrade() -> None:
    op.create_foreign_key(
        "locations_company_id_fkey",
        "locations",
        "companies",
        ["company_id"],
        ["id"],
        ondelete="CASCADE",
    )
