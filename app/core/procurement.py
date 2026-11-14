"""The rules of the procurement flow, in one place.

Statuses, the approval matrix and who may do what are read from here by
the services and the routes, so a status the frontend shows and the one
the server enforces are the same word.
"""

# ----------------------------------------------------------------------
# The requisition's own life
# ----------------------------------------------------------------------
class PRStatus:
    DRAFT = "DRAFT"
    PENDING_APPROVAL = "PENDING_APPROVAL"
    RETURNED = "RETURNED"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"

    #: Who may edit: the requester, and only before or after a send-back.
    EDITABLE = (DRAFT, RETURNED)


# ----------------------------------------------------------------------
# Where the procurement team has got to with an approved requisition
# ----------------------------------------------------------------------
class ProcurementStatus:
    AWAITING = "AWAITING_PROCUREMENT"
    ASSIGNED = "ASSIGNED"
    IN_PROGRESS = "IN_PROGRESS"
    QUOTATION_COLLECTION = "QUOTATION_COLLECTION"
    QUOTATIONS_RECEIVED = "QUOTATIONS_RECEIVED"
    COMPARISON = "COMPARISON_IN_PROGRESS"
    #: A PO has been raised for some or all lines and waits on its approval.
    PO_APPROVAL = "PO_APPROVAL_PENDING"
    #: Every line is on an approved PO.
    PO_GENERATED = "PO_GENERATED"
    COMPLETED = "COMPLETED"
    ON_HOLD = "ON_HOLD"
    CANCELLED = "CANCELLED"

    ALL = (
        AWAITING, ASSIGNED, IN_PROGRESS, QUOTATION_COLLECTION,
        QUOTATIONS_RECEIVED, COMPARISON, PO_APPROVAL, PO_GENERATED,
        COMPLETED, ON_HOLD, CANCELLED,
    )

    #: Statuses procurement may set by hand; the rest follow from actions.
    MANUAL = (IN_PROGRESS, ON_HOLD, CANCELLED, COMPLETED)

    #: Still being worked on - counted for "overdue".
    OPEN = (
        AWAITING, ASSIGNED, IN_PROGRESS, QUOTATION_COLLECTION,
        QUOTATIONS_RECEIVED, COMPARISON, PO_APPROVAL, ON_HOLD,
    )


PURCHASE_TYPES = {
    "SALES_ORDER": "Customer Sales Order Requirement",
    "STOCK": "General Inventory / Stock Replenishment",
    "OFFICE": "Office / Administrative Requirement",
    "PROJECT": "Project Requirement",
    "PRODUCTION": "Production Requirement",
    "OTHER": "Other",
}

PRIORITIES = ("LOW", "NORMAL", "HIGH", "URGENT")

#: What someone may log by hand on a requisition or PO's activity history.
ACTIVITY_TYPES = ("Note", "Call", "Email", "Meeting", "Follow-up", "Site Visit")

#: The sales reporting chart. Only people holding one of these roles raise
#: a requisition against a sales order - and see only the orders their
#: place in the chart lets them see. Everyone else's requisitions are for
#: stock, office, projects and the like.
SALES_CHART_ROLES = ("Founder", "CEO", "AVP", "Zonal Head", "Area Manager")


# ----------------------------------------------------------------------
# The approval matrix
# ----------------------------------------------------------------------
class ApprovalStep:
    REPORTING_MANAGER = "REPORTING_MANAGER"
    DEPARTMENT_HEAD = "DEPARTMENT_HEAD"
    FINANCE = "FINANCE"
    DIRECTOR = "DIRECTOR"

    LABELS = {
        REPORTING_MANAGER: "Reporting Manager",
        DEPARTMENT_HEAD: "Department Head",
        FINANCE: "Finance",
        DIRECTOR: "Director / CFO",
    }

    #: Steps decided by whoever holds the role of this name.
    ROLE_NAMES = {FINANCE: "Finance", DIRECTOR: "Director"}

    #: Any other role can be a step too, set on Masters > PR Approval
    #: Matrix: "ROLE:CEO" is decided by whoever holds the CEO role. The step
    #: column is 40 characters, so the role name may be up to 35.
    ROLE_PREFIX = "ROLE:"
    MAX_ROLE_NAME = 35

    @classmethod
    def role_of(cls, step: str) -> str | None:
        """The role that decides ``step``, if a role does."""
        if step in cls.ROLE_NAMES:
            return cls.ROLE_NAMES[step]
        if str(step).startswith(cls.ROLE_PREFIX):
            return step[len(cls.ROLE_PREFIX):] or None
        return None

    @classmethod
    def label(cls, step: str) -> str:
        if step in cls.LABELS:
            return cls.LABELS[step]
        return cls.role_of(step) or step


#: The setting the thresholds are kept under (Masters > PR Approval Matrix).
#: The bands are applied to the purchase order, once a vendor's price is
#: known; a requisition itself only needs the requester's manager, who
#: confirms the need.
APPROVAL_SETTING = "pr_approval_matrix"

