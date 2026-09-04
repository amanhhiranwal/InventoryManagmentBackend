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

        created_user = UserRepository.create(db, user)
        
        # Mirror user record across all microservice databases
        try:
            from sqlalchemy import create_engine, text
            from urllib.parse import quote_plus
            from app.core.config import settings
            db_names = ["solutions", "crm_db", "inventory_db", "sales_db"]
            encoded_pw = quote_plus(settings.POSTGRES_PASSWORD)
            for target_db in db_names:
                try:
                    db_url = f"postgresql+psycopg2://{settings.POSTGRES_USER}:{encoded_pw}@{settings.POSTGRES_HOST}:{settings.POSTGRES_PORT}/{target_db}"
                    t_engine = create_engine(db_url)
                    with t_engine.connect() as conn:
                        exists = conn.execute(text(f"SELECT id FROM users WHERE id = '{created_user.id}';")).fetchone()
                        if not exists:
                            conn.execute(text("""
                                INSERT INTO users (id, first_name, last_name, email, password, phone_number, employee_id, is_super_admin, is_active, created_at, updated_at)
                                VALUES (:id, :first_name, :last_name, :email, :password, :phone_number, :employee_id, :is_super_admin, :is_active, NOW(), NOW())
                            """), {
                                "id": str(created_user.id),
                                "first_name": created_user.first_name,
                                "last_name": created_user.last_name,
                                "email": created_user.email,
                                "password": created_user.password,
                                "phone_number": created_user.phone_number,
                                "employee_id": created_user.employee_id,
                                "is_super_admin": created_user.is_super_admin,
                                "is_active": created_user.is_active,
                            })
                            conn.commit()
                except Exception as inner_e:
                    print(f"User sync error to {target_db}:", inner_e)
        except Exception as e:
            print("User replication error:", e)

        return created_user

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
