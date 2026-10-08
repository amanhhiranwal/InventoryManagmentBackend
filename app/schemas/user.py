from pydantic import BaseModel, EmailStr


class CreateUserRequest(BaseModel):
    first_name: str
    last_name: str
    email: EmailStr
    password: str
    phone_number: str
    employee_id: str
    location: str | None = None
    role_ids: list[str] = []
    company_ids: list[str] = []
    reports_to_id: str | None = None


class UpdateUserRoleRequest(BaseModel):
    role_ids: list[str] = []
    company_ids: list[str] = []


class UpdateUserRequest(BaseModel):
    first_name: str
    last_name: str
    #: Left out to keep the address as it is. Changing it changes how
    #: somebody signs in, so a form that does not carry the field must
    #: not be read as asking for it to be blanked.
    email: str | None = None
    phone_number: str
    employee_id: str
    location: str | None = None
    role_ids: list[str] = []
    company_ids: list[str] = []
    reports_to_id: str | None = None


class SetUserActiveRequest(BaseModel):
    is_active: bool
