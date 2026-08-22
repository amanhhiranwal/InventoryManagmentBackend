from pydantic import BaseModel
from typing import Optional, List, Dict, Any

class CreateLeadRequest(BaseModel):
    title: str
    description: Optional[str] = None
    status: Optional[str] = "new"
    assigned_to_id: Optional[str] = None

class ProgressLeadRequest(BaseModel):
    stage: str
    status: Optional[str] = None
    demo_status: Optional[str] = None
    requirements: Optional[str] = None
    quotation_type: Optional[str] = None
    quotation_items: Optional[List[Dict[str, Any]]] = None

class AssignLeadRequest(BaseModel):
    assigned_to_id: str

