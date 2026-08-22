from fastapi import APIRouter, Depends, Query
from typing import List
from sqlalchemy.orm import Session

from app.controllers.user_controller import UserController
from app.database.dependencies import get_db
from app.middleware.permission_middleware import require_permission, require_super_admin
from app.schemas.user import CreateUserRequest, UpdateUserRoleRequest, UpdateUserRequest

router = APIRouter(
    prefix="/users",
    tags=["Users"],
)


@router.post("/")
def create_user(
    request: CreateUserRequest,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("user.create")),
):
    return UserController.create(request, db)


@router.get("/")
def get_users(
    page: int = 1,
    size: int = 20,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("user.read")),
):
    skip = (page - 1) * size
    return UserController.get_all(db, skip=skip, limit=size)


@router.put("/{user_id}/role")
def update_user_role(
    user_id: str,
    request: UpdateUserRoleRequest,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("user.update")),
):
    return UserController.update_role(user_id, request.role_ids, request.company_ids, db)


@router.delete("/{user_id}")
def delete_user(
    user_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("user.delete")),
):
    return UserController.delete(user_id, db)


@router.put("/{user_id}")
def update_user(
    user_id: str,
    request: UpdateUserRequest,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("user.update")),
):
    return UserController.update(user_id, request, db)


@router.get("/by-roles", tags=["Microservice Internal"])
def get_users_by_roles(
    role_ids: List[str] = Query(...),
    db: Session = Depends(get_db)
):
    from app.models.user_role import UserRole
    from uuid import UUID
    role_uuids = [UUID(rid) for rid in role_ids]
    user_roles = db.query(UserRole.user_id).filter(UserRole.role_id.in_(role_uuids)).all()
    user_ids = [str(ur.user_id) for ur in user_roles]
    return {"success": True, "user_ids": user_ids}


@router.get("/{user_id}/role-ids", tags=["Microservice Internal"])
def get_user_role_ids(
    user_id: str,
    db: Session = Depends(get_db)
):
    from app.repositories.rbac_repository import RBACRepository
    user = RBACRepository.get_user(db, user_id)
    if not user:
        return {"success": False, "role_ids": []}
    role_ids = [str(r.id) for r in user.roles]
    return {"success": True, "role_ids": role_ids}


@router.get("/names", tags=["Microservice Internal"])
def get_user_names(
    user_ids: List[str] = Query(...),
    db: Session = Depends(get_db)
):
    from app.models.user import User
    from uuid import UUID
    user_uuids = [UUID(uid) for uid in user_ids]
    users = db.query(User).filter(User.id.in_(user_uuids)).all()
    names = {str(u.id): f"{u.first_name} {u.last_name}" for u in users}
    return {"success": True, "names": names}