#: As the requisition form sets it out. ``upto`` is inclusive; the last
#: band has no ceiling.
DEFAULT_APPROVAL_MATRIX = [
    {"upto": 50000, "steps": [ApprovalStep.REPORTING_MANAGER]},
    {
        "upto": 500000,
        "steps": [
            ApprovalStep.REPORTING_MANAGER,
            ApprovalStep.DEPARTMENT_HEAD,
            ApprovalStep.FINANCE,
        ],
    },
    {
        "upto": None,
        "steps": [
            ApprovalStep.REPORTING_MANAGER,
            ApprovalStep.DEPARTMENT_HEAD,
            ApprovalStep.FINANCE,
            ApprovalStep.DIRECTOR,
        ],
    },
]

# ----------------------------------------------------------------------
# Vendors, quotations, selections, orders
# ----------------------------------------------------------------------
class VendorStatus:
    DRAFT = "DRAFT"
    PENDING_VERIFICATION = "PENDING_VERIFICATION"
    APPROVED = "APPROVED"
    ACTIVE = "ACTIVE"
    ON_HOLD = "ON_HOLD"
    BLOCKED = "BLOCKED"
    INACTIVE = "INACTIVE"

    ALL = (DRAFT, PENDING_VERIFICATION, APPROVED, ACTIVE, ON_HOLD, BLOCKED, INACTIVE)

    #: Who a quotation can be recorded against and a PO placed with.
    USABLE = (APPROVED, ACTIVE)


VENDOR_TYPES = ("Manufacturer", "Distributor", "Dealer", "Service Provider", "Contractor", "Other")


class QuotationStatus:
    DRAFT = "DRAFT"
    RECEIVED = "RECEIVED"
    UNDER_REVIEW = "UNDER_REVIEW"
    NEGOTIATION = "NEGOTIATION"
    REVISED = "REVISED"
    FINAL = "FINAL"
    SELECTED = "SELECTED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"

    ALL = (DRAFT, RECEIVED, UNDER_REVIEW, NEGOTIATION, REVISED, FINAL, SELECTED, REJECTED, EXPIRED)

    #: What procurement may set by hand on the latest version.
    MANUAL = (DRAFT, RECEIVED, UNDER_REVIEW, NEGOTIATION, FINAL, REJECTED)


COMPLIANCE = ("COMPLIANT", "PARTIAL", "NON_COMPLIANT")

# ----------------------------------------------------------------------
# Payment terms, as the purchase team writes them
# ----------------------------------------------------------------------
PAYMENT_TERMS = {
    "ADVANCE_100": "100% advance",
    "ADVANCE_DELIVERY": "Advance, balance on delivery",
    "ADVANCE_CREDIT": "Advance, balance after credit days",
    "CREDIT_100": "100% after credit days",
}

#: The day counts offered for each term that has a credit period.
CREDIT_DAY_OPTIONS = {
    "ADVANCE_CREDIT": (15, 30, 45, 60, 90),
    "CREDIT_100": (7, 15, 21, 30, 45, 60, 90),
}

PAYMENT_MODES = {
    "BANK_TRANSFER": "Bank transfer",
    "PDC": "Post-dated cheque",
}


class POStatus:
    DRAFT = "DRAFT"
    PENDING_APPROVAL = "PENDING_APPROVAL"
    APPROVED = "APPROVED"
    SENT = "SENT"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    PARTIALLY_RECEIVED = "PARTIALLY_RECEIVED"
    FULLY_RECEIVED = "FULLY_RECEIVED"
    CLOSED = "CLOSED"
    CANCELLED = "CANCELLED"

    #: The moves people make by hand. Receiving is for goods receipt,
    #: which this module does not do, so no one moves a PO there by hand.
    MOVES = {
        DRAFT: (PENDING_APPROVAL, CANCELLED),
        PENDING_APPROVAL: (APPROVED, DRAFT, CANCELLED),
        APPROVED: (SENT, CANCELLED),
        SENT: (ACKNOWLEDGED, CLOSED, CANCELLED),
        ACKNOWLEDGED: (CLOSED, CANCELLED),
        PARTIALLY_RECEIVED: (CLOSED,),
        FULLY_RECEIVED: (CLOSED,),
    }


# ----------------------------------------------------------------------
# Permissions
# ----------------------------------------------------------------------
#: Raising a requisition is the "My Requisitions" tick on Roles & Access.
#: Every role has it until a super admin takes it away (release setup
#: grants it to each role once, and a new role starts with it). Approving
#: needs no permission: an approver is whoever the chain names.
RAISE_REQUISITION = "requisition.create"

#: The rest cover the procurement team's work, and are granted to nobody
#: by default - a super admin has them all, and Roles & Access hands them out.
PERMISSIONS = {
    RAISE_REQUISITION: "Raise purchase requisitions (every role, unless a super admin removes it)",
    "procurement.read": "See the procurement desk and approved requisitions",
    "procurement.manage": "Work requisitions: raise directly, assign, request and record quotations, compare",
    "vendor.read": "See the vendor master",
    "vendor.manage": "Add and edit vendors",
    "vendor.approve": "Verify, approve, block or deactivate a vendor",
    "purchase_order.read": "See purchase orders",
    "purchase_order.manage": "Create purchase orders, set their payment terms and move them on",
    "purchase_order.pay": "Record payments against approved purchase orders (the accounts team)",
    "procurement_masters.manage": "Keep departments, cost centres and the PR approval matrix",
}
