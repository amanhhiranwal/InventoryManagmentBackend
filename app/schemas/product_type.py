from pydantic import BaseModel
from typing import Optional

class CreateProductTypeRequest(BaseModel):
    name: str
    code: str
    category: str
    description: Optional[str] = None
