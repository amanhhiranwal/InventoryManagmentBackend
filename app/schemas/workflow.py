from typing import Optional

from pydantic import BaseModel


class CreateWorkflowRequest(BaseModel):
    name: str
    description: Optional[str] = None
    nodes: list
    edges: list
