from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel

from app.schemas.sales_order import SalesOrderItem


class CreateProformaInvoiceRequest(BaseModel):
    """Raise a proforma invoice against a sales order.

    Only the order is required. Anything left out is copied from it, so the
    Create Proforma Invoice dialog can send just the order id and the form
    can send everything it lets the user change.
    """

    sales_order_id: int

    #: "DRAFT" (Save as Draft) or "GENERATED" (Send For Approval).
    status: Optional[str] = None

    issue_date: Optional[datetime] = None
    due_date: Optional[datetime] = None
    assigned_to: Optional[str] = None

    billing_address: Optional[Dict[str, Any]] = None
    shipping_address: Optional[Dict[str, Any]] = None

    items: Optional[List[SalesOrderItem]] = None

    freight_charges: Optional[float] = None
    installation_lumpsum: Optional[float] = None
    gst_percent: Optional[float] = None
    amount_paid: Optional[float] = None
    advance_percent: Optional[float] = None

    commercial_terms: Optional[List[str]] = None
    technical_notes: Optional[str] = None
    attachments: Optional[List[Dict[str, Any]]] = None


class UpdateProformaInvoiceRequest(BaseModel):
    issue_date: Optional[datetime] = None
    due_date: Optional[datetime] = None
    assigned_to: Optional[str] = None

    billing_address: Optional[Dict[str, Any]] = None
    shipping_address: Optional[Dict[str, Any]] = None

    items: Optional[List[SalesOrderItem]] = None

    freight_charges: Optional[float] = None
    installation_lumpsum: Optional[float] = None
    gst_percent: Optional[float] = None
    amount_paid: Optional[float] = None
    advance_percent: Optional[float] = None

    commercial_terms: Optional[List[str]] = None
    technical_notes: Optional[str] = None
    attachments: Optional[List[Dict[str, Any]]] = None


class UpdateProformaInvoiceStatusRequest(BaseModel):
    status: str
    remarks: Optional[str] = None


class SendProformaInvoiceRequest(BaseModel):
    """Payload behind the Send Proforma Invoice dialog."""

    to: List[str]
    cc: Optional[List[str]] = None
    bcc: Optional[List[str]] = None
    subject: Optional[str] = None
    body: Optional[str] = None
    #: Rich-text body from the editor; becomes the text/html part.
    body_html: Optional[str] = None

    #: Statutory & Operational Clauses checkboxes.
    track_opens: Optional[bool] = False
    alert_on_download: Optional[bool] = False
    attach_gst_audit_trail: Optional[bool] = False
    notify_lead_owner: Optional[bool] = False

    #: Delivers only to the caller and leaves the status alone.
    test_only: Optional[bool] = False


class LogProformaInvoiceActivityRequest(BaseModel):
    """A note, or a status move, from the detail page's Activity History."""

    status: Optional[str] = None
    action: Optional[str] = None
    remarks: Optional[str] = None
