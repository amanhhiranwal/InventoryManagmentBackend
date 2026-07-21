from app.services.user_service import UserService

class UserController:

    @staticmethod
    def _to_response_dict(user):
        role_ids = [str(r.id) for r in user.roles]
        company_ids = [str(c.id) for c in user.companies]
        
        from app.repositories.rbac_repository import RBACRepository
        from sqlalchemy.orm import object_session
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

        return {
            "id": str(user.id),
            "first_name": user.first_name,
            "last_name": user.last_name,
            "email": user.email,
            "phone_number": user.phone_number,
            "employee_id": user.employee_id,
            "role_id": role_ids[0] if role_ids else "",
            "role_ids": role_ids,
            "company_ids": company_ids,
            "permissions": permissions,
            "is_super_admin": user.is_super_admin,
            "is_active": user.is_active,
        }

    @staticmethod
    def create(request, db):
        user = UserService.create(request, db)
        return {
            "success": True,
            "message": "User created successfully.",
            "data": UserController._to_response_dict(user),
        }

    @staticmethod
    def get_all(db, skip: int = 0, limit: int = 100):
        result = UserService.get_all(db, skip, limit)
        return {
            "success": True,
            "data": [UserController._to_response_dict(u) for u in result["data"]],
            "total": result["total"],
        }

    @staticmethod
    def update_role(user_id: str, role_ids: list[str], company_ids: list[str], db):
        user = UserService.update_role(user_id, role_ids, company_ids, db)
        return {
            "success": True,
            "message": "User roles and companies updated successfully.",
            "data": UserController._to_response_dict(user),
        }

    @staticmethod
    def delete(user_id: str, db):
        UserService.delete(user_id, db)
        return {
            "success": True,
            "message": "User deleted successfully.",
        }

    @staticmethod
    def update(user_id: str, request, db):
        user = UserService.update(user_id, request, db)
        return {
            "success": True,
            "message": "User updated successfully.",
            "data": UserController._to_response_dict(user),
        }
