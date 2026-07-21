from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.user import User
from app.repositories.auth_repository import AuthRepository
from app.schemas.auth import RegisterSuperAdminRequest
from app.services.password_service import PasswordService

from app.services.jwt_service import JWTService


class AuthService:

    @staticmethod
    def register_super_admin(
        request: RegisterSuperAdminRequest,
        db: Session,
    ):

        admin = AuthRepository.get_super_admin(db)

        if admin:
            raise HTTPException(
                status_code=400,
                detail="Super Admin already exists.",
            )

        role = AuthRepository.get_role_by_name(
            db,
            "Super Admin",
        )

        if role is None:
            role = AuthRepository.create_role(
                db,
                "Super Admin",
            )

        hashed_password = PasswordService.hash_password(
            request.password
        )

        user = User(
            first_name=request.first_name,
            last_name=request.last_name,
            email=request.email,
            password=hashed_password,
            phone_number=request.phone_number,
            employee_id=request.employee_id,
            is_super_admin=True,
            is_active=True,
        )
        user.roles = [role]

        return AuthRepository.create_super_admin(
            db,
            user,
        )
    


    @staticmethod
    def login(request, db):

        user = AuthRepository.get_user_by_email(
        db,
        request.email,
        )

        if user is None:

            raise HTTPException(
                status_code=401,
                detail="Invalid credentials",
            )

        if not PasswordService.verify_password(
            request.password,
            user.password,
        ):

            raise HTTPException(
                status_code=401,
                detail="Invalid credentials",
            )

        role_ids = [str(r.id) for r in user.roles]
        token = JWTService.create_access_token(
            {
                "user_id": str(user.id),
                "email": user.email,
                "role_id": role_ids[0] if role_ids else "",
                "is_super_admin": user.is_super_admin,
            }
        )

        return {
            "access_token": token,
            "token_type": "Bearer",
            "user": user,
        }