from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel


class QuotationItem(BaseModel):
    """One priced line on the quotation."""

    product: Optional[str] = None
    model: Optional[str] = None
    sku: Optional[str] = None
    quantity: Optional[float] = 1
    unit_price: Optional[float] = 0.0
    discount: Optional[float] = 0.0
    tax: Optional[float] = 18.0


class CreateQuotationRequest(BaseModel):
    """Create a quotation, normally against an existing Opportunity.

    Totals are all optional: when omitted the service recomputes them from
    the line items, so the stored figures can never disagree with the lines.
    """

    opportunity_id: Optional[int] = None
    status: Optional[str] = None

    opportunity_name: Optional[str] = None
    organization_name: Optional[str] = None
    contact_name: Optional[str] = None
    designation: Optional[str] = None
    email: Optional[str] = None
    mobile_number: Optional[str] = None
    customer_type: Optional[str] = None

    quotation_date: Optional[datetime] = None
    validation_date: Optional[datetime] = None

    billing_address: Optional[Dict[str, Any]] = None
    shipping_address: Optional[Dict[str, Any]] = None
    shipping_same_as_billing: Optional[bool] = False

    items: Optional[List[QuotationItem]] = None

    orc_percent: Optional[float] = 0.0
    orc_amount: Optional[float] = 0.0

    #: "AMOUNT" or "PERCENT" - the unit the figure below was typed in.
    discount_mode: Optional[str] = None
    orc_mode: Optional[str] = None
    #: Raw values as entered. A discount here overrides the per-line total.
    discount_input: Optional[float] = None
    orc_input: Optional[float] = None
    freight_charges: Optional[float] = 0.0
    installation_lumpsum: Optional[float] = 0.0
    gst_percent: Optional[float] = 18.0
    advance_percent: Optional[float] = 30.0

    attachments: Optional[List[Dict[str, Any]]] = None
    terms: Optional[List[Dict[str, Any]]] = None
    remarks: Optional[str] = None

    customer_type_id: Optional[int] = None
    state_id: Optional[int] = None
    assigned_to_id: Optional[str] = None


class UpdateQuotationRequest(BaseModel):
    opportunity_name: Optional[str] = None
    organization_name: Optional[str] = None
    contact_name: Optional[str] = None
    designation: Optional[str] = None
    email: Optional[str] = None
    mobile_number: Optional[str] = None
    customer_type: Optional[str] = None

    quotation_date: Optional[datetime] = None
    validation_date: Optional[datetime] = None

    billing_address: Optional[Dict[str, Any]] = None
    shipping_address: Optional[Dict[str, Any]] = None
    shipping_same_as_billing: Optional[bool] = None

    items: Optional[List[QuotationItem]] = None

    orc_percent: Optional[float] = None
    orc_amount: Optional[float] = None

    discount_mode: Optional[str] = None
    orc_mode: Optional[str] = None
    discount_input: Optional[float] = None
    orc_input: Optional[float] = None
    freight_charges: Optional[float] = None
    installation_lumpsum: Optional[float] = None
    gst_percent: Optional[float] = None
    advance_percent: Optional[float] = None

    attachments: Optional[List[Dict[str, Any]]] = None
    terms: Optional[List[Dict[str, Any]]] = None
    remarks: Optional[str] = None

    customer_type_id: Optional[int] = None
    state_id: Optional[int] = None
    assigned_to_id: Optional[str] = None


class UpdateQuotationStatusRequest(BaseModel):
    status: str
    rejected_reason: Optional[str] = None


class SendQuotationRequest(BaseModel):
    """Payload behind the Send Quotation to Client dialog."""

    to: List[str]
    cc: Optional[List[str]] = None
    bcc: Optional[List[str]] = None
    subject: Optional[str] = None
    #: Plain-text body, used for the text/plain part of the message.
    body: Optional[str] = None
    #: Rich-text body from the editor. When given it becomes the text/html
    #: part verbatim, so bold/italic/underline survive into the mail client.
    body_html: Optional[str] = None

    #: Statutory & Operational Clauses checkboxes.
    track_opens: Optional[bool] = False
    alert_on_download: Optional[bool] = False
    attach_gst_audit_trail: Optional[bool] = False
    notify_lead_owner: Optional[bool] = False

    #: Send Test Email To Self - delivers to the caller without moving the
    #: quotation to SENT.
    test_only: Optional[bool] = False
