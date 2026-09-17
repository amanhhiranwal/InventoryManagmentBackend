from typing import Optional

from pydantic import BaseModel


class CreateCustomerTypeRequest(BaseModel):
    name: str
    code: str
    description: Optional[str] = None
