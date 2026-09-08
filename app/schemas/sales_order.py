from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel


class SalesOrderItem(BaseModel):
    product_id: Optional[str] = None
    item: Optional[str] = None
    description: Optional[str] = None
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

    total_amount: Optional[float] = 0.0
    discount_amount: Optional[float] = 0.0
    gst_amount: Optional[float] = 0.0
    grand_total: Optional[float] = 0.0

    aging_0_30: Optional[float] = 0.0
    aging_31_60: Optional[float] = 0.0
    aging_61_90: Optional[float] = 0.0
    aging_91_120: Optional[float] = 0.0
    aging_121_180: Optional[float] = 0.0
    aging_above_180: Optional[float] = 0.0

    remarks: Optional[str] = None


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

    aging_0_30: Optional[float] = None
    aging_31_60: Optional[float] = None
    aging_61_90: Optional[float] = None
    aging_91_120: Optional[float] = None
    aging_121_180: Optional[float] = None
    aging_above_180: Optional[float] = None

    remarks: Optional[str] = None


class UpdateSalesOrderStatusRequest(BaseModel):
    status: str
