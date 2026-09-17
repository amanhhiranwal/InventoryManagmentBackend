from typing import Optional

from pydantic import BaseModel


class CreateStateRequest(BaseModel):
    name: str
    code: str
    country: Optional[str] = "India"
