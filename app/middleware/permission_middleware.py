from fastapi import Depends, HTTPException
from sqlalchemy.orm import Session

from app.database.dependencies import get_db
from app.middleware.auth_middleware import get_current_user
from app.repositories.rbac_repository import RBACRepository


def require_permission(permission_name: str):

    def permission_checker(

        current_user=Depends(get_current_user),

        db: Session = Depends(get_db),

    ):

        user = RBACRepository.get_user(
            db,
            current_user["user_id"],
        )

        if user is None:

            raise HTTPException(
                status_code=401,
                detail="User not found",
            )

        if user.is_super_admin:
            return user

        permission_names = set()
        for role in user.roles:
            role_perms = RBACRepository.get_role_permissions(db, role.id)
            for p in role_perms:
                permission_names.add(p.permission_name)
        print("Required Permission:", permission_name)
        print("Assigned Permissions:", permission_names)

        required_perm = permission_name
        if required_perm != "superAdmin":
            required_perm = "authentication"

        has_perm = (
            required_perm in permission_names
            or (required_perm == "authentication" and "authenication" in permission_names)
        )

        if not has_perm:

            raise HTTPException(
                status_code=403,
                detail="Permission denied",
            )

        return user

    return permission_checker


def require_super_admin(
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    user = RBACRepository.get_user(
        db,
        current_user["user_id"],
    )
    if user is None:
        raise HTTPException(
            status_code=401,
            detail="User not found",
        )
    if not user.is_super_admin:
        raise HTTPException(
            status_code=403,
            detail="Super Admin access required",
        )
    return user
