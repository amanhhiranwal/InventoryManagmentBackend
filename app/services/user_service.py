from fastapi import HTTPException
from sqlalchemy.orm import Session
from uuid import UUID

from app.models.user import User
from app.models.company import Company
from app.repositories.user_repository import UserRepository
from app.repositories.rbac_repository import RBACRepository
from app.services.password_service import PasswordService
from app.utils.validators import validate_uuid

class UserService:

    @staticmethod
    def create(request, db: Session) -> User:
        # Check if email is already taken
        email_exists = UserRepository.get_by_email(db, request.email)
        if email_exists:
            raise HTTPException(
                status_code=400,
                detail="User email already exists.",
            )

        # Check if employee_id is already taken
        employee_exists = UserRepository.get_by_employee_id(db, request.employee_id)
        if employee_exists:
            raise HTTPException(
                status_code=400,
                detail="Employee ID already exists.",
            )

        # Validate roles
        roles_list = []
        for r_id in request.role_ids:
            validate_uuid(r_id, "role_id")
            role = RBACRepository.get_role_by_id(db, r_id)
            if role is None:
                raise HTTPException(
                    status_code=404,
                    detail=f"Role '{r_id}' not found.",
                )
            roles_list.append(role)

        # Validate companies
        companies_list = []
        for c_id in request.company_ids:
            validate_uuid(c_id, "company_id")
            company = db.query(Company).filter(Company.id == UUID(c_id)).first()
            if company is None:
                raise HTTPException(
                    status_code=404,
                    detail=f"Company '{c_id}' not found.",
                )
            companies_list.append(company)

        hashed_password = PasswordService.hash_password(request.password)

        user = User(
            first_name=request.first_name,
            last_name=request.last_name,
            email=request.email,
            password=hashed_password,
            phone_number=request.phone_number,
            employee_id=request.employee_id,
            is_super_admin=False,
            is_active=True,
        )

        user.roles = roles_list
        user.companies = companies_list

        return UserRepository.create(db, user)

    @staticmethod
    def get_all(db: Session, skip: int = 0, limit: int = 100) -> dict:
        return UserRepository.get_all(db, skip, limit)

    @staticmethod
    def update_role(user_id: str, role_ids: list[str], company_ids: list[str], db: Session) -> User:
        validate_uuid(user_id, "user_id")

        user = UserRepository.get_by_id(db, UUID(user_id))
        if user is None:
            raise HTTPException(
                status_code=404,
                detail="User not found.",
            )

        # Validate roles
        roles_list = []
        for r_id in role_ids:
            validate_uuid(r_id, "role_id")
            role = RBACRepository.get_role_by_id(db, r_id)
            if role is None:
                raise HTTPException(
                    status_code=404,
                    detail=f"Role '{r_id}' not found.",
                )
            roles_list.append(role)

        # Validate companies
        companies_list = []
        for c_id in company_ids:
            validate_uuid(c_id, "company_id")
            company = db.query(Company).filter(Company.id == UUID(c_id)).first()
            if company is None:
                raise HTTPException(
                    status_code=404,
                    detail=f"Company '{c_id}' not found.",
                )
            companies_list.append(company)

        user.roles = roles_list
        user.companies = companies_list
        return UserRepository.update(db, user)

    @staticmethod
    def delete(user_id: str, db: Session) -> None:
        validate_uuid(user_id, "user_id")
        user = UserRepository.get_by_id(db, UUID(user_id))
        if user is None:
            raise HTTPException(
                status_code=404,
                detail="User not found.",
            )
        if user.is_super_admin:
            raise HTTPException(
                status_code=400,
                detail="Cannot delete Super Admin.",
            )
        UserRepository.delete(db, user)

    @staticmethod
    def update(user_id: str, request, db: Session) -> User:
        validate_uuid(user_id, "user_id")
        user = UserRepository.get_by_id(db, UUID(user_id))
        if user is None:
            raise HTTPException(
                status_code=404,
                detail="User not found.",
            )
        if request.employee_id != user.employee_id:
            emp_exists = UserRepository.get_by_employee_id(db, request.employee_id)
            if emp_exists:
                raise HTTPException(
                    status_code=400,
                    detail="Employee ID already exists.",
                )
        roles_list = []
        for r_id in request.role_ids:
            validate_uuid(r_id, "role_id")
            role = RBACRepository.get_role_by_id(db, r_id)
            if role is None:
                raise HTTPException(
                    status_code=404,
                    detail=f"Role '{r_id}' not found.",
                )
            roles_list.append(role)
        companies_list = []
        for c_id in request.company_ids:
            validate_uuid(c_id, "company_id")
            company = db.query(Company).filter(Company.id == UUID(c_id)).first()
            if company is None:
                raise HTTPException(
                    status_code=404,
                    detail=f"Company '{c_id}' not found.",
                )
            companies_list.append(company)
        user.first_name = request.first_name
        user.last_name = request.last_name
        user.phone_number = request.phone_number
        user.employee_id = request.employee_id
        user.roles = roles_list
        user.companies = companies_list
        return UserRepository.update(db, user)
