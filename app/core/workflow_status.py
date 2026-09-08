"""Canonical CRM workflow statuses and their allowed transitions.

This module is the single source of truth for every status value used by the
sales workflow. Backend, database and API responses all use these exact
values; the frontend is free to render different display labels, but the
persisted value must always be one of the constants defined here.

Entities marked "defined, not yet wired" have their canonical values recorded
here so the vocabulary stays in one place, but no model consumes them yet.
"""


def _terminal() -> set[str]:
    return set()


# ---------------------------------------------------------------------------
# Lead
# ---------------------------------------------------------------------------
class LeadStatus:
    NEW = "NEW"
    CONTACTED = "CONTACTED"
    QUALIFIED = "QUALIFIED"
    CONVERTED = "CONVERTED"
    LOST = "LOST"

    ALL = [NEW, CONTACTED, QUALIFIED, CONVERTED, LOST]


LEAD_TRANSITIONS: dict[str, set[str]] = {
    LeadStatus.NEW: {LeadStatus.CONTACTED, LeadStatus.LOST},
    LeadStatus.CONTACTED: {LeadStatus.QUALIFIED, LeadStatus.LOST},
    # CONVERTED is reached through the Lead -> Opportunity conversion endpoint.
    LeadStatus.QUALIFIED: {LeadStatus.CONVERTED, LeadStatus.LOST},
    LeadStatus.CONVERTED: _terminal(),
    LeadStatus.LOST: _terminal(),
}


# ---------------------------------------------------------------------------
# Opportunity
# ---------------------------------------------------------------------------
class OpportunityStatus:
    QUALIFICATION = "QUALIFICATION"
    REQUIREMENT = "REQUIREMENT"
    DEMO = "DEMO"
    PROPOSAL = "PROPOSAL"
    NEGOTIATION = "NEGOTIATION"
    WON = "WON"
    LOST = "LOST"

    ALL = [
        QUALIFICATION,
        REQUIREMENT,
        DEMO,
        PROPOSAL,
        NEGOTIATION,
        WON,
        LOST,
    ]

    #: Ordered pipeline stages, excluding the terminal outcomes.
    PIPELINE = [QUALIFICATION, REQUIREMENT, DEMO, PROPOSAL, NEGOTIATION]


OPPORTUNITY_TRANSITIONS: dict[str, set[str]] = {
    OpportunityStatus.QUALIFICATION: {
        OpportunityStatus.REQUIREMENT,
        OpportunityStatus.LOST,
    },
    OpportunityStatus.REQUIREMENT: {
        OpportunityStatus.DEMO,
        OpportunityStatus.LOST,
    },
    OpportunityStatus.DEMO: {
        OpportunityStatus.PROPOSAL,
        OpportunityStatus.LOST,
    },
    OpportunityStatus.PROPOSAL: {
        OpportunityStatus.NEGOTIATION,
        OpportunityStatus.LOST,
    },
    OpportunityStatus.NEGOTIATION: {
        OpportunityStatus.WON,
        OpportunityStatus.LOST,
    },
    OpportunityStatus.WON: _terminal(),
    OpportunityStatus.LOST: _terminal(),
}


# ---------------------------------------------------------------------------
# Sales Order
# ---------------------------------------------------------------------------
class SalesOrderStatus:
    DRAFT = "DRAFT"
    CONFIRMED = "CONFIRMED"
    ON_HOLD = "ON_HOLD"
    RELEASED = "RELEASED"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"

    ALL = [DRAFT, CONFIRMED, ON_HOLD, RELEASED, COMPLETED, CANCELLED]


SALES_ORDER_TRANSITIONS: dict[str, set[str]] = {
    SalesOrderStatus.DRAFT: {
        SalesOrderStatus.CONFIRMED,
        SalesOrderStatus.CANCELLED,
    },
    SalesOrderStatus.CONFIRMED: {
        SalesOrderStatus.RELEASED,
        SalesOrderStatus.ON_HOLD,
        SalesOrderStatus.CANCELLED,
    },
    SalesOrderStatus.RELEASED: {
        SalesOrderStatus.COMPLETED,
        SalesOrderStatus.ON_HOLD,
        SalesOrderStatus.CANCELLED,
    },
    # A held order resumes into whichever state it was working towards.
    SalesOrderStatus.ON_HOLD: {
        SalesOrderStatus.CONFIRMED,
        SalesOrderStatus.RELEASED,
        SalesOrderStatus.CANCELLED,
    },
    SalesOrderStatus.COMPLETED: _terminal(),
    SalesOrderStatus.CANCELLED: _terminal(),
}


# ---------------------------------------------------------------------------
# Downstream workflow entities - defined, not yet wired to a model.
# ---------------------------------------------------------------------------
class QuotationStatus:
    DRAFT = "DRAFT"
    SENT = "SENT"
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"

    ALL = [DRAFT, SENT, ACCEPTED, REJECTED, EXPIRED]


class CustomerPOStatus:
    NOT_REQUIRED = "NOT_REQUIRED"
    PENDING = "PENDING"
    RECEIVED = "RECEIVED"
    UNDER_VERIFICATION = "UNDER_VERIFICATION"
    VERIFIED = "VERIFIED"
    REJECTED = "REJECTED"

    ALL = [
        NOT_REQUIRED,
        PENDING,
        RECEIVED,
        UNDER_VERIFICATION,
        VERIFIED,
        REJECTED,
    ]


