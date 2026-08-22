from fastapi import Depends, HTTPException
from sqlalchemy.orm import Session
from app.middleware.auth_middleware import get_current_user
from app.database.dependencies import get_db

ALIAS_MAP = {
    "company.read": {"company.read", "company.view", "company.create", "company.update", "lead.read", "sales.menu"},
    "company.view": {"company.read", "company.view", "company.create", "company.update", "lead.read", "sales.menu"},
    "company.create": {"company.read", "company.view", "company.create", "company.update", "lead.read", "sales.menu"},
    "company.update": {"company.read", "company.view", "company.create", "company.update", "lead.read", "sales.menu"},
    "company.delete": {"company.delete"},

    "location.read": {"location.read", "location.view", "location.create", "location.update", "lead.read", "sales.menu"},
    "location.view": {"location.read", "location.view", "location.create", "location.update", "lead.read", "sales.menu"},
    "location.create": {"location.read", "location.view", "location.create", "location.update", "lead.read", "sales.menu"},
    "location.update": {"location.read", "location.view", "location.create", "location.update", "lead.read", "sales.menu"},
    "location.delete": {"location.delete"},

    "customer_type.read": {"customer_type.read", "customer_type.view", "customer_type.create", "lead.read", "sales.menu"},
    "customer_type.create": {"customer_type.read", "customer_type.view", "customer_type.create", "lead.read", "sales.menu"},
    "customer_type.delete": {"customer_type.delete"},

    "product_type.read": {"product_type.read", "product_type.view", "product_type.create", "lead.read", "sales.menu"},
    "product_type.create": {"product_type.read", "product_type.view", "product_type.create", "lead.read", "sales.menu"},
    "product_type.update": {"product_type.update"},
    "product_type.delete": {"product_type.delete"},

    "category_group.read": {"category_group.read", "category_group.view", "category_group.create", "lead.read", "sales.menu"},
    "category_group.create": {"category_group.read", "category_group.view", "category_group.create", "lead.read", "sales.menu"},
    "category_group.delete": {"category_group.delete"},

    "workflow.read": {"workflow.read", "workflow.view", "workflow.create", "lead.read", "sales.menu"},
    "workflow.create": {"workflow.read", "workflow.view", "workflow.create", "lead.read", "sales.menu"},
    "workflow.update": {"workflow.update"},
    "workflow.delete": {"workflow.delete"},

    "user.read": {"user.read", "user.view", "user.create", "lead.read", "lead.create", "sales.menu"},
    "user.create": {"user.read", "user.view", "user.create"},
    "user.update": {"user.update"},

    "role.read": {"role.read", "role.view", "role.create", "lead.read", "sales.menu"},
    "role.create": {"role.read", "role.view", "role.create"},

    "lead.read": {"lead.read", "lead.view", "lead.create", "sales.menu"},
    "opportunity.read": {"opportunity.read", "opportunity.view", "sales.menu"},
    "order.read": {"order.read", "order.view", "sales.menu"},
    "inventory.read": {"inventory.read", "inventory.view", "lead.read", "lead.create", "sales.menu"},
    "unit.read": {"unit.read", "unit.view", "lead.read", "sales.menu"},
    "customer.read": {"customer.read", "customer.view", "sales.menu"},
}

def require_permission(permission_name: str):

    def permission_checker(
        current_user=Depends(get_current_user),
        db: Session = Depends(get_db),
    ):
        if not current_user:
            raise HTTPException(
                status_code=401,
                detail="User not found",
            )

        if current_user.get("is_super_admin"):
            return current_user

        # Collect user permissions from JWT token
        user_permissions = set(current_user.get("permissions", []))

        # Try fetching live DB permissions if available
        user_id = current_user.get("user_id")
        if user_id:
            try:
                from app.repositories.rbac_repository import RBACRepository
                user_obj = RBACRepository.get_user(db, user_id)
                if user_obj and user_obj.roles:
                    for role in user_obj.roles:
                        role_perms = RBACRepository.get_role_permissions(db, role.id)
                        for p in role_perms:
                            user_permissions.add(p.permission_name)
            except Exception:
                pass

        target_keys = ALIAS_MAP.get(permission_name, {permission_name})

        if not any(k in user_permissions for k in target_keys):
            raise HTTPException(
                status_code=403,
                detail=f"Permission denied. Required: {permission_name}",
            )

        return current_user

    return permission_checker


def require_super_admin(
    current_user=Depends(get_current_user),
):
    if not current_user:
        raise HTTPException(
            status_code=401,
            detail="User not found",
        )
    if not current_user.get("is_super_admin"):
        raise HTTPException(
            status_code=403,
            detail="Super Admin access required",
        )
    return current_user
