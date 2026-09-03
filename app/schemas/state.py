from pydantic import BaseModel
from typing import Optional

class CreateStateRequest(BaseModel):
    name: str
    code: str
    country: Optional[str] = "India"
