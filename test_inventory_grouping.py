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

        # 4. Clean up multi-serial test
        InventoryService.delete_item(item["_id"], all_units=True, current_user=current_user, db=db)
        remaining = InventoryService.get_items(search=tag, current_user=current_user, db=db, grouped=False)
        assert len(remaining) == 0, f"Expected 0 items after delete, got {len(remaining)}"

        # 5. Direct creation without serial number
        non_serial = InventoryService.create_item(
            name=f"NonSerial {tag}",
            serial_number="",
            product_type_code="COMPUTE",
            category="Hardware Solutions",
            attributes={
                "model_number": f"MDL-NS-{tag}",
                "specification": "No serial batch",
                "rate": 500.0,
                "unit": "Nos",
                "instock": 25.0,
                "case_size": 1.0,
            },
            current_user=current_user,
            db=db,
        )
        assert non_serial["serial_number"] == ""

        # 6. Bulk upload CSV without Hardware Serial Number column
        from app.services import inventory_import_service
        csv_without_serial = (
            "Model Number,Product Name,Specification,Product Type Code,Unit,ECP Rate,In-Stock Qty,Case Size\n"
            f"MDL-CSV1-{tag},CSV Item 1 {tag},No serial column,COMPUTE,Nos,250,50,1\n"
            f"MDL-CSV2-{tag},CSV Item 2 {tag},Blank serial row,COMPUTE,Nos,300,10,1\n"
        ).encode("utf-8")

        res_csv = inventory_import_service.load(
            content=csv_without_serial,
            filename="test_upload.csv",
            current_user=current_user,
            db=db,
            commit=True,
        )
        assert len(res_csv["errors"]) == 0, f"Bulk upload failed with errors: {res_csv['errors']}"
        assert res_csv["created"] == 2, f"Expected 2 created products, got {res_csv['created']}"

        # 7. Verify both imported items exist without duplicate clashes
        csv_items = InventoryService.get_items(search=tag, current_user=current_user, db=db, grouped=True)
        # Total 3 items: non_serial, CSV Item 1, CSV Item 2
        assert len(csv_items) == 3, f"Expected 3 items with tag, got {len(csv_items)}"
        for ci in csv_items:
            assert ci["serial_number"] == ""
            assert ci["serials_count"] == 0
            assert ci["serial_numbers"] == []

        # 8. Clean up all tag items
        sync_mongo_db["inventory_items"].delete_many({
            "$or": [
                {"name": {"$regex": tag}},
                {"attributes.model_number": {"$regex": tag}},
            ]
        })

        print("All inventory grouping & optional serial checks PASSED.")

    finally:
        db.close()


if __name__ == "__main__":
    run_check()
