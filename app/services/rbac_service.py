from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.permission import Permission
from app.models.role import Role

from app.repositories.rbac_repository import RBACRepository
from app.models.role_permission import RolePermission
from app.utils.validators import validate_uuid


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

    @staticmethod
    def assign_permission_to_role(
        role_id: str,
        permission_id: str,
        db: Session,
    ):
        validate_uuid(role_id, "role_id")
        validate_uuid(permission_id, "permission_id")

        role = RBACRepository.get_role_by_id(
            db,
            role_id,
        )

        if role is None:
            raise HTTPException(
                status_code=404,
                detail="Role not found",
            )

        permission = RBACRepository.get_permission_by_id(
            db,
            permission_id,
        )

        if permission is None:
            raise HTTPException(
                status_code=404,
                detail="Permission not found",
            )

        existing = RBACRepository.get_role_permission(
            db,
            role_id,
            permission_id,
        )

        if existing:
            raise HTTPException(
                status_code=409,
                detail="Permission already assigned to role",
            )

        role_permission = RolePermission(
            role_id=role_id,
            permission_id=permission_id,
        )

        return RBACRepository.assign_permission_to_role(
            db,
            role_permission,
        )

    @staticmethod
    def get_permissions_by_role(
        role_id: str,
        db: Session,
    ):

        role = RBACRepository.get_role_by_id(
            db,
            role_id,
        )

        if role is None:
            raise HTTPException(
                status_code=404,
                detail="Role not found",
            )

        return RBACRepository.get_permissions_by_role(
            db,
            role_id,
        )

    @staticmethod
    def remove_permission_from_role(
        role_id: str,
        permission_id: str,
        db: Session,
    ):

        mapping = RBACRepository.get_role_permission(
            db,
            role_id,
            permission_id,
        )

        if mapping is None:
            raise HTTPException(
                status_code=404,
                detail="Permission not assigned to role",
            )

        RBACRepository.remove_permission_from_role(
            db,
            mapping,
        )

        return {"message": "Permission removed from role"}
