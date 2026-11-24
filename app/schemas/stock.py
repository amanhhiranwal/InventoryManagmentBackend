from datetime import date, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class DocumentLine(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    #: Required except on a vendor return, which takes it from the GRN line.
    sku: str | None = Field(default=None, max_length=100)
    #: Left out: the warehouse's main store bin.
    bin_id: UUID | None = None
    quantity: Decimal = Field(..., gt=0, max_digits=14, decimal_places=3)
    unit_cost: Decimal | None = Field(default=None, ge=0, max_digits=14, decimal_places=2)
    serials: list[str] = Field(default_factory=list, max_length=5000)
    #: Vendor returns: the GRN line and which of its goods go back.
    grn_line_id: int | None = None
    stock_status: Literal["UNRESTRICTED", "REJECTED", "BLOCKED"] | None = None


class DocumentUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    movement_date: date | None = None
    reason: str | None = Field(default=None, max_length=300)
    remarks: str | None = Field(default=None, max_length=2000)
    #: Adjustments: why (DAMAGE, LOSS, FOUND ...).
    reason_code: str | None = Field(default=None, max_length=30)
    #: Vendor returns: the GRN the goods came in on.
    grn_id: int | None = None
    lines: list[DocumentLine] = Field(..., min_length=1, max_length=500)


class DocumentCreate(DocumentUpdate):
    warehouse_id: UUID
    #: Adjustments: IN (found, correction) or OUT (damage, loss).
    direction: Literal["IN", "OUT"] | None = None


class RejectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    reason: str = Field(..., min_length=1, max_length=1000)


class GrnLine(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    po_item_id: int | None = None
    #: A customer return: the dispatch line the goods went out on.
    dispatch_line_id: int | None = None
    sku: str = Field(..., min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=300)
    uom: str | None = Field(default=None, max_length=30)
    qty_ordered: Decimal | None = Field(default=None, ge=0, max_digits=14, decimal_places=3)
    qty_received: Decimal = Field(..., gt=0, max_digits=14, decimal_places=3)
    unit_cost: Decimal | None = Field(default=None, ge=0, max_digits=14, decimal_places=2)
    remarks: str | None = Field(default=None, max_length=500)


class GrnSave(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    warehouse_id: UUID
    receipt_type: Literal["PURCHASE", "RETURN", "OTHER"] = "PURCHASE"
    purchase_order_id: int | None = None
    #: A customer return: the dispatch the goods went out on.
    dispatch_order_id: int | None = None
    supplier_name: str | None = Field(default=None, max_length=200)
    supplier_address: str | None = Field(default=None, max_length=600)
    delivery_note_no: str | None = Field(default=None, max_length=120)
    delivery_note_date: date | None = None
    receipt_date: date | None = None
    transport_remarks: str | None = Field(default=None, max_length=300)
    remarks: str | None = Field(default=None, max_length=2000)
    lines: list[GrnLine] = Field(..., min_length=1, max_length=300)


class GrnDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    line_id: int
    qty_accepted: Decimal = Field(..., ge=0, max_digits=14, decimal_places=3)
    qty_rejected: Decimal = Field(..., ge=0, max_digits=14, decimal_places=3)
    qty_damaged: Decimal | None = Field(default=None, ge=0, max_digits=14, decimal_places=3)
    #: Where accepted goods go; left out, the main store bin.
    bin_id: UUID | None = None
    serials: list[str] = Field(default_factory=list, max_length=5000)
    remarks: str | None = Field(default=None, max_length=500)


class GrnInspect(BaseModel):
    model_config = ConfigDict(extra="forbid")

    lines: list[GrnDecision] = Field(..., min_length=1, max_length=300)


class CancelRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    reason: str = Field(..., min_length=1, max_length=1000)


class ReserveLine(BaseModel):
    model_config = ConfigDict(extra="forbid")

    so_line_id: int
    qty: Decimal = Field(..., gt=0, max_digits=14, decimal_places=3)


class ReserveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    warehouse_id: UUID
    lines: list[ReserveLine] = Field(..., min_length=1, max_length=300)


class ReleaseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    so_line_id: int | None = None
    reason: str = Field(..., min_length=1, max_length=300)


class DispatchCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    sales_order_id: int
    dispatch_date: date | None = None
    ship_to: str | None = Field(default=None, max_length=1000)
    remarks: str | None = Field(default=None, max_length=2000)
    lines: list[ReserveLine] = Field(..., min_length=1, max_length=300)


class PickLine(BaseModel):
    model_config = ConfigDict(extra="forbid")

    line_id: int
    serials: list[str] = Field(default_factory=list, max_length=5000)


class PickRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    lines: list[PickLine] = Field(..., min_length=1, max_length=300)


class PackRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    packages: int = Field(..., ge=1, le=100000)
    gross_weight: str | None = Field(default=None, max_length=40)


class DispatchDocuments(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    transporter: str | None = Field(default=None, max_length=200)
    vehicle_number: str | None = Field(default=None, max_length=30)
    driver_name: str | None = Field(default=None, max_length=120)
    docket_no: str | None = Field(default=None, max_length=80)
    challan_no: str | None = Field(default=None, max_length=80)
    challan_date: date | None = None
    invoice_no: str | None = Field(default=None, max_length=80)
    eway_bill_no: str | None = Field(default=None, max_length=40)
    eway_bill_date: date | None = None
    eway_valid_until: date | None = None
    gate_pass_no: str | None = Field(default=None, max_length=80)
    consignment_value: Decimal | None = Field(default=None, ge=0, max_digits=14, decimal_places=2)
    ship_to: str | None = Field(default=None, max_length=1000)
    remarks: str | None = Field(default=None, max_length=2000)


class PodRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    receiver: str = Field(..., min_length=1, max_length=200)
    delivered_at: datetime | None = None
    remarks: str | None = Field(default=None, max_length=2000)


# ----------------------------------------------------------------------
# Transfers
# ----------------------------------------------------------------------
class TransferLine(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    sku: str = Field(..., min_length=1, max_length=100)
    #: The sending store bin; left out, the main store.
    bin_id: UUID | None = None
    quantity: Decimal = Field(..., gt=0, max_digits=14, decimal_places=3)
    serials: list[str] = Field(default_factory=list, max_length=5000)
    remarks: str | None = Field(default=None, max_length=500)


class TransferUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    to_warehouse_id: UUID | None = None
    transfer_date: date | None = None
    reason: str | None = Field(default=None, max_length=300)
    remarks: str | None = Field(default=None, max_length=2000)
    lines: list[TransferLine] = Field(..., min_length=1, max_length=300)


class TransferCreate(TransferUpdate):
    from_warehouse_id: UUID
    to_warehouse_id: UUID


class TransferShip(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    vehicle_number: str | None = Field(default=None, max_length=30)
    docket_no: str | None = Field(default=None, max_length=80)
    challan_no: str | None = Field(default=None, max_length=80)


class TransferReceiveLine(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    line_id: int
    qty_received: Decimal = Field(..., ge=0, max_digits=14, decimal_places=3)
    qty_damaged: Decimal | None = Field(default=None, ge=0, max_digits=14, decimal_places=3)
    #: The receiving store bin; left out, the main store.
    bin_id: UUID | None = None
    serials_received: list[str] = Field(default_factory=list, max_length=5000)
    serials_damaged: list[str] = Field(default_factory=list, max_length=5000)
    remarks: str | None = Field(default=None, max_length=500)


class TransferReceive(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    lines: list[TransferReceiveLine] = Field(..., min_length=1, max_length=300)
    remarks: str | None = Field(default=None, max_length=2000)


# ----------------------------------------------------------------------
# Cycle counts
# ----------------------------------------------------------------------
class CountCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    warehouse_id: UUID
    #: One store bin; left out, every store bin.
    bin_id: UUID | None = None
    count_date: date | None = None
    remarks: str | None = Field(default=None, max_length=2000)


class CountEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    line_id: int
    qty_counted: Decimal = Field(..., ge=0, max_digits=14, decimal_places=3)
    remarks: str | None = Field(default=None, max_length=500)


class CountFound(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    sku: str = Field(..., min_length=1, max_length=100)
    bin_id: UUID | None = None
    qty_counted: Decimal = Field(..., ge=0, max_digits=14, decimal_places=3)
    remarks: str | None = Field(default=None, max_length=500)


class CountRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    lines: list[CountEntry] = Field(default_factory=list, max_length=2000)
    found: list[CountFound] = Field(default_factory=list, max_length=300)
