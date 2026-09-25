from app.services.user_service import UserService


class UserController:

    @staticmethod
    def _to_response_dict(user):
        role_ids = [str(r.id) for r in user.roles]
        company_ids = [str(c.id) for c in user.companies]
        
        from sqlalchemy.orm import object_session

        from app.repositories.rbac_repository import RBACRepository
        session = object_session(user)
        permissions = []
        if session:
            seen_perms = set()
            for role in user.roles:
                role_perms = RBACRepository.get_role_permissions(session, role.id)
                for p in role_perms:
                    if p.permission_name not in seen_perms:
                        seen_perms.add(p.permission_name)
                        permissions.append(p.permission_name)

        reports_to_name = None
        if session and user.reports_to_id:
            from app.models.user import User

            manager = session.get(User, user.reports_to_id)
            if manager:
                reports_to_name = f"{manager.first_name} {manager.last_name}".strip()

        return {
            "id": str(user.id),
            "first_name": user.first_name,
            "last_name": user.last_name,
            "email": user.email,
            "phone_number": user.phone_number,
            "employee_id": user.employee_id,
            "role_id": role_ids[0] if role_ids else "",
            "role_ids": role_ids,
            # Named as well as identified, so a picker can show "Priya (Zonal
            # Head)" without loading the roles master first.
            "role_names": [r.role_name for r in user.roles],
            "company_ids": company_ids,
            "permissions": permissions,
            "is_super_admin": user.is_super_admin,
            "is_active": user.is_active,
            "reports_to_id": str(user.reports_to_id) if user.reports_to_id else None,
            "reports_to_name": reports_to_name,
        }

    @staticmethod
    def create(request, db, current_user=None):
        user = UserService.create(request, db, current_user)
        return {
            "success": True,
            "message": "User created successfully.",
            "data": UserController._to_response_dict(user),
        }

    @staticmethod
    def get_all(db, skip: int = 0, limit: int = 100, current_user=None):
        result = UserService.get_all(db, skip, limit, current_user)
        return {
            "success": True,
            "data": [UserController._to_response_dict(u) for u in result["data"]],
            "total": result["total"],
        }

    @staticmethod
    def update_role(user_id: str, role_ids: list[str], company_ids: list[str], db, current_user=None):
        user = UserService.update_role(user_id, role_ids, company_ids, db, current_user)
        return {
            "success": True,
            "message": "User roles and companies updated successfully.",
            "data": UserController._to_response_dict(user),
        }

    @staticmethod
    def delete(user_id: str, db, current_user=None):
        UserService.delete(user_id, db, current_user)
        return {
            "success": True,
            "message": "User deleted successfully.",
        }

    @staticmethod
    def update(user_id: str, request, db, current_user=None):
        user = UserService.update(user_id, request, db, current_user)
        return {
            "success": True,
            "message": "User updated successfully.",
            "data": UserController._to_response_dict(user),
        }
