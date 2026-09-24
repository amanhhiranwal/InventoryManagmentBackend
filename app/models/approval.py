from sqlalchemy import JSON, Column, DateTime, Float, Integer, String, func
from sqlalchemy.dialects.postgresql import UUID

from app.database.base import Base


class ApprovalStatus:
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    WITHDRAWN = "WITHDRAWN"

    ALL = [PENDING, APPROVED, REJECTED, WITHDRAWN]


class ApprovalDocument:
    QUOTATION = "QUOTATION"
    SALES_ORDER = "SALES_ORDER"

    ALL = [QUOTATION, SALES_ORDER]


class SalesApproval(Base):
    """One discount approval, travelling up the hierarchy a step at a time.

    The chain is worked out when the request is raised (see
    app/core/approvals) and frozen onto the row, so changing the bands
    later never rewrites a decision someone already made. ``steps`` holds
    one entry per role in that chain, each recording who decided, when and
    why.
    """

    __tablename__ = "sales_approval"

    id = Column(Integer, primary_key=True, autoincrement=True)

    #: "QUOTATION" or "SALES_ORDER", with the id of that record.
    document_type = Column(String(20), nullable=False, index=True)
    document_id = Column(Integer, nullable=False, index=True)
    #: The human reference, e.g. QT-3008 - kept so a withdrawn document's
    #: history still reads.
    document_number = Column(String(50), nullable=True)

    #: What is being approved: the discount off an end customer price, or a
    #: dealer transfer price.
    price_type = Column(String(10), nullable=False, default="ECP")
    discount_percent = Column(Float, nullable=False, default=0.0)
    discount_amount = Column(Float, nullable=True)
    orc_percent = Column(Float, nullable=True)
    orc_amount = Column(Float, nullable=True)
    document_value = Column(Float, nullable=True)

    status = Column(String(20), nullable=False, default=ApprovalStatus.PENDING, index=True)

    #: Which step of ``steps`` is waiting on a decision.
    current_step = Column(Integer, nullable=False, default=0)

    #: [{role, approver_id, approver_name, decision, remarks, decided_at}]
    steps = Column(JSON, nullable=False, default=list)

    requested_by = Column(UUID(as_uuid=True), nullable=True, index=True)
    requested_by_name = Column(String(200), nullable=True)
    requested_at = Column(DateTime, server_default=func.now(), nullable=True)
    decided_at = Column(DateTime, nullable=True)

    remarks = Column(String(2000), nullable=True)

    created_at = Column(DateTime, server_default=func.now(), nullable=True)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=True)
