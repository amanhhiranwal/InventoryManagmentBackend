from sqlalchemy.orm import Session

from app.core.config import settings
from app.schemas.auth import (
    ForgotPasswordRequest,
    RegisterSuperAdminRequest,
    ResetPasswordRequest,
)
from app.services.auth_service import AuthService
from app.services.password_reset_service import PasswordResetService


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
                "is_super_admin": user.is_super_admin,
            },
        }


    @staticmethod
    def forgot_password(
        request: ForgotPasswordRequest,
        db: Session,
    ):

        reset_link = PasswordResetService.forgot_password(
            request.email,
            db,
        )

        response = {
            "success": True,
            "message": (
                "If an account exists for that email, "
                "a password reset link has been sent."
            ),
        }

        # Outside production the link is echoed back so the flow can be
        # exercised without a configured mail server.
        if reset_link and settings.ENV != "production" and not settings.SMTP_HOST:
            response["reset_link"] = reset_link

        return response


    @staticmethod
    def reset_password(
        request: ResetPasswordRequest,
        db: Session,
    ):

        PasswordResetService.reset_password(
            token=request.token,
            new_password=request.password,
            db=db,
            email=request.email,
        )

        return {
            "success": True,
            "message": "Password reset successful. You can now sign in with your new password.",
        }
