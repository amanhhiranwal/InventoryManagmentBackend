from pydantic import BaseModel
from typing import Optional, List, Dict, Any

class CreateLeadRequest(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    status: Optional[str] = "new"
    
    # Structured Fields
    contact_name: Optional[str] = None
    organization_name: Optional[str] = None
    email: Optional[str] = None
    mobile_number: Optional[str] = None
    website: Optional[str] = None
    office_address: Optional[str] = None
    city: Optional[str] = None
    zip_code: Optional[str] = None
    country: Optional[str] = "India"
    gst_number: Optional[str] = None
    pan_number: Optional[str] = None
    coi_number: Optional[str] = None
    designation: Optional[str] = None
    remarks: Optional[str] = None

    # Foreign Keys
    customer_type_id: Optional[int] = None
    state_id: Optional[int] = None
    lead_source_id: Optional[int] = None
    assigned_to_id: Optional[str] = None

class UpdateLeadRequest(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    status: Optional[str] = None
    stage: Optional[str] = None

    # Structured Fields
    contact_name: Optional[str] = None
    organization_name: Optional[str] = None
    email: Optional[str] = None
    mobile_number: Optional[str] = None
    website: Optional[str] = None
    office_address: Optional[str] = None
    city: Optional[str] = None
    zip_code: Optional[str] = None
    country: Optional[str] = None
    gst_number: Optional[str] = None
    pan_number: Optional[str] = None
    coi_number: Optional[str] = None
    designation: Optional[str] = None
    remarks: Optional[str] = None

    # Foreign Keys
    customer_type_id: Optional[int] = None
    state_id: Optional[int] = None
    lead_source_id: Optional[int] = None
    assigned_to_id: Optional[str] = None

class ProgressLeadRequest(BaseModel):
    stage: str
    status: Optional[str] = None
    demo_status: Optional[str] = None
    requirements: Optional[str] = None
    quotation_type: Optional[str] = None
    quotation_items: Optional[List[Dict[str, Any]]] = None

    # Free text the user typed when moving the lead. Recorded on the activity
    # entry the move writes, so the history explains why it happened.
    remarks: Optional[str] = None

class LogLeadActivityRequest(BaseModel):
    """A single entry from the Log Activity form in the Lead Details drawer.

    Both fields are optional on their own: a status with no remarks is a plain
    move, remarks with no status is a note against the lead. At least one must
    be supplied, which the service enforces.
    """

    status: Optional[str] = None
    action: Optional[str] = None
    remarks: Optional[str] = None

class AssignLeadRequest(BaseModel):
    assigned_to_id: str
