from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.controllers.rbac_controller import RBACController
from app.database.dependencies import get_db

from app.schemas.rbac import (
    CreatePermissionRequest,
    CreateRoleRequest,
)

router = APIRouter(
    prefix="/rbac",
    tags=["RBAC"],
)

# ---------------- Permissions ---------------- #

@router.post("/permissions")
def create_permission(
    request: CreatePermissionRequest,
    db: Session = Depends(get_db),
):
    return RBACController.create_permission(request, db)


@router.get("/permissions")
def get_permissions(
    db: Session = Depends(get_db),
):
    return RBACController.get_permissions(db)


@router.delete("/permissions/{permission_id}")
def delete_permission(
    permission_id: int,
    db: Session = Depends(get_db),
):
    return RBACController.delete_permission(permission_id, db)


# ---------------- Roles ---------------- #

@router.post("/roles")
def create_role(
    request: CreateRoleRequest,
    db: Session = Depends(get_db),
):
    return RBACController.create_role(request, db)


@router.get("/roles")
def get_roles(
    db: Session = Depends(get_db),
):
    return RBACController.get_roles(db)


@router.delete("/roles/{role_id}")
def delete_role(
    role_id: int,
    db: Session = Depends(get_db),
):
    return RBACController.delete_role(role_id, db)


@router.post(
    "/roles/{role_id}/permissions/{permission_id}"
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


@router.get(
    "/roles/{role_id}/permissions"
)
def get_role_permissions(
    role_id: str,
    db: Session = Depends(get_db),
):
    return RBACController.get_permissions_by_role(
        role_id,
        db,
    )


@router.delete(
    "/roles/{role_id}/permissions/{permission_id}"
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