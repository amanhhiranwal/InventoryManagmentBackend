from pydantic import BaseModel


class CreateRoleRequest(BaseModel):
    role_name: str
    description: str


class RoleResponse(BaseModel):
    id: str
    role_name: str
    description: str
