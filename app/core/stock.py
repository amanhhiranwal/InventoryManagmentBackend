"""The words the stock ledger uses, in one place.

Products stay in MongoDB (the catalogue, with its per-type fields and
images). How many of each product a company holds, where, and in what
state lives in PostgreSQL: a ledger of every movement, and a balance
per company, warehouse, bin, product (SKU) and stock status that the
ledger keeps up to date in the same transaction.

A product is identified in the ledger by its SKU, which is the catalogue
record's model number - the same key a sales order line already carries.
"""

#: What state a quantity is in. Only UNRESTRICTED stock can be promised.
UNRESTRICTED = "UNRESTRICTED"
QUARANTINE = "QUARANTINE"
REJECTED = "REJECTED"
BLOCKED = "BLOCKED"
IN_TRANSIT = "IN_TRANSIT"
STOCK_STATUSES = (UNRESTRICTED, QUARANTINE, REJECTED, BLOCKED, IN_TRANSIT)

#: Why stock moved. Each posting service writes exactly one of these.
OPENING_BALANCE = "OPENING_BALANCE"
PURCHASE_RECEIPT = "PURCHASE_RECEIPT"
SALES_DISPATCH = "SALES_DISPATCH"
TRANSFER_OUT = "TRANSFER_OUT"
TRANSFER_IN = "TRANSFER_IN"
CUSTOMER_RETURN = "CUSTOMER_RETURN"
VENDOR_RETURN = "VENDOR_RETURN"
STOCK_ADJUSTMENT_IN = "STOCK_ADJUSTMENT_IN"
STOCK_ADJUSTMENT_OUT = "STOCK_ADJUSTMENT_OUT"
REVERSAL = "REVERSAL"
#: Quality check moving received goods out of quarantine: to available,
#: rejected or blocked.
QC_STATUS_TRANSFER = "QC_STATUS_TRANSFER"
MOVEMENT_TYPES = (QC_STATUS_TRANSFER, 
    OPENING_BALANCE, PURCHASE_RECEIPT, SALES_DISPATCH, TRANSFER_OUT, TRANSFER_IN,
    CUSTOMER_RETURN, VENDOR_RETURN, STOCK_ADJUSTMENT_IN, STOCK_ADJUSTMENT_OUT, REVERSAL,
)

#: Where a serial-numbered unit is in its life.
SERIAL_IN_STOCK = "IN_STOCK"
SERIAL_QUARANTINE = "QUARANTINE"
SERIAL_REJECTED = "REJECTED"
SERIAL_RESERVED = "RESERVED"
SERIAL_PICKED = "PICKED"
SERIAL_DISPATCHED = "DISPATCHED"
SERIAL_IN_TRANSIT = "IN_TRANSIT"

#: A stock document's life. Nothing moves until it is POSTED.
DRAFT = "DRAFT"
SUBMITTED = "SUBMITTED"
POSTED = "POSTED"
REJECTED_DOC = "REJECTED"
DOCUMENT_STATUSES = (DRAFT, SUBMITTED, POSTED, REJECTED_DOC)

#: Kinds of stock document. All share the draft -> submit -> approve by
#: someone else -> post flow.
OPENING = "OPENING"
ADJUSTMENT_IN = "ADJUSTMENT_IN"
ADJUSTMENT_OUT = "ADJUSTMENT_OUT"
VENDOR_RETURN_DOC = "VENDOR_RETURN"
DOCUMENT_TYPES = (OPENING, ADJUSTMENT_IN, ADJUSTMENT_OUT, VENDOR_RETURN_DOC)

#: Number prefix per document type: COA-OPN-2026-00001.
DOCUMENT_PREFIX = {OPENING: "OPN", ADJUSTMENT_IN: "ADJ", ADJUSTMENT_OUT: "ADJ", VENDOR_RETURN_DOC: "VRT"}

#: Stock out of the warehouse (negative) or into it (positive).
DOCUMENT_SIGN = {OPENING: 1, ADJUSTMENT_IN: 1, ADJUSTMENT_OUT: -1, VENDOR_RETURN_DOC: -1}

#: Why stock was adjusted. Required on every adjustment.
ADJUSTMENT_REASONS = ("DAMAGE", "LOSS", "THEFT", "FOUND", "EXPIRY", "DATA_CORRECTION", "COUNT_VARIANCE", "OTHER")

