from pydantic import BaseModel
from typing import Any, Optional

class CreateWorkflowRequest(BaseModel):
    name: str
    description: Optional[str] = None
    nodes: list
    edges: list
