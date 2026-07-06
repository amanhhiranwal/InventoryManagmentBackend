from pydantic import BaseModel


class CreatePermissionRequest(BaseModel):
    permission_name: str
    module: str
    description: str | None = None


class PermissionResponse(BaseModel):
    id: int
    permission_name: str
    module: str
    description: str | None = None

    class Config:
        from_attributes = True


class CreateRoleRequest(BaseModel):
    role_name: str
    description: str | None = None


class RoleResponse(BaseModel):
    id: int
    role_name: str
    description: str | None = None

    class Config:
        from_attributes = True