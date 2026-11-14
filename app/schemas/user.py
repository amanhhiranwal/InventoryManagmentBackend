from pydantic import BaseModel, EmailStr


class CreateUserRequest(BaseModel):
    first_name: str
    last_name: str
    email: EmailStr
    #: Optional: left out, the server makes one up and emails it. An
    #: account is unusable until the password has been changed either
    #: way, so what it starts as matters less than who knows it.
    password: str | None = None
    phone_number: str
    employee_id: str
    location: str | None = None
    role_ids: list[str] = []
    company_ids: list[str] = []
    reports_to_id: str | None = None
    #: Masters > Departments. Decides who heads a purchase requisition's
    #: Department Head approval step.
    department_id: int | None = None
    #: Their job title as HR writes it. Shown, never used for access.
    designation: str | None = None
    #: Managers they report to beside reports_to_id - two Directors over
    #: the Founder. Both see their records; approvals follow the main one.
    also_reports_to_ids: list[str] = []


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
    department_id: int | None = None
    #: Each is only changed when the form sends it.
    designation: str | None = None
    also_reports_to_ids: list[str] | None = None


class SetUserActiveRequest(BaseModel):
    is_active: bool
