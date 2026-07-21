from pydantic import BaseModel
from typing import Optional, List, Dict, Any

class CreateLeadRequest(BaseModel):
    title: str
    description: Optional[str] = None
    status: Optional[str] = "new"

class ProgressLeadRequest(BaseModel):
    stage: str
    status: Optional[str] = None
    demo_status: Optional[str] = None
    requirements: Optional[str] = None
    quotation_type: Optional[str] = None
    quotation_items: Optional[List[Dict[str, Any]]] = None
