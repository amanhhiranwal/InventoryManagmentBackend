from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.controllers.role_controller import RoleController
from app.database.dependencies import get_db
from app.middleware.permission_middleware import require_permission
from app.schemas.role import CreateRoleRequest

router = APIRouter(
    prefix="/roles",
    tags=["Roles"],
)


@router.post("/")
def create_role(
    request: CreateRoleRequest,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("role.create")),
):

    return RoleController.create(
        request,
        db,
    )


@router.get("/")
def get_roles(
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("role.read")),
):

    return RoleController.list(db)
