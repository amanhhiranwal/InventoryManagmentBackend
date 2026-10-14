from app.services.user_service import UserService


class UserController:

    @staticmethod
    def _permissions_by_role(db, role_ids) -> dict[str, list[str]]:
        """What each of these roles grants, in one query rather than one each.

        Asking per role per user meant a list of 37 people asked the same
        handful of questions about the same handful of roles 37 times
        over.
        """

        from app.models.permission import Permission
        from app.models.role_permission import RolePermission

        wanted = {str(r) for r in role_ids}

        if not wanted:
            return {}

        granted: dict[str, list[str]] = {}

        rows = (
            db.query(RolePermission.role_id, Permission.permission_name)
            .join(Permission, Permission.id == RolePermission.permission_id)
            .filter(RolePermission.role_id.in_(list(wanted)))
            .order_by(Permission.permission_name)
            .all()
        )

        for role_id, name in rows:
            granted.setdefault(str(role_id), []).append(name)

        return granted

    @staticmethod
    def _manager_names(db, manager_ids) -> dict[str, str]:
        """Who each of these people is, by id, in one query."""

        from app.models.user import User

        wanted = {str(m) for m in manager_ids if m}

        if not wanted:
            return {}

        rows = (
            db.query(User.id, User.first_name, User.last_name)
            .filter(User.id.in_(list(wanted)))
            .all()
        )

        return {
            str(uid): f"{first or ''} {last or ''}".strip()
            for uid, first, last in rows
        }

    @staticmethod
    def _to_response_dict(user, granted=None, managers=None):
        """One user, as the API returns them.

        ``granted`` and ``managers`` are the lookups a list builds once and
        hands in. Without them the record is assembled on its own, which is
        what a single-user response wants.
        """

        role_ids = [str(r.id) for r in user.roles]
        company_ids = [str(c.id) for c in user.companies]

        from sqlalchemy.orm import object_session

        session = object_session(user)

        if granted is None:
            granted = (
                UserController._permissions_by_role(session, role_ids)
                if session
                else {}
            )

        permissions = []
        seen_perms = set()

        for role_id in role_ids:
            for name in granted.get(role_id, []):
                if name not in seen_perms:
                    seen_perms.add(name)
                    permissions.append(name)

        reports_to_name = None

        if user.reports_to_id:
            if managers is None:
                managers = (
                    UserController._manager_names(session, [user.reports_to_id])
                    if session
                    else {}
                )

            reports_to_name = managers.get(str(user.reports_to_id))

        return {
            "id": str(user.id),
            "first_name": user.first_name,
            "last_name": user.last_name,
            "email": user.email,
            "phone_number": user.phone_number,
            "employee_id": user.employee_id,
            "location": user.location,
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
    def get_all(db, skip: int = 0, limit: int = 100, current_user=None, search: str | None = None):
        result = UserService.get_all(db, skip, limit, current_user, search)

        people = result["data"]

        # The two lookups every row needs, read once for the whole page
        # instead of once per person.
        granted = UserController._permissions_by_role(
            db, {str(r.id) for u in people for r in u.roles}
        )

        managers = UserController._manager_names(
            db, {u.reports_to_id for u in people}
        )

        return {
            "success": True,
            "data": [
                UserController._to_response_dict(u, granted, managers)
                for u in people
            ],
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
    def reset_password(user_id: str, db, current_user=None):
        user = UserService.reset_password(user_id, db, current_user)

        return {
            "success": True,
            "message": (
                f"A temporary password has been emailed to {user.email}. "
                "They will choose their own when they sign in."
            ),
        }

    @staticmethod
    def update(user_id: str, request, db, current_user=None):
        user = UserService.update(user_id, request, db, current_user)
        return {
            "success": True,
            "message": "User updated successfully.",
            "data": UserController._to_response_dict(user),
        }
