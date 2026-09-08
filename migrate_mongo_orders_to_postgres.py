"""Copy sales orders from the legacy MongoDB collection into Postgres.

Sales orders used to live in the Mongo ``sales_orders`` collection, where the
create schema silently discarded most of the payload. They now live in the
``sales_order`` Postgres table.

This script is idempotent: each copied row records its original Mongo
ObjectId in ``legacy_mongo_id``, so re-running skips documents already moved.
The Mongo collection is left untouched, so the original data remains
available until you choose to drop it.

Usage:
    python migrate_mongo_orders_to_postgres.py
"""

import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.core.workflow_status import normalize_sales_order_status  # noqa: E402
from app.database.mongodb import sync_mongo_db  # noqa: E402
from app.database.postgres import SessionLocal  # noqa: E402
from app.models.sales_order import SalesOrder  # noqa: E402


def _parse_datetime(value):
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _to_uuid(value):
    from uuid import UUID

    if not value:
        return None
    try:
        return UUID(str(value))
    except (ValueError, AttributeError, TypeError):
        return None


def migrate():
    db = SessionLocal()
    collection = sync_mongo_db["sales_orders"]

    total = collection.count_documents({})
    print(f"--- Found {total} document(s) in Mongo 'sales_orders' ---")

    migrated = 0
    skipped = 0

    try:
        for doc in collection.find().sort("_id", 1):
            mongo_id = str(doc["_id"])

            existing = (
                db.query(SalesOrder)
                .filter(SalesOrder.legacy_mongo_id == mongo_id)
                .first()
            )

            if existing is not None:
                print(f"  skip {mongo_id} (already migrated as id={existing.id})")
                skipped += 1
                continue

            created_at = _parse_datetime(doc.get("created_at")) or datetime.utcnow()

            order = SalesOrder(
                legacy_mongo_id=mongo_id,
                order_number=doc.get("sales_order_id") or None,
                opportunity_id=None,
                status=normalize_sales_order_status(doc.get("status")),
                customer_name=doc.get("customer_name") or "Unknown Customer",
                company_name=doc.get("company_name"),
                customer_type=doc.get("customer_type"),
                state=doc.get("state"),
                order_date=_parse_datetime(doc.get("order_date")) or created_at,
                assigned_to=doc.get("assigned_to"),
                sales_executive=doc.get("sales_executive"),
                customer_information=doc.get("customer_information"),
                billing_address=doc.get("billing_address"),
                shipping_address=doc.get("shipping_address"),
                items=doc.get("items") or [],
                total_amount=doc.get("total_amount") or 0.0,
                discount_amount=doc.get("discount_amount") or 0.0,
                gst_amount=doc.get("gst_amount") or 0.0,
                grand_total=doc.get("grand_total") or 0.0,
                aging_0_30=doc.get("aging_0_30") or 0.0,
                aging_31_60=doc.get("aging_31_60") or 0.0,
                aging_61_90=doc.get("aging_61_90") or 0.0,
                aging_91_120=doc.get("aging_91_120") or 0.0,
                aging_121_180=doc.get("aging_121_180") or 0.0,
                aging_above_180=doc.get("aging_above_180") or 0.0,
                remarks=doc.get("remarks"),
                creator_id=_to_uuid(doc.get("creator_id")),
                creator_name=doc.get("creator_name"),
                created_at=created_at,
                updated_at=created_at,
            )

            db.add(order)
            db.flush()

            if not order.order_number:
                order.order_number = f"SO-{order.id:05d}"

            db.commit()

            print(
                f"  migrated {mongo_id} -> sales_order id={order.id} "
                f"({order.order_number}, {order.status})"
            )
            migrated += 1

        print(
            f"--- Done. migrated={migrated} skipped={skipped} "
            f"total_in_postgres={db.query(SalesOrder).count()} ---"
        )
        print("Mongo 'sales_orders' collection was left unchanged.")

    finally:
        db.close()


if __name__ == "__main__":
    migrate()
