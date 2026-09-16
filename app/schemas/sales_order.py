from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel


class SalesOrderItem(BaseModel):
    product_id: Optional[str] = None
    item: Optional[str] = None
    description: Optional[str] = None

    #: The product family, the specific model and its SKU, named the same way
    #: the quotation names them so a line carried from one to the other keeps
    #: its shape. Previously only the model reached the order, as
    #: ``description``, which left the Model / Variant column with nothing to
    #: show.
    product: Optional[str] = None
    model: Optional[str] = None
    sku: Optional[str] = None
    rate: Optional[float] = 0.0
    price: Optional[float] = 0.0
    qty: Optional[float] = 0.0
    quantity_case: Optional[float] = 0.0
    quantity_kg_ltr: Optional[float] = 0.0
    discount: Optional[float] = 0.0
    tax_rate: Optional[float] = 0.0
    tax_amount: Optional[float] = 0.0
    line_total: Optional[float] = 0.0


class CreateSalesOrderRequest(BaseModel):
    customer_name: str

    order_number: Optional[str] = None
    opportunity_id: Optional[int] = None
    status: Optional[str] = None

    company_name: Optional[str] = None
    customer_type: Optional[str] = None
    state: Optional[str] = None
    order_date: Optional[datetime] = None

    assigned_to: Optional[str] = None
    sales_executive: Optional[str] = None

    customer_information: Optional[Dict[str, Any]] = None
    billing_address: Optional[Dict[str, Any]] = None
    shipping_address: Optional[Dict[str, Any]] = None

    items: List[SalesOrderItem] = []

    #: Totals are recomputed server-side from the line items; these are
    #: accepted for backward compatibility but never trusted.
    total_amount: Optional[float] = 0.0
    discount_amount: Optional[float] = 0.0
    gst_amount: Optional[float] = 0.0
    grand_total: Optional[float] = 0.0

    #: "AMOUNT" or "PERCENT" - the unit the figure below was typed in.
    discount_mode: Optional[str] = None
    orc_mode: Optional[str] = None
    #: Raw values as entered. A discount here overrides the per-line total.
    discount_input: Optional[float] = None
    orc_input: Optional[float] = None

    freight_charges: Optional[float] = 0.0
    installation_lumpsum: Optional[float] = 0.0
    gst_percent: Optional[float] = 18.0
    advance_received: Optional[float] = 0.0

    aging_0_30: Optional[float] = 0.0
    aging_31_60: Optional[float] = 0.0
    aging_61_90: Optional[float] = 0.0
    aging_91_120: Optional[float] = 0.0
    aging_121_180: Optional[float] = 0.0
    aging_above_180: Optional[float] = 0.0

    remarks: Optional[str] = None

    #: Quotation this order was raised against, and the customer's own
    #: purchase order reference and date.
    quotation_id: Optional[str] = None
    po_number: Optional[str] = None
    po_date: Optional[datetime] = None

    #: Share of the total expected up front. Drives the Advance / Balance
    #: split on the Order Summary.
    advance_percent: Optional[float] = 30.0

    #: Commercial conditions, one string per bullet, and the agreed scope.
    commercial_terms: Optional[List[str]] = None
    technical_notes: Optional[str] = None

    #: Annexures attached to the order: [{name, size, type}].
    attachments: Optional[List[Dict[str, Any]]] = None


class UpdateSalesOrderRequest(BaseModel):
    customer_name: Optional[str] = None

    opportunity_id: Optional[int] = None

    company_name: Optional[str] = None
    customer_type: Optional[str] = None
    state: Optional[str] = None
    order_date: Optional[datetime] = None

    assigned_to: Optional[str] = None
    sales_executive: Optional[str] = None

    customer_information: Optional[Dict[str, Any]] = None
    billing_address: Optional[Dict[str, Any]] = None
    shipping_address: Optional[Dict[str, Any]] = None

    items: Optional[List[SalesOrderItem]] = None

    total_amount: Optional[float] = None
    discount_amount: Optional[float] = None
    gst_amount: Optional[float] = None
    grand_total: Optional[float] = None

    discount_mode: Optional[str] = None
    orc_mode: Optional[str] = None
    discount_input: Optional[float] = None
    orc_input: Optional[float] = None

    freight_charges: Optional[float] = None
    installation_lumpsum: Optional[float] = None
    gst_percent: Optional[float] = None
    advance_received: Optional[float] = None

    aging_0_30: Optional[float] = None
    aging_31_60: Optional[float] = None
    aging_61_90: Optional[float] = None
    aging_91_120: Optional[float] = None
    aging_121_180: Optional[float] = None
    aging_above_180: Optional[float] = None

    remarks: Optional[str] = None

    #: Quotation this order was raised against, and the customer's own
    #: purchase order reference and date.
    quotation_id: Optional[str] = None
    po_number: Optional[str] = None
    po_date: Optional[datetime] = None

    #: Share of the total expected up front. Drives the Advance / Balance
    #: split on the Order Summary.
    advance_percent: Optional[float] = None

    #: Commercial conditions, one string per bullet, and the agreed scope.
    commercial_terms: Optional[List[str]] = None
    technical_notes: Optional[str] = None

    #: Annexures attached to the order: [{name, size, type}].
    attachments: Optional[List[Dict[str, Any]]] = None


class UpdateSalesOrderStatusRequest(BaseModel):
    status: str

    # Free text the user typed when moving the order. Recorded on the
    # activity entry the move writes, so the history explains why.
    remarks: Optional[str] = None


class LogSalesOrderActivityRequest(BaseModel):
    """A single entry from the Log Activity control on the order detail page.

    Both fields are optional on their own: a status with no remarks is a
    plain move, remarks with no status is a note against the order. At least
    one must be supplied, which the service enforces.
    """

    status: Optional[str] = None
    action: Optional[str] = None
    remarks: Optional[str] = None
