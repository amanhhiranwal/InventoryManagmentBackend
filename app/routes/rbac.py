from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.controllers.rbac_controller import RBACController
from app.database.dependencies import get_db
from app.middleware.permission_middleware import (
    require_granted,
    require_permission,
    require_super_admin,
)
from app.schemas.rbac import (
    CreatePermissionRequest,
    CreateRoleRequest,
)
from app.services.hierarchy_service import HierarchyService

router = APIRouter(
    prefix="/rbac",
    tags=["RBAC"],
)

# Only a super admin creates roles or decides which pages each role gets.
# A role given Roles & Access may look, read-only, at the roles below its
# own - never its own level or above. The role list is also read by the
# Accounts page to pick roles, and is cut down the same way.

# ---------------- Permissions ---------------- #

@router.post(
    "/permissions",
    dependencies=[Depends(require_super_admin)],
)
def create_permission(
    request: CreatePermissionRequest,
    db: Session = Depends(get_db),
):
    return RBACController.create_permission(request, db)


@router.get(
    "/permissions",
    dependencies=[Depends(require_granted("role.read"))],
)
def get_permissions(
    db: Session = Depends(get_db),
):
    return RBACController.get_permissions(db)


@router.delete(
    "/permissions/{permission_id}",
    dependencies=[Depends(require_super_admin)],
)
def delete_permission(
    permission_id: str,
    db: Session = Depends(get_db),
):
    return RBACController.delete_permission(permission_id, db)


# ---------------- Roles ---------------- #

@router.post(
    "/roles",
    dependencies=[Depends(require_super_admin)],
)
def create_role(
    request: CreateRoleRequest,
    db: Session = Depends(get_db),
):
    return RBACController.create_role(request, db)


@router.get("/roles")
def get_roles(
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("role.read")),
):
    return RBACController.get_roles(db, current_user)


@router.put(
    "/roles/{role_id}",
    dependencies=[Depends(require_super_admin)],
)
def update_role(
    role_id: str,
    request: CreateRoleRequest,
    db: Session = Depends(get_db),
):
    return RBACController.update_role(role_id, request, db)


@router.delete(
    "/roles/{role_id}",
    dependencies=[Depends(require_super_admin)],
)
def delete_role(
    role_id: str,
    db: Session = Depends(get_db),
):
    return RBACController.delete_role(role_id, db)


@router.post(
    "/roles/{role_id}/permissions/{permission_id}",
    dependencies=[Depends(require_super_admin)],
)
def assign_permission(
    role_id: str,
    permission_id: str,
    db: Session = Depends(get_db),
):
    return RBACController.assign_permission_to_role(
        role_id,
        permission_id,
        db,
    )


@router.get("/roles/{role_id}/permissions")
def get_role_permissions(
    role_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(require_granted("role.read")),
):
    HierarchyService.assert_role_below(current_user, role_id, db)

    return RBACController.get_permissions_by_role(
        role_id,
        db,
    )


@router.delete(
    "/roles/{role_id}/permissions/{permission_id}",
    dependencies=[Depends(require_super_admin)],
)
def remove_permission(
    role_id: str,
    permission_id: str,
    db: Session = Depends(get_db),
):
    return RBACController.remove_permission_from_role(
        role_id,
        permission_id,
        db,
    )


# ----------------------------------------------------------------------
# Store access: which stores a role works in
# ----------------------------------------------------------------------
class RoleStoresRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    warehouse_ids: list[str] = Field(default_factory=list, max_length=500)


@router.get("/roles/{role_id}/warehouses")
def get_role_warehouses(
    role_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(require_granted("role.read")),
):
    """Every store, and whether this role is given it."""

    from app.services.warehouse_service import RoleStoreService

    HierarchyService.assert_role_below(current_user, role_id, db)
    return {"success": True, "data": RoleStoreService.list_for_role(role_id, db)}


@router.put(
    "/roles/{role_id}/warehouses",
    dependencies=[Depends(require_super_admin)],
)
def set_role_warehouses(role_id: str, request: RoleStoresRequest, db: Session = Depends(get_db)):
    """Give the role exactly these stores. A store given to any role or person
    is then kept for them alone."""

    from app.services.warehouse_service import RoleStoreService

    return {"success": True, "message": "Store access saved.", "data": RoleStoreService.set_for_role(role_id, request.warehouse_ids, db)}


# ----------------------------------------------------------------------
# Products shown: which products a role sees on the Inventory list
# ----------------------------------------------------------------------
class RoleProductScopeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scope: str = Field(pattern="^(OWN_COMPANIES|ALL_COMPANIES|STORE_STOCK)$")


@router.get("/roles/{role_id}/product-scope")
def get_role_product_scope(
    role_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(require_granted("role.read")),
):
    """Own companies (the default), all companies, or what is in stock at the role's stores."""

    from app.services.warehouse_service import RoleStoreService

    HierarchyService.assert_role_below(current_user, role_id, db)
    return {"success": True, "data": RoleStoreService.product_scope_for_role(role_id, db)}


@router.put(
    "/roles/{role_id}/product-scope",
    dependencies=[Depends(require_super_admin)],
)
def set_role_product_scope(role_id: str, request: RoleProductScopeRequest, db: Session = Depends(get_db)):
    from app.services.warehouse_service import RoleStoreService

    return {"success": True, "message": "Products shown saved.",
            "data": RoleStoreService.set_product_scope(role_id, request.scope, db)}
