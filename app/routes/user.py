from fastapi import APIRouter, Depends
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
    current_user=Depends(require_super_admin),
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
    current_user=Depends(require_super_admin),
):
    return UserController.delete(user_id, db)


@router.put("/{user_id}")
def update_user(
    user_id: str,
    request: UpdateUserRequest,
    db: Session = Depends(get_db),
    current_user=Depends(require_super_admin),
):
    return UserController.update(user_id, request, db)