class ApprovalRequestStatus:
    NOT_REQUIRED = "NOT_REQUIRED"
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    CORRECTION_REQUIRED = "CORRECTION_REQUIRED"
    CANCELLED = "CANCELLED"

    ALL = [
        NOT_REQUIRED,
        PENDING,
        APPROVED,
        REJECTED,
        CORRECTION_REQUIRED,
        CANCELLED,
    ]


class ProformaInvoiceStatus:
    DRAFT = "DRAFT"
    GENERATED = "GENERATED"
    SENT = "SENT"
    CANCELLED = "CANCELLED"

    ALL = [DRAFT, GENERATED, SENT, CANCELLED]


class AgreementStatus:
    DRAFT = "DRAFT"
    SENT = "SENT"
    UNDER_REVIEW = "UNDER_REVIEW"
    SIGNED = "SIGNED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    CANCELLED = "CANCELLED"

    ALL = [DRAFT, SENT, UNDER_REVIEW, SIGNED, REJECTED, EXPIRED, CANCELLED]


class PaymentStatus:
    PENDING = "PENDING"
    PARTIAL = "PARTIAL"
    PAID = "PAID"
    FAILED = "FAILED"
    REFUNDED = "REFUNDED"
    CANCELLED = "CANCELLED"

    ALL = [PENDING, PARTIAL, PAID, FAILED, REFUNDED, CANCELLED]


class PaymentApprovalStatus:
    """Payment-related approval. Deliberately separate from ApprovalRequest,
    which covers commercial/order approval."""

    NOT_REQUIRED = "NOT_REQUIRED"
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    CORRECTION_REQUIRED = "CORRECTION_REQUIRED"
    CANCELLED = "CANCELLED"

    ALL = [
        NOT_REQUIRED,
        PENDING,
        APPROVED,
        REJECTED,
        CORRECTION_REQUIRED,
        CANCELLED,
    ]


# ---------------------------------------------------------------------------
# Legacy value mapping
# ---------------------------------------------------------------------------
#: Lowercase/legacy lead values that predate the canonical vocabulary.
LEGACY_LEAD_STATUS_MAP: dict[str, str] = {
    "new": LeadStatus.NEW,
    "contacted": LeadStatus.CONTACTED,
    "qualified": LeadStatus.QUALIFIED,
    "converted": LeadStatus.CONVERTED,
    "dead": LeadStatus.LOST,
    "lost": LeadStatus.LOST,
    "active": LeadStatus.NEW,
    "inactive": LeadStatus.LOST,
}

#: Legacy Lead.stage values mapped onto an opportunity status.
LEGACY_STAGE_TO_OPPORTUNITY_STATUS: dict[str, str] = {
    "opportunity": OpportunityStatus.QUALIFICATION,
    "quotation": OpportunityStatus.PROPOSAL,
    "dead": OpportunityStatus.LOST,
}

#: Legacy Mongo sales order status labels mapped onto canonical values.
LEGACY_SALES_ORDER_STATUS_MAP: dict[str, str] = {
    "draft": SalesOrderStatus.DRAFT,
    "pending approval": SalesOrderStatus.DRAFT,
    "confirmed": SalesOrderStatus.CONFIRMED,
    "payment pending": SalesOrderStatus.CONFIRMED,
    "processing": SalesOrderStatus.RELEASED,
    "released": SalesOrderStatus.RELEASED,
    "on hold": SalesOrderStatus.ON_HOLD,
    "on_hold": SalesOrderStatus.ON_HOLD,
    "completed": SalesOrderStatus.COMPLETED,
    "cancelled": SalesOrderStatus.CANCELLED,
    "canceled": SalesOrderStatus.CANCELLED,
}


def normalize_lead_status(value: str | None) -> str:
    """Map any legacy or canonical lead status onto a canonical value."""

    if not value:
        return LeadStatus.NEW

    candidate = str(value).strip()

    if candidate.upper() in LeadStatus.ALL:
        return candidate.upper()

    return LEGACY_LEAD_STATUS_MAP.get(candidate.lower(), LeadStatus.NEW)


def normalize_sales_order_status(value: str | None) -> str:
    """Map any legacy or canonical sales order status onto a canonical value."""

    if not value:
        return SalesOrderStatus.DRAFT

    candidate = str(value).strip()

    if candidate.upper() in SalesOrderStatus.ALL:
        return candidate.upper()

    return LEGACY_SALES_ORDER_STATUS_MAP.get(
        candidate.lower(),
        SalesOrderStatus.DRAFT,
    )


def assert_transition(
    entity: str,
    transitions: dict[str, set[str]],
    current: str,
    target: str,
) -> None:
    """Raise HTTP 400 when ``current -> target`` is not an allowed move.

    Re-saving the same status is always permitted so that partial updates
    which echo the existing status back do not fail.
    """

    from fastapi import HTTPException

    if current == target:
        return

    if target not in transitions.get(current, set()):
        allowed = sorted(transitions.get(current, set()))
        raise HTTPException(
            status_code=400,
            detail=(
                f"Invalid {entity} status transition: {current} -> {target}. "
                f"Allowed from {current}: {', '.join(allowed) or 'none (terminal state)'}."
            ),
        )
