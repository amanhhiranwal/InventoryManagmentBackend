from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.controllers.auth_controller import AuthController
from app.database.dependencies import get_db
from app.middleware.auth_middleware import get_current_user
from app.repositories.rbac_repository import RBACRepository
from app.schemas.auth import (
    ChangePasswordRequest,
    ForgotPasswordRequest,
    LoginRequest,
    RegisterSuperAdminRequest,
    ResetPasswordRequest,
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
    db: Session = Depends(get_db),
):
    user = RBACRepository.get_user(db, current_user["user_id"])
    if user is None:
        raise HTTPException(
            status_code=401,
            detail="User not found",
        )

    permissions = []
    seen_perms = set()
    for role in user.roles:
        role_perms = RBACRepository.get_role_permissions(db, role.id)
        for p in role_perms:
            if p.permission_name not in seen_perms:
                seen_perms.add(p.permission_name)
                permissions.append(p)

    role_ids = [str(r.id) for r in user.roles]
    company_ids = [str(c.id) for c in user.companies]

    return {
        "success": True,
        "data": {
            "user_id": str(user.id),
            "email": user.email,
            "role_id": role_ids[0] if role_ids else "",
            "role_ids": role_ids,
            "company_ids": company_ids,
            "is_super_admin": user.is_super_admin,
            "first_name": user.first_name,
            "last_name": user.last_name,
            #: Reachable while the flag stands, so a reloaded tab learns
            #: it still has a password to choose.
            "must_change_password": bool(user.must_change_password),
            "permissions": [
                {
                    "id": p.permission_name,
                    "name": p.permission_name,
                    "description": p.description,
                }
                for p in permissions
            ],
            "exp": current_user.get("exp"),
        },
    }


@router.post("/change-password")
def change_password(
    request: ChangePasswordRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Choose your own password, replacing the one you signed in with.

    Reachable while an account is still on its temporary password -
    everything else is not, which is the point of the flag.
    """

    from app.models.user import User
    from app.services.password_service import PasswordService

    user = db.query(User).filter(User.id == current_user["user_id"]).first()

    if user is None:
        raise HTTPException(status_code=401, detail="User not found")

    if not PasswordService.verify_password(
        request.current_password, user.password
    ):
        raise HTTPException(
            status_code=400,
            detail="That is not your current password.",
        )

    new_password = (request.new_password or "").strip()

    if len(new_password) < 8:
        raise HTTPException(
            status_code=400,
            detail="A password needs at least 8 characters.",
        )

    if PasswordService.verify_password(new_password, user.password):
        raise HTTPException(
            status_code=400,
            detail="Choose a password you have not just been sent.",
        )

    user.password = PasswordService.hash_password(new_password)
    user.must_change_password = False

    db.commit()

    return {
        "success": True,
        "message": "Password changed. That is the one to use from now on.",
    }


@router.post("/logout")
async def logout():

    return {
        "success": True,
        "message": "Logged out successfully"
    }


@router.post("/forgot-password")
def forgot_password(
    request: ForgotPasswordRequest,
    db: Session = Depends(get_db),
):
    return AuthController.forgot_password(
        request,
        db,
    )


@router.post("/reset-password")
def reset_password(
    request: ResetPasswordRequest,
    db: Session = Depends(get_db),
):
    return AuthController.reset_password(
        request,
        db,
    )
