from fastapi import Depends, HTTPException
from app.middleware.auth_middleware import get_current_user


def require_permission(permission_name: str):

    def permission_checker(
        current_user=Depends(get_current_user),
    ):
        if not current_user:
            raise HTTPException(
                status_code=401,
                detail="User not found",
            )

        if current_user.get("is_super_admin"):
            return current_user

        permissions = current_user.get("permissions", [])

        if permission_name not in permissions:
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
