import sys
import os
from uuid import UUID
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from sqlalchemy import create_engine, text
from app.database.base import Base
from app.models import (
    user, role, permission, role_permission, company, location,
    lead, workflow, customer_type, product_type, category_group,
    user_company, user_role, menu
)
from app.services.password_service import PasswordService
from app.services.menu_service import MenuService

DB_NAMES = ["solutions", "auth_db", "crm_db", "inventory_db", "sales_db"]
BASE_URL = "postgresql+psycopg2://amanhiranwal:aman%4015@localhost:5433/"

def sync_databases():
    print("=== 1. Ensuring All Microservice Databases Exist ===")
    admin_engine = create_engine(BASE_URL + "postgres", isolation_level="AUTOCOMMIT")
    with admin_engine.connect() as conn:
        existing_dbs = [row[0] for row in conn.execute(text("SELECT datname FROM pg_database;"))]
        for db_name in DB_NAMES:
            if db_name not in existing_dbs:
                print(f"Creating database '{db_name}'...")
                conn.execute(text(f'CREATE DATABASE "{db_name}";'))
            else:
                print(f"Database '{db_name}' exists.")

    print("\n=== 2. Synchronizing Table Schemas across all Databases ===")
    for db_name in DB_NAMES:
        print(f"\n--- Syncing DB: {db_name} ---")
        engine = create_engine(BASE_URL + db_name)
        
        # 1. Create tables if missing
        Base.metadata.create_all(bind=engine)
        print(f"Base metadata tables created for '{db_name}'.")

        # 2. Add any missing columns
        with engine.connect() as conn:
            conn.execute(text("ALTER TABLE leads ADD COLUMN IF NOT EXISTS assigned_to_id UUID;"))
            conn.execute(text("ALTER TABLE leads ADD COLUMN IF NOT EXISTS assigned_by_id UUID;"))
            conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS location_id UUID;"))
            conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS is_active BOOLEAN DEFAULT TRUE;"))
            conn.execute(text("ALTER TABLE companies ADD COLUMN IF NOT EXISTS is_active BOOLEAN DEFAULT TRUE;"))
            conn.execute(text("ALTER TABLE locations ADD COLUMN IF NOT EXISTS is_active BOOLEAN DEFAULT TRUE;"))
            conn.commit()

        # 3. Seed superadmin user in db
        from app.models.user import User
        from app.models.role import Role
        from sqlalchemy.orm import sessionmaker

        Session = sessionmaker(bind=engine)
        db = Session()
        try:
            super_admin = db.query(User).filter(User.email == "superadmin@example.com").first()
            if not super_admin:
                role = db.query(Role).filter(Role.role_name == "Super Admin").first()
                if not role:
                    role = Role(role_name="Super Admin", description="Super Administrator")
                    db.add(role)
                    db.commit()
                    db.refresh(role)

                SUPERADMIN_UUID = UUID("84466f6a-4d02-4db8-88c8-7e93ac554d67")
                super_admin = User(
                    id=SUPERADMIN_UUID,
                    first_name="Super",
                    last_name="Admin",
                    email="superadmin@example.com",
                    password=PasswordService.hash_password("password123"),
                    phone_number="1234567890",
                    employee_id="EMP-ADMIN",
                    is_super_admin=True,
                    is_active=True,
                )
                super_admin.roles = [role]
                db.add(super_admin)
                db.commit()
                print(f"Super admin user created in '{db_name}'.")
            else:
                super_admin.password = PasswordService.hash_password("password123")
                super_admin.is_super_admin = True
                super_admin.is_active = True
                db.commit()
                print(f"Super admin user verified in '{db_name}'.")

            MenuService.seed_default_menus(db)
            print(f"Default DB menus seeded in '{db_name}'.")

            # Seed permissions & assign to standard roles
            from app.models.permission import Permission
            from app.models.role_permission import RolePermission
            
            all_perms_keys = [
                "dashboard.read", "customer.read", "sales.menu", "lead.read", "lead.create", "lead.update",
                "opportunity.read", "order.read", "inventory.menu", "inventory.read", "inventory.create",
                "masters.menu", "company.read", "location.read", "customer_type.read", "product_type.read",
                "category_group.read", "unit.read", "role.read", "user.read", "workflow.read", "reports.read"
            ]

            perm_objs = []
            for p_key in all_perms_keys:
                p_obj = db.query(Permission).filter(Permission.permission_name == p_key).first()
                if not p_obj:
                    p_obj = Permission(permission_name=p_key, module=p_key.split(".")[0], description=f"Permission for {p_key}")
                    db.add(p_obj)
                    db.commit()
                    db.refresh(p_obj)
                perm_objs.append(p_obj)

            exec_roles = ["CEO", "AVP", "Zonal Head", "Area Head"]
            for r_name in exec_roles:
                r_obj = db.query(Role).filter(Role.role_name == r_name).first()
                if not r_obj:
                    r_obj = Role(role_name=r_name, description=f"{r_name} role")
                    db.add(r_obj)
                    db.commit()
                    db.refresh(r_obj)
                
                for p_obj in perm_objs:
                    rp_exists = db.query(RolePermission).filter(
                        RolePermission.role_id == r_obj.id,
                        RolePermission.permission_id == p_obj.id
                    ).first()
                    if not rp_exists:
                        db.add(RolePermission(role_id=r_obj.id, permission_id=p_obj.id))
            db.commit()
            print(f"Standard executive role permissions verified in '{db_name}'.")
            from app.services.customer_type_service import CustomerTypeService
            CustomerTypeService.seed_default_customer_types(db)
            print(f"Default sales_customer_type seeded in '{db_name}'.")

            from app.services.state_service import StateService
            StateService.seed_default_states(db)
            print(f"Default sales_state seeded in '{db_name}'.")
        except Exception as e:
            print(f"Error seeding '{db_name}':", e)
            db.rollback()
        finally:
            db.close()

if __name__ == "__main__":
    sync_databases()
