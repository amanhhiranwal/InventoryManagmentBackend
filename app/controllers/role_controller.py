from app.services.role_service import RoleService


class RoleController:

    @staticmethod
    def create(request, db):

        role = RoleService.create(
            request,
            db,
        )

        return {
            "success": True,
            "data": role,
        }

    @staticmethod
    def list(db):

        roles = RoleService.list(db)

        return {
            "success": True,
            "data": roles,
        }
