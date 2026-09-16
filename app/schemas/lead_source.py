from typing import Optional

from pydantic import BaseModel


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
