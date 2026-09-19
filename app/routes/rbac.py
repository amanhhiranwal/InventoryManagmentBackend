from fastapi import APIRouter, Depends
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
