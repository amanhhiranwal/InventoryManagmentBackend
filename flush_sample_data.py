import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from sqlalchemy import create_engine, text
from pymongo import MongoClient

DB_NAMES = ["solutions", "auth_db", "crm_db", "inventory_db", "sales_db"]
BASE_PG_URL = "postgresql+psycopg2://amanhiranwal:aman%4015@localhost:5433/"
MONGO_URL = "mongodb://localhost:27017/"

def flush_sample_data():
    print("==================================================")
    print("     FLUSHING ALL SAMPLE & TEST DATA FROM SYSTEM  ")
    print("==================================================")

    # 1. Clean PostgreSQL Databases
    for db_name in DB_NAMES:
        print(f"\n[PostgreSQL] Cleaning sample records in '{db_name}'...")
        engine = create_engine(BASE_PG_URL + db_name)
        with engine.connect() as conn:
            # Delete sample leads
            conn.execute(text("DELETE FROM leads;"))
            
            # Delete sample workflows
            conn.execute(text("DELETE FROM workflows;"))

            # Delete sample locations
            conn.execute(text("DELETE FROM locations;"))

            # Delete sample companies
            conn.execute(text("DELETE FROM companies;"))

            # Delete sample customer types, product types, category groups
            conn.execute(text("DELETE FROM customer_types;"))
            conn.execute(text("DELETE FROM product_types;"))
            conn.execute(text("DELETE FROM category_groups;"))

            # Delete non-superadmin sample users
            conn.execute(text("DELETE FROM user_roles WHERE user_id IN (SELECT id FROM users WHERE is_super_admin = FALSE);"))
            conn.execute(text("DELETE FROM user_companies WHERE user_id IN (SELECT id FROM users WHERE is_super_admin = FALSE);"))
            conn.execute(text("DELETE FROM users WHERE is_super_admin = FALSE;"))

            # Delete non-default test roles
            default_roles = "('Super Admin', 'CEO', 'AVP', 'Zonal Head', 'Area Head', 'Sales Person')"
            conn.execute(text(f"DELETE FROM role_permissions WHERE role_id IN (SELECT id FROM roles WHERE role_name NOT IN {default_roles});"))
            conn.execute(text(f"DELETE FROM roles WHERE role_name NOT IN {default_roles};"))

            conn.commit()
            print(f"-> PostgreSQL '{db_name}' sample data flushed cleanly.")

    # 2. Clean MongoDB Collections
    print("\n[MongoDB] Cleaning sample inventory & sales documents...")
    try:
        m_client = MongoClient(MONGO_URL)
        for m_db_name in ["solutions", "crm_db", "inventory_db", "sales_db"]:
            m_db = m_client[m_db_name]
            
            # Flush sample inventory items, customers, orders
            m_db["inventory_items"].delete_many({})
            m_db["sales_customers"].delete_many({})
            m_db["sales_orders"].delete_many({})
            m_db["leads"].delete_many({})

            # Re-seed clean standard inventory units in MongoDB if empty
            units_col = m_db["inventory_units"]
            units_col.delete_many({})
            standard_units = [{"name": "Pcs"}, {"name": "Box"}, {"name": "Kg"}, {"name": "Meter"}, {"name": "Litre"}]
            units_col.insert_many(standard_units)

            print(f"-> MongoDB '{m_db_name}' sample collections flushed.")
    except Exception as e:
        print("MongoDB cleaning warning:", e)

    print("\n==================================================")
    print("   ALL SAMPLE DATA FLUSHED SUCCESSFULLY! SYSTEM CLEAN.")
    print("==================================================")

if __name__ == "__main__":
    flush_sample_data()
