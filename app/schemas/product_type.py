from typing import Optional

from pydantic import BaseModel


class CreateProductTypeRequest(BaseModel):
    name: str
    code: str
    category: str
    description: Optional[str] = None
