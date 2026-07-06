from sqlalchemy.orm import Session

from app.schemas.auth import RegisterSuperAdminRequest
from app.services.auth_service import AuthService


class AuthController:

    @staticmethod
    def register_super_admin(
        request: RegisterSuperAdminRequest,
        db: Session,
    ):

        user = AuthService.register_super_admin(
            request,
            db,
        )

        return {
            "success": True,
            "message": "Super Admin created successfully.",
            "data": {
                "id": str(user.id),
                "email": user.email,
            },
        }
    


    @staticmethod
    def login(request, db):

        result = AuthService.login(
            request,
            db,
        )

        user = result["user"]

        return {
            "success": True,
            "access_token": result["access_token"],
            "token_type": "Bearer",
            "user": {
                "id": str(user.id),
                "first_name": user.first_name,
                "last_name": user.last_name,
                "email": user.email,
            },
        }