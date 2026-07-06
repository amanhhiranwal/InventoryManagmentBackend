from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.permission import Permission
from app.models.role import Role

from app.repositories.rbac_repository import RBACRepository


class RBACService:

    # ---------------- Permission ---------------- #

    @staticmethod
    def create_permission(request, db: Session):

        permission = Permission(
        permission_name=request.permission_name,
        module=request.module,
        description=request.description,
        )

        return RBACRepository.create_permission(
            db,
            permission,
        )

    @staticmethod
    def get_permissions(db: Session):
        return RBACRepository.get_permissions(db)

    @staticmethod
    def delete_permission(permission_id: int, db: Session):

        permission = RBACRepository.get_permission_by_id(
            db,
            permission_id,
        )

        if permission is None:
            raise HTTPException(
                status_code=404,
                detail="Permission not found",
            )

        RBACRepository.delete_permission(
            db,
            permission,
        )

        return {"message": "Permission deleted"}

    # ---------------- Role ---------------- #

    @staticmethod
    def create_role(request, db: Session):

        role = Role(
        role_name=request.role_name,
        description=request.description,
    )

        return RBACRepository.create_role(
            db,
            role,
        )

    @staticmethod
    def get_roles(db: Session):
        return RBACRepository.get_roles(db)

    @staticmethod
    def delete_role(role_id: int, db: Session):

        role = RBACRepository.get_role_by_id(
            db,
            role_id,
        )

        if role is None:
            raise HTTPException(
                status_code=404,
                detail="Role not found",
            )

        RBACRepository.delete_role(
            db,
            role,
        )

        return {"message": "Role deleted"}