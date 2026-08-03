from pydantic import BaseModel, EmailStr


class CreateUserRequest(BaseModel):
    first_name: str
    last_name: str
    email: EmailStr
    password: str
    phone_number: str
    employee_id: str
    role_ids: list[str] = []
    company_ids: list[str] = []


class UpdateUserRoleRequest(BaseModel):
    role_ids: list[str] = []
    company_ids: list[str] = []


class UpdateUserRequest(BaseModel):
    first_name: str
    last_name: str
    phone_number: str
    employee_id: str
    role_ids: list[str] = []
    company_ids: list[str] = []
