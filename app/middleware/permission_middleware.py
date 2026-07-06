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

        permissions = RBACRepository.get_permissions(
            db,
            user.role_id,
        )

        if permission_name not in permissions:

            raise HTTPException(
                status_code=403,
                detail="Permission denied",
            )

        return user

    return permission_checker
