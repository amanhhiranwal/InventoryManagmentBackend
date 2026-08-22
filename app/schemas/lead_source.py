from pydantic import BaseModel
from typing import Optional

class CreateLeadSourceRequest(BaseModel):
    name: str
    code: Optional[str] = None
    description: Optional[str] = None

class LeadSourceResponse(BaseModel):
    id: str
    name: str
    code: Optional[str] = None
    description: Optional[str] = None
    is_active: bool

    class Config:
        from_attributes = True
