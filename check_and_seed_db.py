import sys
import os

# Add app to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.database.postgres import engine, SessionLocal
from app.database.base import Base
from app.models.user import User
from app.models.role import Role
from app.models.permission import Permission
from app.models.role_permission import RolePermission
from app.models.company import Company
from app.models.location import Location
from app.models.lead import Lead
from app.models.workflow import Workflow
from app.models.customer_type import CustomerType
from app.models.product_type import ProductType
from app.models.category_group import CategoryGroup
from app.models.user_company import UserCompany
from app.models.user_role import UserRole
from app.services.password_service import PasswordService
from uuid import uuid4

def sync_db_and_seed():
    print("--- 1. Syncing Database Schemas (create_all) ---")
    try:
        Base.metadata.create_all(bind=engine)
        print("Successfully synchronized all PostgreSQL table schemas.")
    except Exception as e:
        print("Database schema sync error:", e)
        return

    db = SessionLocal()
    try:
        print("\n--- 2. Checking / Creating Super Admin User ---")
        super_admin = db.query(User).filter(User.email == "superadmin@example.com").first()
        if not super_admin:
            print("Creating superadmin@example.com...")
            role = db.query(Role).filter(Role.role_name == "Super Admin").first()
            if not role:
                role = Role(role_name="Super Admin", description="Super Administrator")
                db.add(role)
                db.commit()
                db.refresh(role)

            hashed_pw = PasswordService.hash_password("password123")
            super_admin = User(
                first_name="Super",
                last_name="Admin",
                email="superadmin@example.com",
                password=hashed_pw,
                is_super_admin=True,
                is_active=True,
            )
            super_admin.roles = [role]
            db.add(super_admin)
            db.commit()
            db.refresh(super_admin)
            print("Super admin created successfully.")
        else:
            # Ensure password is set to password123
            super_admin.password = PasswordService.hash_password("password123")
            super_admin.is_super_admin = True
            super_admin.is_active = True
            db.commit()
            print("Super admin password updated to 'password123'.")

        print("\n--- 3. Seeding Default Master Roles & Permissions ---")
        default_roles = ["CEO", "AVP", "Zonal Head", "Area Head", "Sales Person"]
        for r_name in default_roles:
            r_obj = db.query(Role).filter(Role.role_name == r_name).first()
            if not r_obj:
                r_obj = Role(role_name=r_name, description=f"{r_name} role")
                db.add(r_obj)
        db.commit()

        default_perms = [
            "dashboard.read", "customer.read", "customer.create", "customer.update", "customer.delete",
            "sales.menu", "lead.read", "lead.create", "lead.update", "lead.delete",
            "opportunity.read", "order.read", "inventory.menu", "inventory.read", "inventory.create",
            "masters.menu", "company.read", "company.create", "company.update", "company.delete",
            "location.read", "location.create", "location.update", "location.delete",
            "customer_type.read", "product_type.read", "category_group.read", "unit.read",
            "role.read", "user.read", "user.create", "user.update", "user.delete", "workflow.read"
        ]
        for p_key in default_perms:
            p_obj = db.query(Permission).filter(Permission.permission_name == p_key).first()
            if not p_obj:
                p_obj = Permission(permission_name=p_key, module=p_key.split(".")[0], description=f"Permission for {p_key}")
                db.add(p_obj)
        db.commit()

        print("All default master roles and permissions seeded successfully.")
    except Exception as e:
        print("Error during seeding:", e)
        db.rollback()
    finally:
        db.close()

if __name__ == "__main__":
    sync_db_and_seed()
