from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.controllers.auth_controller import AuthController
from app.database.dependencies import get_db
from app.middleware.auth_middleware import get_current_user
from app.schemas.auth import (
    LoginRequest,
    RegisterSuperAdminRequest,
)

router = APIRouter(
    prefix="/auth",
    tags=["Authentication"],
)


@router.post("/register-super-admin")
def register_super_admin(
    request: RegisterSuperAdminRequest,
    db: Session = Depends(get_db),
):
    return AuthController.register_super_admin(
        request,
        db,
    )


@router.post("/login")
def login(
    request: LoginRequest,
    db: Session = Depends(get_db),
):
    return AuthController.login(
        request,
        db,
    )


@router.get("/me")
def me(
    current_user=Depends(get_current_user),
):
    return {
        "success": True,
        "data": current_user,
    }