#: An adjustment worth at least this much also needs Finance's tick
#: (AppSetting "adjustment_finance_threshold").
ADJUSTMENT_FINANCE_SETTING = "adjustment_finance_threshold"
ADJUSTMENT_FINANCE_DEFAULT = 100000

#: permission -> description, for Roles & Access. Granted to no role by
#: default: a super admin has them all and hands them out.
VIEW_STOCK = "stock.read"
CREATE_OPENING = "stock.opening.create"
APPROVE_OPENING = "stock.opening.approve"
PERMISSIONS = {
    VIEW_STOCK: "See warehouse stock and the stock ledger for the warehouses you work in",
    CREATE_OPENING: "Prepare opening stock for a warehouse and send it for approval",
    APPROVE_OPENING: "Approve opening stock, which posts it to the stock ledger",
}

# ----------------------------------------------------------------------
# Goods receipt (GRN)
# ----------------------------------------------------------------------
#: A GRN's life, as the Stock-In screen shows it. Stock moves at POSTED.
GRN_DRAFT = "DRAFT"
#: Sent in by the storekeeper; still no stock until the receipt is posted.
GRN_SUBMITTED = "SUBMITTED"
#: Receipt posted: the goods are in stock, in QUARANTINE, awaiting QC.
GRN_QC_PENDING = "QC_PENDING"
GRN_POSTED = "POSTED"
GRN_CANCELLED = "CANCELLED"
GRN_STATUSES = (GRN_DRAFT, GRN_SUBMITTED, GRN_QC_PENDING, GRN_POSTED, GRN_CANCELLED)

#: What came in. A purchase is received against a PO; the sheet also
#: records goods coming back from a customer or dealer, and goods that
#: arrived with no documents at all.
GRN_PURCHASE = "PURCHASE"
GRN_RETURN = "RETURN"
GRN_OTHER = "OTHER"
GRN_TYPES = (GRN_PURCHASE, GRN_RETURN, GRN_OTHER)

VIEW_GRN = "grn.read"
CREATE_GRN = "grn.create"
POST_GRN = "grn.post"
APPROVE_GRN = "grn.approve"
PERMISSIONS.update({
    VIEW_GRN: "See goods receipts (GRNs) for the warehouses you work in",
    CREATE_GRN: "Record goods received at a warehouse and send the GRN in",
    POST_GRN: "Post a GRN's receipt: the goods enter stock in quarantine and the PO's received quantity moves",
    APPROVE_GRN: "Quality check: accept, reject or mark damaged, which releases accepted goods to stock",
})

# ----------------------------------------------------------------------
# Stock out: reservations and dispatch orders
# ----------------------------------------------------------------------
#: While off, the existing flow still counts stock out of the MongoDB
#: catalogue at READY. Turned on (after migrate_stock_to_ledger.py) the
#: dispatch order is the only stock-out, through the ledger.
LEDGER_LIVE_SETTING = "stock_ledger_live"

RES_ACTIVE = "ACTIVE"
RES_RELEASED = "RELEASED"
RES_CONSUMED = "CONSUMED"

DO_DRAFT = "DRAFT"
DO_PICKED = "PICKED"
DO_PACKED = "PACKED"
DO_DISPATCHED = "DISPATCHED"
DO_DELIVERED = "DELIVERED"
DO_CANCELLED = "CANCELLED"
DO_STATUSES = (DO_DRAFT, DO_PICKED, DO_PACKED, DO_DISPATCHED, DO_DELIVERED, DO_CANCELLED)

#: Lines on a sales order that are not goods: warranty terms and the AI add-on.
NON_STOCK_SKU_PREFIXES = ("WARRANTY:", "ADDON:")

#: An e-way bill is needed above this consignment value unless a company
#: setting says otherwise (AppSetting "eway_bill_threshold").
EWAY_BILL_SETTING = "eway_bill_threshold"
EWAY_BILL_DEFAULT = 50000

