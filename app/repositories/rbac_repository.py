from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.models.user import User
from app.models.role import Role
from app.models.permission import Permission
from app.models.role_permission import RolePermission


class RBACRepository:

    # =========================
    # User
    # =========================

    @staticmethod
    def get_user(db: Session, user_id: str):
        return (
            db.query(User)
            .filter(User.id == user_id)
            .first()
        )

    # =========================
    # Permission CRUD
    # =========================

    @staticmethod
    def create_permission(
        db: Session,
        permission: Permission,
    ):
        existing = (
            db.query(Permission)
            .filter(
                Permission.permission_name == permission.permission_name
            )
            .first()
        )

        if existing:
            raise HTTPException(
                status_code=409,
                detail=f"Permission '{permission.permission_name}' already exists."
            )

        try:
            db.add(permission)
            db.commit()
            db.refresh(permission)
            return permission

        except IntegrityError:
            db.rollback()
            raise HTTPException(
                status_code=409,
                detail="Permission already exists."
            )

        except SQLAlchemyError:
            db.rollback()
            raise HTTPException(
                status_code=500,
                detail="Failed to create permission."
            )

    @staticmethod
    def get_permissions(db: Session):
        return (
            db.query(Permission)
            .order_by(Permission.permission_name)
            .all()
        )

    @staticmethod
    def get_permission_by_id(
        db: Session,
        permission_id: str,
    ):
        return (
            db.query(Permission)
            .filter(Permission.id == permission_id)
            .first()
        )

    @staticmethod
    def get_permission_by_name(
        db: Session,
        permission_name: str,
    ):
        return (
            db.query(Permission)
            .filter(
                Permission.permission_name == permission_name
            )
            .first()
        )

    @staticmethod
    def delete_permission(
        db: Session,
        permission: Permission,
    ):
        try:
            db.delete(permission)
            db.commit()

        except SQLAlchemyError:
            db.rollback()
            raise HTTPException(
                status_code=500,
                detail="Failed to delete permission."
            )

    # =========================
    # Role CRUD
    # =========================

    @staticmethod
    def create_role(
        db: Session,
        role: Role,
    ):
        existing = (
            db.query(Role)
            .filter(
                Role.role_name == role.role_name
            )
            .first()
        )

        if existing:
            raise HTTPException(
                status_code=409,
                detail=f"Role '{role.role_name}' already exists."
            )

        try:
            db.add(role)
            db.commit()
            db.refresh(role)
            return role

        except IntegrityError:
            db.rollback()
            raise HTTPException(
                status_code=409,
                detail="Role already exists."
            )

        except SQLAlchemyError:
            db.rollback()
            raise HTTPException(
                status_code=500,
                detail="Failed to create role."
            )

    @staticmethod
    def get_roles(db: Session):
        return (
            db.query(Role)
            .order_by(Role.role_name)
            .all()
        )

    @staticmethod
    def get_role_by_id(
        db: Session,
        role_id: str,
    ):
        return (
            db.query(Role)
            .filter(Role.id == role_id)
            .first()
        )

    @staticmethod
    def get_role_by_name(
        db: Session,
        role_name: str,
    ):
        return (
            db.query(Role)
            .filter(
                Role.role_name == role_name
            )
            .first()
        )

    @staticmethod
    def delete_role(
        db: Session,
        role: Role,
    ):
        try:
            db.delete(role)
            db.commit()

        except SQLAlchemyError:
            db.rollback()
            raise HTTPException(
                status_code=500,
                detail="Failed to delete role."
            )

    # =========================
    # Role Permissions
    # =========================

    @staticmethod
    def add_permission_to_role(
        db: Session,
        role_permission: RolePermission,
    ):
        existing = (
            db.query(RolePermission)
            .filter(
                RolePermission.role_id == role_permission.role_id,
                RolePermission.permission_id == role_permission.permission_id,
            )
            .first()
        )

        if existing:
            raise HTTPException(
                status_code=409,
                detail="Permission is already assigned to this role."
            )

        try:
            db.add(role_permission)
            db.commit()
            db.refresh(role_permission)
            return role_permission

        except IntegrityError:
            db.rollback()
            raise HTTPException(
                status_code=409,
                detail="Permission is already assigned to this role."
            )

        except SQLAlchemyError:
            db.rollback()
            raise HTTPException(
                status_code=500,
                detail="Failed to assign permission to role."
            )

    @staticmethod
    def get_role_permissions(
        db: Session,
        role_id: str,
    ):
        permissions = (
            db.query(Permission)
            .join(
                RolePermission,
                Permission.id == RolePermission.permission_id,
            )
            .filter(
                RolePermission.role_id == role_id
            )
            .order_by(Permission.permission_name)
            .all()
        )

        return permissions

    @staticmethod
    def remove_permission_from_role(
        db: Session,
        role_id: str,
        permission_id: str,
    ):
        role_permission = (
            db.query(RolePermission)
            .filter(
                RolePermission.role_id == role_id,
                RolePermission.permission_id == permission_id,
            )
            .first()
        )

        if role_permission is None:
            raise HTTPException(
                status_code=404,
                detail="Permission assignment not found."
            )

        try:
            db.delete(role_permission)
            db.commit()

        except SQLAlchemyError:
            db.rollback()
            raise HTTPException(
                status_code=500,
                detail="Failed to remove permission from role."
            )

        return role_permission