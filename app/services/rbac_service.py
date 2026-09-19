from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.permission import Permission
from app.models.role import Role
from app.models.role_permission import RolePermission
from app.models.user_role import UserRole
from app.repositories.rbac_repository import RBACRepository
from app.services.hierarchy_service import HierarchyService
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
    def delete_permission(permission_id: str, db: Session):

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

        name = (request.role_name or "").strip()

        if not name:
            raise HTTPException(status_code=400, detail="Role name is required.")

        if db.query(Role).filter(Role.role_name.ilike(name)).first():
            raise HTTPException(status_code=409, detail=f"A role named '{name}' already exists.")

        role = Role(
            role_name=name,
            description=(request.description or "").strip() or None,
        )

        return RBACRepository.create_role(
            db,
            role,
        )

    @staticmethod
    def get_roles(db: Session, current_user: dict | None = None):
        """Roles the caller may see, with where each sits in the hierarchy.

        A super admin sees every role. Anyone else - who only needs the list
        to pick roles for their team - sees just the roles below their own.
        """

        roles = RBACRepository.get_roles(db)
        levels = HierarchyService.role_levels(db)

        is_admin = current_user is None or current_user.get("is_super_admin")

        if not is_admin:
            my_roles = HierarchyService.user_role_ids(current_user.get("user_id"), db)
            below = HierarchyService.junior_role_ids(my_roles, db)
            roles = [r for r in roles if str(r.id) in below]

        counts: dict[str, int] = {}
        for row in db.query(UserRole.role_id).all():
            counts[str(row.role_id)] = counts.get(str(row.role_id), 0) + 1

        return [
            {
                "id": str(r.id),
                "role_name": r.role_name,
                "description": r.description,
                "level": levels.get(str(r.id), {}).get("level"),
                "parent_role_ids": levels.get(str(r.id), {}).get("parent_role_ids", []),
                "user_count": counts.get(str(r.id), 0),
            }
            for r in roles
        ]

    @staticmethod
    def update_role(role_id: str, request, db: Session):
        validate_uuid(role_id, "role_id")
        role = RBACRepository.get_role_by_id(db, role_id)

        if role is None:
            raise HTTPException(status_code=404, detail="Role not found")

        if role.role_name == "Super Admin":
            raise HTTPException(status_code=400, detail="The Super Admin role cannot be renamed.")

        name = (request.role_name or "").strip()
        if not name:
            raise HTTPException(status_code=400, detail="Role name is required.")

        clash = (
            db.query(Role)
            .filter(Role.role_name.ilike(name), Role.id != role.id)
            .first()
        )
        if clash:
            raise HTTPException(status_code=409, detail=f"A role named '{name}' already exists.")

        role.role_name = name
        role.description = (request.description or "").strip() or None
        db.commit()
        db.refresh(role)
        return role

    @staticmethod
    def delete_role(role_id: str, db: Session):

        role = RBACRepository.get_role_by_id(
            db,
            role_id,
        )

        if role is None:
            raise HTTPException(
                status_code=404,
                detail="Role not found",
            )

        # Deleting a role that people hold would silently strip their access,
        # and one on the hierarchy chart would break the reporting lines.
        in_use = db.query(UserRole).filter(UserRole.role_id == role.id).count()
        if in_use:
            raise HTTPException(
                status_code=409,
                detail=f"{in_use} user(s) still have this role. Move them to another role first.",
            )

        if str(role.id) in HierarchyService.role_levels(db):
            raise HTTPException(
                status_code=409,
                detail="This role is on a hierarchy chart. Remove it from the workflow first.",
            )

        for mapping in db.query(RolePermission).filter(RolePermission.role_id == role.id).all():
            db.delete(mapping)

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