VIEW_DISPATCH = "dispatch.read"
MANAGE_DISPATCH = "dispatch.manage"
COMMIT_DISPATCH = "dispatch.commit"
RESERVE_STOCK = "stock.reserve"
PERMISSIONS.update({
    VIEW_DISPATCH: "See dispatch orders and sales order stock availability for the warehouses you work in",
    MANAGE_DISPATCH: "Create dispatch orders, pick and pack, record delivery documents and proof of delivery",
    COMMIT_DISPATCH: "Commit a dispatch: the goods have left the warehouse and stock goes out",
    RESERVE_STOCK: "Reserve available stock for a sales order, and release it",
})

# ----------------------------------------------------------------------
# Adjustments and vendor returns
# ----------------------------------------------------------------------
CREATE_ADJUSTMENT = "stock.adjust.create"
APPROVE_ADJUSTMENT = "stock.adjust.approve"
FINANCE_ADJUSTMENT = "stock.adjust.finance"
CREATE_VENDOR_RETURN = "vendor_return.create"
APPROVE_VENDOR_RETURN = "vendor_return.approve"
PERMISSIONS.update({
    CREATE_ADJUSTMENT: "Request a stock adjustment (damage, loss, found, correction) with a reason",
    APPROVE_ADJUSTMENT: "Approve a stock adjustment, which posts it to the ledger",
    FINANCE_ADJUSTMENT: "Approve stock adjustments above the finance value limit",
    CREATE_VENDOR_RETURN: "Prepare a return of received goods to the vendor, against its GRN",
    APPROVE_VENDOR_RETURN: "Approve a vendor return, which takes the goods out of stock",
})

#: Which permissions draft and approve each kind of stock document.
DOCUMENT_PERMISSIONS = {
    OPENING: (CREATE_OPENING, APPROVE_OPENING),
    ADJUSTMENT_IN: (CREATE_ADJUSTMENT, APPROVE_ADJUSTMENT),
    ADJUSTMENT_OUT: (CREATE_ADJUSTMENT, APPROVE_ADJUSTMENT),
    VENDOR_RETURN_DOC: (CREATE_VENDOR_RETURN, APPROVE_VENDOR_RETURN),
}

# ----------------------------------------------------------------------
# Warehouse transfers (same company only)
# ----------------------------------------------------------------------
#: DRAFT -> SUBMITTED -> APPROVED -> SHIPPED (stock leaves the sending
#: warehouse and is IN_TRANSIT at the receiving one) -> RECEIVED. Cancel
#: is possible until it ships; after that the goods are on the road.
TRF_DRAFT = "DRAFT"
TRF_SUBMITTED = "SUBMITTED"
TRF_APPROVED = "APPROVED"
TRF_SHIPPED = "SHIPPED"
TRF_RECEIVED = "RECEIVED"
TRF_CANCELLED = "CANCELLED"
TRANSFER_STATUSES = (TRF_DRAFT, TRF_SUBMITTED, TRF_APPROVED, TRF_SHIPPED, TRF_RECEIVED, TRF_CANCELLED)

CREATE_TRANSFER = "transfer.create"
APPROVE_TRANSFER = "transfer.approve"
SHIP_TRANSFER = "transfer.ship"
RECEIVE_TRANSFER = "transfer.receive"
PERMISSIONS.update({
    CREATE_TRANSFER: "Request a stock transfer between two warehouses of the same company",
    APPROVE_TRANSFER: "Approve a stock transfer request",
    SHIP_TRANSFER: "Ship an approved transfer: stock leaves the sending warehouse and is in transit",
    RECEIVE_TRANSFER: "Receive a transfer at the receiving warehouse, recording shortage and damage",
})

# ----------------------------------------------------------------------
# Cycle counts
# ----------------------------------------------------------------------
#: OPEN (system quantities snapshotted, counting) -> SUBMITTED -> POSTED
#: (variances posted as adjustments) or CANCELLED.
COUNT_OPEN = "OPEN"
COUNT_SUBMITTED = "SUBMITTED"
COUNT_POSTED = "POSTED"
COUNT_CANCELLED = "CANCELLED"
COUNT_STATUSES = (COUNT_OPEN, COUNT_SUBMITTED, COUNT_POSTED, COUNT_CANCELLED)

CREATE_COUNT = "stock.count.create"
APPROVE_COUNT = "stock.count.approve"
PERMISSIONS.update({
    CREATE_COUNT: "Start a stock count for a warehouse and enter the counted quantities",
    APPROVE_COUNT: "Approve a stock count, which posts the differences as adjustments",
})
