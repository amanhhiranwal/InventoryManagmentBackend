from sqlalchemy.orm import Session

from app.services.rbac_service import RBACService


class RBACController:

    @staticmethod
    def create_permission(request, db: Session):
        return RBACService.create_permission(request, db)

    @staticmethod
    def get_permissions(db: Session):
        return RBACService.get_permissions(db)

    @staticmethod
    def delete_permission(permission_id: int, db: Session):
        return RBACService.delete_permission(permission_id, db)

    @staticmethod
    def create_role(request, db: Session):
        return RBACService.create_role(request, db)

    @staticmethod
    def get_roles(db: Session):
        return RBACService.get_roles(db)

    @staticmethod
    def delete_role(role_id: int, db: Session):
        return RBACService.delete_role(role_id, db)

    @staticmethod
    def assign_permission_to_role(
        role_id: str,
        permission_id: str,
        db: Session,
    ):
        return RBACService.assign_permission_to_role(
            role_id,
            permission_id,
            db,
        )

    @staticmethod
    def get_permissions_by_role(
        role_id: str,
        db: Session,
    ):
        return RBACService.get_permissions_by_role(
            role_id,
            db,
        )

    @staticmethod
    def remove_permission_from_role(
        role_id: str,
        permission_id: str,
        db: Session,
    ):
        return RBACService.remove_permission_from_role(
            role_id,
            permission_id,
            db,
        )
