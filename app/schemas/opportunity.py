from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel


class CreateOpportunityRequest(BaseModel):
    """Create an opportunity directly, without going through a Lead."""

    title: Optional[str] = None
    description: Optional[str] = None
    status: Optional[str] = None

    lead_id: Optional[int] = None

    deal_value: Optional[float] = 0.0
    priority: Optional[str] = "Medium"
    expected_closing_date: Optional[datetime] = None

    contact_name: Optional[str] = None
    organization_name: Optional[str] = None
    email: Optional[str] = None
    mobile_number: Optional[str] = None
    website: Optional[str] = None
    designation: Optional[str] = None
    office_address: Optional[str] = None
    city: Optional[str] = None
    zip_code: Optional[str] = None
    country: Optional[str] = "India"
    gst_number: Optional[str] = None
    pan_number: Optional[str] = None
    coi_number: Optional[str] = None

    shipping_address: Optional[str] = None
    shipping_city: Optional[str] = None
    shipping_state: Optional[str] = None
    shipping_zip_code: Optional[str] = None
    shipping_country: Optional[str] = None

    requirements: Optional[str] = None
    remarks: Optional[str] = None
    demo_status: Optional[str] = None

    product_items: Optional[List[Dict[str, Any]]] = None

    customer_type_id: Optional[int] = None
    state_id: Optional[int] = None
    assigned_to_id: Optional[str] = None


class UpdateOpportunityRequest(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None

    deal_value: Optional[float] = None
    priority: Optional[str] = None
    expected_closing_date: Optional[datetime] = None

    contact_name: Optional[str] = None
    organization_name: Optional[str] = None
    email: Optional[str] = None
    mobile_number: Optional[str] = None
    website: Optional[str] = None
    designation: Optional[str] = None
    office_address: Optional[str] = None
    city: Optional[str] = None
    zip_code: Optional[str] = None
    country: Optional[str] = None
    gst_number: Optional[str] = None
    pan_number: Optional[str] = None
    coi_number: Optional[str] = None

    shipping_address: Optional[str] = None
    shipping_city: Optional[str] = None
    shipping_state: Optional[str] = None
    shipping_zip_code: Optional[str] = None
    shipping_country: Optional[str] = None

    requirements: Optional[str] = None
    remarks: Optional[str] = None
    demo_status: Optional[str] = None

    product_items: Optional[List[Dict[str, Any]]] = None

    customer_type_id: Optional[int] = None
    state_id: Optional[int] = None
    assigned_to_id: Optional[str] = None


class UpdateOpportunityStatusRequest(BaseModel):
    status: str
    won_reason: Optional[str] = None
    lost_reason: Optional[str] = None


class ConvertLeadRequest(BaseModel):
    """Promote a Lead into an Opportunity.

    Every field is optional: anything omitted falls back to the value already
    held on the originating Lead, so a bare {} still performs a valid
    conversion while the New Opportunity form can override any detail.
    """

    title: Optional[str] = None
    description: Optional[str] = None

    deal_value: Optional[float] = 0.0
    priority: Optional[str] = "Medium"
    expected_closing_date: Optional[datetime] = None

    contact_name: Optional[str] = None
    organization_name: Optional[str] = None
    email: Optional[str] = None
    mobile_number: Optional[str] = None
    website: Optional[str] = None
    designation: Optional[str] = None
    office_address: Optional[str] = None
    city: Optional[str] = None
    zip_code: Optional[str] = None
    country: Optional[str] = None
    gst_number: Optional[str] = None
    pan_number: Optional[str] = None
    coi_number: Optional[str] = None

    shipping_address: Optional[str] = None
    shipping_city: Optional[str] = None
    shipping_state: Optional[str] = None
    shipping_zip_code: Optional[str] = None
    shipping_country: Optional[str] = None

    requirements: Optional[str] = None
    remarks: Optional[str] = None
    demo_status: Optional[str] = None

    product_items: Optional[List[Dict[str, Any]]] = None

    customer_type_id: Optional[int] = None
    state_id: Optional[int] = None
    assigned_to_id: Optional[str] = None
