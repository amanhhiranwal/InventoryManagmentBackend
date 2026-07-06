from app.models.role import Role
from app.repositories.role_repository import RoleRepository


class RoleService:

    @staticmethod
    def create(request, db):

        role = Role(
            role_name=request.role_name,
            description=request.description,
        )

        return RoleRepository.create(db, role)

    @staticmethod
    def list(db):

        return RoleRepository.get_all(db)
