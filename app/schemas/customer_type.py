from pydantic import BaseModel
from typing import Optional

class CreateCustomerTypeRequest(BaseModel):
    name: str
    code: str
    description: Optional[str] = None
