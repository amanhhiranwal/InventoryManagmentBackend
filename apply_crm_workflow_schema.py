"""Apply the CRM workflow schema + data migration to a create_all-managed DB.

The development database is synchronised with ``Base.metadata.create_all``
(see check_and_seed_db.py) rather than Alembic, so it has no
``alembic_version`` row. This script performs exactly what migration
``b7c3d91e5a20`` does, in an idempotent way, for such databases.

Environments that *are* Alembic-managed should run ``alembic upgrade head``
instead and skip this script.

Usage:
    python apply_crm_workflow_schema.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from sqlalchemy import text  # noqa: E402

import app.models  # noqa: E402,F401  (registers every model)
from app.database.postgres import SessionLocal, engine  # noqa: E402
from app.models.opportunity import Opportunity  # noqa: E402
from app.models.sales_order import SalesOrder  # noqa: E402


BACKFILL_OPPORTUNITIES = """
INSERT INTO sales_opportunity (
    lead_id, title, description, status,
    deal_value, priority,
    contact_name, organization_name, email, mobile_number, website,
    designation, office_address, city, zip_code, country,
    gst_number, pan_number, coi_number,
    requirements, remarks, demo_status, product_items,
    customer_type_id, state_id,
    creator_id, assigned_to_id,
    created_at, updated_at
)
SELECT
    l.id,
    l.title,
    l.description,
    CASE
        WHEN lower(l.stage) = 'quotation' THEN 'PROPOSAL'
        WHEN lower(l.stage) = 'dead' THEN 'LOST'
        ELSE 'QUALIFICATION'
    END,
    0.0,
    'Medium',
    l.contact_name, l.organization_name, l.email, l.mobile_number,
    l.website, l.designation, l.office_address, l.city, l.zip_code,
    l.country, l.gst_number, l.pan_number, l.coi_number,
    l.requirements, l.remarks, l.demo_status, l.quotation_items,
    l.customer_type_id, l.state_id,
    l.creator_id, l.assigned_to_id,
    l.created_at, l.updated_at
FROM sales_lead l
WHERE lower(l.stage) IN ('opportunity', 'quotation')
  AND NOT EXISTS (
      SELECT 1 FROM sales_opportunity o WHERE o.lead_id = l.id
  )
"""

MARK_CONVERTED = """
UPDATE sales_lead
SET status = 'CONVERTED'
WHERE lower(stage) IN ('opportunity', 'quotation')
  AND status <> 'CONVERTED'
"""

NORMALISE_STATUSES = """
UPDATE sales_lead SET status = CASE
    WHEN lower(status) = 'new' THEN 'NEW'
    WHEN lower(status) = 'contacted' THEN 'CONTACTED'
    WHEN lower(status) = 'qualified' THEN 'QUALIFIED'
    WHEN lower(status) = 'converted' THEN 'CONVERTED'
    WHEN lower(status) IN ('dead', 'lost', 'inactive') THEN 'LOST'
    WHEN lower(status) = 'active' THEN 'NEW'
    ELSE upper(status)
END
WHERE status IS NOT NULL
  AND status <> upper(status)
"""

MARK_DEAD_STAGE_LOST = """
UPDATE sales_lead
SET status = 'LOST'
WHERE lower(stage) = 'dead'
  AND status <> 'LOST'
"""


def main():
    print("--- 1. Creating sales_opportunity / sales_order tables ---")
    Opportunity.__table__.create(bind=engine, checkfirst=True)
    SalesOrder.__table__.create(bind=engine, checkfirst=True)

    with engine.connect() as conn:
        for table in ("sales_opportunity", "sales_order"):
            exists = conn.execute(
                text(f"SELECT to_regclass('public.{table}')")
            ).scalar()
            print(f"    {table}: {'ok' if exists else 'MISSING'}")

    db = SessionLocal()
    try:
        print("--- 2. Backfilling opportunities from existing leads ---")
        result = db.execute(text(BACKFILL_OPPORTUNITIES))
        print(f"    inserted {result.rowcount} opportunity row(s)")

        converted = db.execute(text(MARK_CONVERTED))
        print(f"    marked {converted.rowcount} lead(s) CONVERTED")

        print("--- 3. Normalising lead statuses to canonical values ---")
        normalised = db.execute(text(NORMALISE_STATUSES))
        print(f"    rewrote {normalised.rowcount} lead status value(s)")

        dead = db.execute(text(MARK_DEAD_STAGE_LOST))
        print(f"    marked {dead.rowcount} dead-stage lead(s) LOST")

        db.commit()

        print("--- 4. Result ---")
        for row in db.execute(
            text(
                "SELECT status, stage, count(*) FROM sales_lead "
                "GROUP BY status, stage ORDER BY 3 DESC"
            )
        ):
            print(f"    lead status={row[0]!r} stage={row[1]!r} count={row[2]}")

        for row in db.execute(
            text(
                "SELECT status, count(*) FROM sales_opportunity "
                "GROUP BY status"
            )
        ):
            print(f"    opportunity status={row[0]!r} count={row[1]}")

        print(
            "    sales_order rows:",
            db.execute(text("SELECT count(*) FROM sales_order")).scalar(),
        )

    finally:
        db.close()

    print("--- Done. Now run migrate_mongo_orders_to_postgres.py ---")


if __name__ == "__main__":
    main()
