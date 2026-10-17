"""Runnable self-check for inventory product grouping and multi-serial support.

Verifies that:
1. Creating an item with multiple serial numbers stores granular records.
2. Grouped catalogue query aggregates multiple serial numbers under a single product row.
3. Total in-stock count reflects the sum of all units.
4. Serial numbers list contains all unique serial numbers.
5. Ungrouped query can still fetch raw individual records when requested.
6. Delete with all_units removes all serial units of that product.
"""

import uuid
from app.database.postgres import SessionLocal
from app.database.mongodb import sync_mongo_db
from app.services.inventory_service import InventoryService


def run_check():
    db = SessionLocal()
    tag = f"TEST-GRP-{uuid.uuid4().hex[:6].upper()}"
    current_user = {"is_super_admin": True, "user_id": 1}

    try:
        # Clean any old test records
        sync_mongo_db["inventory_items"].delete_many({"name": f"Test Product {tag}"})

        # 1. Create product with 3 serial numbers
        res = InventoryService.create_item(
            name=f"Test Product {tag}",
            serial_number=f"{tag}-001 / {tag}-002 / {tag}-003",
            product_type_code="COMPUTE",
            category="Hardware Solutions",
            attributes={
                "model_number": f"MDL-{tag}",
                "specification": "Test Spec 16GB",
                "rate": 1000.0,
                "unit": "Nos",
                "instock": 3.0,
                "case_size": 1.0,
            },
            current_user=current_user,
            db=db,
        )
        assert res["name"] == f"Test Product {tag}"

        # 2. Query grouped
        items = InventoryService.get_items(search=tag, current_user=current_user, db=db, grouped=True)
        assert len(items) == 1, f"Expected 1 grouped product, got {len(items)}"
        item = items[0]
        assert item["serials_count"] == 3, f"Expected 3 serials, got {item.get('serials_count')}"
        assert item["serial_numbers"] == [f"{tag}-001", f"{tag}-002", f"{tag}-003"]
        assert item["attributes"]["instock"] == 3.0, f"Expected stock 3.0, got {item['attributes']['instock']}"

        # 3. Query ungrouped
        raw_items = InventoryService.get_items(search=tag, current_user=current_user, db=db, grouped=False)
        assert len(raw_items) == 3, f"Expected 3 raw items, got {len(raw_items)}"

        # 4. Clean up
        InventoryService.delete_item(item["_id"], all_units=True, current_user=current_user, db=db)
        remaining = InventoryService.get_items(search=tag, current_user=current_user, db=db, grouped=False)
        assert len(remaining) == 0, f"Expected 0 items after delete, got {len(remaining)}"

        print("All inventory grouping checks PASSED.")

    finally:
        db.close()


if __name__ == "__main__":
    run_check()
