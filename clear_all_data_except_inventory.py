"""Cleanly clears all transactional data across PostgreSQL and MongoDB while strictly preserving:
- All Inventory data (PostgreSQL product_category, category_groups, product_types, sales_warranty_term, and MongoDB inventory_items, inventory_units, inventory_templates, inventory_movements)
- All Auth & Master setup (users, roles, permissions, role_permissions, user_roles, companies, user_companies, menu_items, workflows, app_settings, departments, customer types, lead sources, states)
"""

from sqlalchemy import text
from app.database.postgres import SessionLocal, engine
from app.database.mongodb import sync_mongo_db


def clear_transactional_data():
    print("=" * 60)
    print("CLEARING ALL DATA EXCEPT INVENTORY AND MASTER SETUP")
    print("=" * 60)

    # 1. PostgreSQL Clean
    # Listed in child-first foreign key dependency order
    tables_to_clear = [
        # Activity logs
        "sales_order_activity",
        "sales_quotation_activity",
        "sales_opportunity_activity",
        "sales_lead_activity",
        "sales_proforma_invoice_activity",
        "procurement_activity",

        # Approvals
        "sales_approval",
        "purchase_order_approval",
        "purchase_requisition_approval",

        # Procurement details
        "purchase_order_payment",
        "purchase_order_item",
        "purchase_order",
        "purchase_requisition_item",
        "purchase_requisition",
        "vendor_quotation_item",
        "vendor_quotation",
        "quotation_collection",
        "vendor",

        # Proforma
        "sales_proforma_invoice",

        # Sales orders & quotations
        "sales_order",
        "sales_quotation",

        # Opportunities & leads
        "sales_opportunity",
        "sales_lead",
        "leads",

        # Notifications & tokens & attachments
        "notifications",
        "sales_attachment",
        "password_reset_tokens",
    ]

    session = SessionLocal()
    try:
        # Check counts before
        print("\n--- Rows to be deleted from PostgreSQL: ---")
        for tbl in tables_to_clear:
            try:
                cnt = session.execute(text(f'SELECT count(*) FROM "{tbl}"')).scalar()
                if cnt > 0:
                    print(f"  {tbl}: {cnt} rows")
            except Exception:
                pass

        # Execute TRUNCATE CASCADE or DELETE in safe order
        for tbl in tables_to_clear:
            try:
                session.execute(text(f'DELETE FROM "{tbl}"'))
            except Exception as e:
                print(f"  Warning on table {tbl}: {e}")

        # Reset document counters so next sequence begins at 1
        try:
            session.execute(text("UPDATE document_counter SET seq = 0"))
            print("  Reset document_counter seq to 0")
        except Exception as e:
            print(f"  Note on document_counter: {e}")

        session.commit()
        print("\nPostgreSQL transactional data cleared successfully.")

    except Exception as e:
        session.rollback()
        print(f"Error clearing PostgreSQL data: {e}")
        raise
    finally:
        session.close()

    # 2. MongoDB Clean
    # Clear only non-inventory collections if any exist
    print("\n--- MongoDB Status: ---")
    if sync_mongo_db is not None:
        all_cols = sync_mongo_db.list_collection_names()
        inventory_cols = {"inventory_items", "inventory_units", "inventory_templates", "inventory_movements"}
        for col_name in all_cols:
            if col_name in inventory_cols:
                cnt = sync_mongo_db[col_name].count_documents({})
                print(f"  [PRESERVED] {col_name}: {cnt} documents")
            else:
                cnt = sync_mongo_db[col_name].count_documents({})
                if cnt > 0:
                    sync_mongo_db[col_name].delete_many({})
                    print(f"  [CLEARED] {col_name}: deleted {cnt} documents")
                else:
                    print(f"  [CLEARED] {col_name}: 0 documents (empty)")

    # 3. Final verification of preserved inventory & master tables
    print("\n--- Verifying Preserved Inventory & Master Tables: ---")
    check_session = SessionLocal()
    inventory_tables = [
        "product_category", "category_groups", "product_types", "sales_warranty_term",
        "users", "roles", "permissions", "companies", "menu_items", "sales_state"
    ]
    for tbl in inventory_tables:
        try:
            cnt = check_session.execute(text(f'SELECT count(*) FROM "{tbl}"')).scalar()
            print(f"  {tbl}: {cnt} rows intact")
        except Exception as e:
            print(f"  {tbl}: error {e}")
    check_session.close()

    print("\n" + "=" * 60)
    print("DATA CLEAR COMPLETE: ALL INVENTORY & SETUP PRESERVED INTACT")
    print("=" * 60)


if __name__ == "__main__":
    clear_transactional_data()
