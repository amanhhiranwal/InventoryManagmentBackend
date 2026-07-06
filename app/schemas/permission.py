from pydantic import BaseModel


class CreatePermissionRequest(BaseModel):
    permission_name: str
    module: str
    description: str | None = None


class PermissionResponse(BaseModel):
    id: str
    permission_name: str
