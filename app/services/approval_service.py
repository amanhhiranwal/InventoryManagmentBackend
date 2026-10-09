"""Raising and deciding discount approvals.

A salesperson applies a discount and sends it up; the chain from
app/core/approvals decides who has to sign, and each approval moves it one
step. Everyone in the requester's reporting line is emailed at every step,
so a deal never stalls quietly in somebody's inbox.

The document is only released once the last step approves.
"""

import logging
from datetime import datetime
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.core.approvals import PriceType, approval_chain, describe_chain
from app.models.approval import ApprovalDocument, ApprovalStatus, SalesApproval
from app.models.user import User
from app.core.config import settings
from app.services.email_service import EmailService
from app.services.email_templates import EmailLetter, inr
from app.services.hierarchy_service import HierarchyService
from app.services.notification_service import notify_users

logger = logging.getLogger(__name__)

#: Where each document lives in the UI, for the link in the email.
DOCUMENT_LINKS = {
    ApprovalDocument.QUOTATION: "/sales/quotations/{id}",
    ApprovalDocument.SALES_ORDER: "/sales/orders/{id}",
}

#: Lives on the company profile service now, beside the field it reads,
#: so every email agrees on where the CRM answers. Re-exported here
#: because this is where the rest of the code already imports it from.
from app.services.company_profile_service import app_url  # noqa: E402,F401


DOCUMENT_LABELS = {
    # What a person reads. The document type itself stays QUOTATION.
    ApprovalDocument.QUOTATION: "Proposal",
    ApprovalDocument.SALES_ORDER: "Sales order",
}


def _name(user: User | None) -> str:
    if user is None:
        return "Someone"
    return f"{user.first_name or ''} {user.last_name or ''}".strip() or user.email


def _role_names(user: User) -> set[str]:
    return {role.role_name for role in user.roles}


class ApprovalService:

    # ------------------------------------------------------------------
    # Raising
    # ------------------------------------------------------------------
    @staticmethod
    def open_for(document_type: str, document_id: int, db: Session) -> SalesApproval | None:
        """The approval still waiting on a decision for this document."""

        return (
            db.query(SalesApproval)
            .filter(
                SalesApproval.document_type == document_type,
                SalesApproval.document_id == document_id,
                SalesApproval.status == ApprovalStatus.PENDING,
            )
            .order_by(SalesApproval.id.desc())
            .first()
        )

    @staticmethod
    def is_approved(document_type: str, document_id: int, db: Session) -> bool:
        """Whether this document has a signature on it.

        Asked before a proposal is emailed. "Nothing is pending" is not the
        same question: a draft nobody ever sent up has nothing pending
        either, and under the rule that every proposal carries the CEO's
        signature that one must not go out.
        """

        return (
            db.query(SalesApproval.id)
            .filter(
                SalesApproval.document_type == document_type,
                SalesApproval.document_id == document_id,
                SalesApproval.status == ApprovalStatus.APPROVED,
            )
            .first()
            is not None
        )

    @staticmethod
    def latest_for(document_type: str, document_id: int, db: Session) -> SalesApproval | None:
        return (
            db.query(SalesApproval)
            .filter(
                SalesApproval.document_type == document_type,
                SalesApproval.document_id == document_id,
            )
            .order_by(SalesApproval.id.desc())
            .first()
        )

    @staticmethod
    def _price_type_for_document(
        document_type: str,
        document_id: int,
        db: Session,
    ) -> str | None:
        """ECP or DTP, from the customer type on the document itself.

        Returns None when the document cannot be read, in which case the
        caller's own value stands - a lookup that fails must not stop a
        proposal going up for approval.
        """

        from app.core.approvals import price_type_for
        from app.models.quotation import Quotation
        from app.models.sales_order import SalesOrder

        model = {
            ApprovalDocument.QUOTATION: Quotation,
            ApprovalDocument.SALES_ORDER: SalesOrder,
        }.get(document_type)

        if model is None:
            return None

        try:
            row = db.query(model).filter(model.id == document_id).first()
        except Exception:  # noqa: BLE001 - never block on a lookup
            return None

        if row is None:
            return None

        return price_type_for(getattr(row, "customer_type", None))

    @staticmethod
    def _deepest_discount(document_type: str, document_id: int, db: Session) -> float | None:
        """The largest discount given anywhere on the document.

        The bands say who owns how much discount, and a 25% cut on one
        line is a 25% discount however small that line is. The form was
        sending the blended figure - the discount as a share of the whole
        subtotal - so 10% off one of two panels arrived as 5.07%, and a
        deep cut on a cheap accessory could land in a band below the one
        that owns it, or skip the chain altogether.

        Returns None when the document cannot be read, in which case the
        caller's own figure stands.
        """

        from app.models.quotation import Quotation
        from app.models.sales_order import SalesOrder

        model = {
            ApprovalDocument.QUOTATION: Quotation,
            ApprovalDocument.SALES_ORDER: SalesOrder,
        }.get(document_type)

        if model is None:
            return None

        try:
            row = db.query(model).filter(model.id == document_id).first()
        except Exception:  # noqa: BLE001 - never block on a lookup
            return None

        if row is None:
            return None

        deepest = 0.0

        for line in (row.items or []):
            if not isinstance(line, dict):
                continue

            try:
                deepest = max(deepest, float(line.get("discount") or 0))
            except (TypeError, ValueError):
                continue

        return deepest

    @staticmethod
    def request(
        document_type: str,
        document_id: int,
        *,
        document_number: str | None,
        price_type: str,
        discount_percent: float,
        discount_amount: float | None,
        orc_percent: float | None,
        orc_amount: float | None,
        document_value: float | None,
        remarks: str | None,
        current_user: dict,
        db: Session,
    ) -> SalesApproval | None:
        """Send a document up for approval.

        Returns None when nothing needs approving. A proposal always needs
        it, so None only comes back for a sales order raised off one the
        CEO has already signed.
        """

        if document_type not in ApprovalDocument.ALL:
            raise HTTPException(status_code=400, detail="Unknown document type.")

        # Which price list this is written against follows from who is
        # buying, so it is read off the document rather than taken from
        # the form. It used to be a dropdown beside the discount, which let
        # a dealer be sent up for approval at end customer price simply by
        # leaving the picker alone.
        derived = ApprovalService._price_type_for_document(
            document_type, document_id, db
        )

        price_type = derived or price_type

        if price_type not in PriceType.ALL:
            raise HTTPException(status_code=400, detail="Unknown price type.")

        # And the discount the chain is built from is the deepest one on
        # the document, not the blended share the form worked out. Whoever
        # owns a 25% cut owns it whether it is on every line or one.
        deepest = ApprovalService._deepest_discount(document_type, document_id, db)

        if deepest is not None:
            discount_percent = max(float(discount_percent or 0), deepest)

        requester = (
            db.query(User)
            .filter(User.id == UUID(str(current_user["user_id"])))
            .first()
        )

        chain = approval_chain(price_type, discount_percent, db, document_type)

        # The chain starts above whoever raised it. The bands say who owns
        # how much discount, so a role the requester already holds is
        # their own to give - an AVP's 5% is an AVP's decision, and asking
        # for it sent the request to every AVP in the business, the
        # requester included, where any of them could sign it off.
        chain = ApprovalService._above_requester(chain, requester, db)

        if not chain:
            return None

        existing = ApprovalService.open_for(document_type, document_id, db)

        if existing is not None:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"This {DOCUMENT_LABELS[document_type].lower()} is already "
                    f"waiting on the {existing.steps[existing.current_step]['role']}."
                ),
            )

        approval = SalesApproval(
            document_type=document_type,
            document_id=document_id,
            document_number=document_number,
            price_type=price_type,
            discount_percent=float(discount_percent or 0),
            discount_amount=discount_amount,
            orc_percent=orc_percent,
            orc_amount=orc_amount,
            document_value=document_value,
            status=ApprovalStatus.PENDING,
            current_step=0,
            steps=[
                {
                    "role": role,
                    "approver_id": None,
                    "approver_name": None,
                    "decision": None,
                    "remarks": None,
                    "decided_at": None,
                }
                for role in chain
            ],
            requested_by=requester.id if requester else None,
            requested_by_name=_name(requester),
            remarks=(remarks or "").strip()[:2000] or None,
        )

        db.add(approval)
        db.commit()
        db.refresh(approval)

        ApprovalService._hold_document(approval, db)
        ApprovalService._announce(approval, db, event="raised")

        return approval

    @staticmethod
    def _hold_document(approval: SalesApproval, db: Session) -> None:
        """Park the document at Pending Approval while the chain runs.

        Nothing should go to a client, or into fulfilment, on a discount
        nobody has signed for yet.
        """

        try:
            waiting_on = approval.steps[approval.current_step]["role"]

            if approval.document_type == ApprovalDocument.QUOTATION:
                from app.core.workflow_status import QuotationStatus
                from app.models.quotation import Quotation
                from app.services.quotation_service import QuotationService

                document = db.query(Quotation).filter(
                    Quotation.id == approval.document_id
                ).first()

                if document is None or document.status != QuotationStatus.DRAFT:
                    return

                previous = document.status
                document.status = QuotationStatus.PENDING_APPROVAL

                QuotationService.record_activity(
                    db,
                    document,
                    action="Sent For Approval",
                    description=f"Waiting on the {waiting_on}.",
                    from_status=previous,
                    to_status=document.status,
                    user_id=str(approval.requested_by) if approval.requested_by else None,
                    commit=True,
                )
                return

            from app.core.workflow_status import SalesOrderStatus
            from app.models.sales_order import SalesOrder
            from app.services.sales_order_service import SalesOrderService

            document = db.query(SalesOrder).filter(
                SalesOrder.id == approval.document_id
            ).first()

            if document is None or document.status != SalesOrderStatus.DRAFT:
                return

            previous = document.status
            document.status = SalesOrderStatus.PENDING_APPROVAL

            SalesOrderService.record_activity(
                db,
                document,
                action="Sent For Approval",
                description=f"Waiting on the {waiting_on}.",
                from_status=previous,
                to_status=document.status,
                user_id=str(approval.requested_by) if approval.requested_by else None,
                commit=True,
            )
        except Exception:  # noqa: BLE001 - the request itself already stands
            db.rollback()
            logger.exception(
                "Approval %s raised but the %s could not be held",
                approval.id,
                approval.document_type,
            )

    # ------------------------------------------------------------------
    # Deciding
    # ------------------------------------------------------------------
    @staticmethod
    def _managers_of(requester_id: str, db: Session) -> list[User]:
        """The requester's reporting line, nearest manager first."""

        users = db.query(User).filter(User.is_active.is_(True)).all()
        by_id = {str(u.id): u for u in users}
        managers = {
            str(u.id): (str(u.reports_to_id) if u.reports_to_id else None)
            for u in users
        }

        chain = HierarchyService.manager_chain(str(requester_id or ""), managers)

        return [by_id[uid] for uid in chain if uid in by_id]

    @staticmethod
    def _above_requester(chain: list[str], requester, db: Session) -> list[str]:
        """The part of the chain that is actually above the person asking.

        The chain is built junior first - AVP, then CEO, then the founder
        - so it is already a ladder. Somebody standing on one of its rungs
        owns every rung at or below them: the bands exist to say who may
        give how much away, and an AVP giving 5% is the AVP's decision.

        Asking anyway was worse than redundant. Nobody in an AVP's own
        reporting line holds AVP, so the step fell through to "everyone
        with the role" and the request went to all four AVPs at once,
        the requester among them, for any of them to sign.

        A super admin stands above the whole chart, so nothing is left
        for them to be asked.
        """

        if requester is None:
            return chain

        if getattr(requester, "is_super_admin", False):
            return []

        held = _role_names(requester)

        # Walk from the top of the ladder down: the highest rung they
        # hold decides how much of the chain is already theirs.
        for index in range(len(chain) - 1, -1, -1):
            if chain[index] in held:
                return chain[index + 1:]

        return chain

    @staticmethod
    def approvers_for_step(approval: SalesApproval, db: Session) -> list[User]:
        """Who this step is actually waiting on.

        The request goes to the requester's *own* manager holding that
        role - an Area Manager's discount is their AVP's to approve, not
        another region's.

        Where nobody in that line holds it, it used to go to everyone
        with the role. That is sideways, not upwards: with four AVPs, a
        request that should have had one owner arrived in four queues,
        and any of them could sign for a region that was not theirs. It
        now goes up the same line instead - the nearest manager above
        them - and only to the super admins when the line runs out, so a
        gap in the chart still cannot freeze a deal.
        """

        if approval.status != ApprovalStatus.PENDING or not approval.steps:
            return []

        role = approval.steps[approval.current_step]["role"]
        managers = [
            manager
            for manager in ApprovalService._managers_of(approval.requested_by, db)
            if str(manager.id) != str(approval.requested_by)
        ]

        in_line = [
            manager for manager in managers if role in _role_names(manager)
        ]

        if in_line:
            return in_line

        # Nobody in the line holds it. The nearest manager above them
        # owns it rather than a row of strangers who happen to share the
        # title - _managers_of walks upwards, so the first is the nearest.
        above = [
            manager
            for manager in managers
            if manager.is_active and not manager.is_super_admin
        ]

        if above:
            return above[:1]

        return (
            db.query(User)
            .filter(User.is_super_admin.is_(True), User.is_active.is_(True))
            .all()
        )

    @staticmethod
    def can_decide(approval: SalesApproval, user: User, db: Session) -> bool:
        """Whether this user is the one the current step is waiting on.

        A super admin stands in for the founder, and can also unblock a
        step nobody else holds - otherwise a missing AVP would freeze
        every deal in the region.
        """

        if approval.status != ApprovalStatus.PENDING:
            return False

        if user.is_super_admin:
            return True

        return any(
            str(approver.id) == str(user.id)
            for approver in ApprovalService.approvers_for_step(approval, db)
        )

    @staticmethod
    def decide_by_link(
        approval_id: int,
        user_id: str,
        step: int,
        approve: bool,
        db: Session,
    ) -> SalesApproval:
        """Decide from the email link, with the link standing in for a login.

        Everything the token claims is checked against the database
        before anything happens, because the token says what it was
        issued for and the database says what is actually true now.

        The step matters most. A link is issued for one step; once that
        step has been decided the approval has moved on, so the link is
        spent. That is what stops a forwarded mail being a second
        signature, and what stops the AVP's link being used after it has
        reached the CEO.
        """

        approval = (
            db.query(SalesApproval).filter(SalesApproval.id == approval_id).first()
        )

        if approval is None:
            raise HTTPException(status_code=404, detail="That request no longer exists.")

        if approval.status != ApprovalStatus.PENDING:
            raise HTTPException(
                status_code=409,
                detail=f"It has already been {approval.status.lower()}.",
            )

        if approval.current_step != step:
            raise HTTPException(
                status_code=409,
                detail=(
                    "That link was for an earlier step, which has already "
                    "been decided."
                ),
            )

        user = db.query(User).filter(User.id == user_id).first()

        if user is None or not user.is_active:
            raise HTTPException(
                status_code=403,
                detail="That account is no longer active.",
            )

        # The link does not widen who may decide. Whoever it was issued
        # to still has to be one of the people this step is waiting on.
        allowed = {
            str(u.id) for u in ApprovalService.approvers_for_step(approval, db)
        }

        if str(user.id) not in allowed:
            raise HTTPException(
                status_code=403,
                detail="This step is not waiting on you.",
            )

        # Deciding in one click is for the people the chain actually
        # names - the AVP, the CEO, the founder. Somebody who is only on
        # this step because the reporting line had a gap and it fell back
        # to the super admins can still decide it, in the CRM, where they
        # are signed in and can see what they are signing.
        step_role = approval.steps[approval.current_step]["role"]

        if step_role not in _role_names(user):
            raise HTTPException(
                status_code=403,
                detail=(
                    f"One-click approval is for the {step_role}. Open the "
                    "CRM to decide this one."
                ),
            )

        return ApprovalService.decide(
            approval_id,
            approve,
            "Decided from the approval email.",
            {"user_id": str(user.id), "is_super_admin": user.is_super_admin},
            db,
        )

    @staticmethod
    def decide(
        approval_id: int,
        approve: bool,
        remarks: str | None,
        current_user: dict,
        db: Session,
    ) -> SalesApproval:
        approval = db.query(SalesApproval).filter(SalesApproval.id == approval_id).first()

        if approval is None:
            raise HTTPException(status_code=404, detail="Approval request not found.")

        if approval.status != ApprovalStatus.PENDING:
            raise HTTPException(
                status_code=400,
                detail=f"This request was already {approval.status.lower()}.",
            )

        user = db.query(User).filter(User.id == UUID(str(current_user["user_id"]))).first()

        if user is None:
            raise HTTPException(status_code=401, detail="User not found.")

        if not ApprovalService.can_decide(approval, user, db):
            waiting_on = approval.steps[approval.current_step]["role"]
            raise HTTPException(
                status_code=403,
                detail=f"This request is waiting on the {waiting_on}.",
            )

        # SQLAlchemy does not see a mutated JSON list, so the column is
        # reassigned rather than edited in place.
        steps = [dict(step) for step in approval.steps]
        steps[approval.current_step].update({
            "approver_id": str(user.id),
            "approver_name": _name(user),
            "decision": "APPROVED" if approve else "REJECTED",
            "remarks": (remarks or "").strip() or None,
            "decided_at": datetime.utcnow().isoformat(),
        })
        approval.steps = steps

        if not approve:
            approval.status = ApprovalStatus.REJECTED
            approval.decided_at = datetime.utcnow()
        elif approval.current_step + 1 >= len(steps):
            approval.status = ApprovalStatus.APPROVED
            approval.decided_at = datetime.utcnow()
        else:
            approval.current_step += 1

        db.commit()
        db.refresh(approval)

        ApprovalService._apply_to_document(approval, db, actor=_name(user))

        ApprovalService._announce(
            approval,
            db,
            event="approved" if approve else "rejected",
            actor=_name(user),
        )

        return approval

    @staticmethod
    def _apply_to_document(approval: SalesApproval, db: Session, actor: str) -> None:
        """Move the document once the chain has finished with it.

        Full approval releases it: a quotation goes back to Draft cleared
        to send, a sales order is Confirmed and enters fulfilment. A
        rejection hands both back to Draft to be reworked. A step that is
        merely passed upwards leaves the document where it is.

        Never allowed to break the decision - a document that cannot be
        moved is logged, and the approval record still says what happened.
        """

        if approval.status not in (ApprovalStatus.APPROVED, ApprovalStatus.REJECTED):
            return

        approved = approval.status == ApprovalStatus.APPROVED

        try:
            if approval.document_type == ApprovalDocument.QUOTATION:
                from app.core.workflow_status import QuotationStatus
                from app.models.quotation import Quotation
                from app.services.quotation_service import QuotationService

                document = db.query(Quotation).filter(
                    Quotation.id == approval.document_id
                ).first()

                if document is None or document.status != QuotationStatus.PENDING_APPROVAL:
                    return

                previous = document.status
                document.status = QuotationStatus.DRAFT

                QuotationService.record_activity(
                    db,
                    document,
                    action="Discount Approved" if approved else "Discount Rejected",
                    description=(
                        f"{actor} approved the discount. Ready to send."
                        if approved
                        else f"{actor} rejected the discount. Back to draft."
                    ),
                    from_status=previous,
                    to_status=document.status,
                    user_id=None,
                    commit=True,
                )
                return

            from app.core.workflow_status import SalesOrderStatus
            from app.models.sales_order import SalesOrder
            from app.services.sales_order_service import SalesOrderService

            document = db.query(SalesOrder).filter(
                SalesOrder.id == approval.document_id
            ).first()

            if document is None or document.status != SalesOrderStatus.PENDING_APPROVAL:
                return

            previous = document.status
            document.status = (
                SalesOrderStatus.CONFIRMED if approved else SalesOrderStatus.DRAFT
            )

            SalesOrderService.record_activity(
                db,
                document,
                action="Order Approved" if approved else "Approval Rejected",
                description=(
                    f"{actor} approved the order. It moves on to accounts for "
                    "payment verification."
                    if approved
                    else f"{actor} rejected the order. Back to draft."
                ),
                from_status=previous,
                to_status=document.status,
                user_id=None,
                commit=True,
            )
        except Exception:  # noqa: BLE001 - the decision itself already stands
            db.rollback()
            logger.exception(
                "Approval %s decided but the %s could not be moved",
                approval.id,
                approval.document_type,
            )

    @staticmethod
    def withdraw(approval_id: int, current_user: dict, db: Session) -> SalesApproval:
        """Pull a request back - the requester's own, or a super admin's call."""

        approval = db.query(SalesApproval).filter(SalesApproval.id == approval_id).first()

        if approval is None:
            raise HTTPException(status_code=404, detail="Approval request not found.")

        if approval.status != ApprovalStatus.PENDING:
            raise HTTPException(status_code=400, detail="Only a pending request can be withdrawn.")

        is_requester = str(approval.requested_by or "") == str(current_user.get("user_id"))

        if not is_requester and not current_user.get("is_super_admin"):
            raise HTTPException(
                status_code=403,
                detail="Only the person who raised this request can withdraw it.",
            )

        approval.status = ApprovalStatus.WITHDRAWN
        approval.decided_at = datetime.utcnow()
        db.commit()
        db.refresh(approval)

        return approval

    # ------------------------------------------------------------------
    # Queues
    # ------------------------------------------------------------------
    @staticmethod
    def waiting_on(current_user: dict, db: Session) -> list[SalesApproval]:
        """Requests this user is the one holding up."""

        user = db.query(User).filter(User.id == UUID(str(current_user["user_id"]))).first()

        if user is None:
            return []

        pending = (
            db.query(SalesApproval)
            .filter(SalesApproval.status == ApprovalStatus.PENDING)
            .order_by(SalesApproval.requested_at.asc())
            .all()
        )

        return [a for a in pending if ApprovalService.can_decide(a, user, db)]

    @staticmethod
    def raised_by_team(current_user: dict, db: Session) -> list[SalesApproval]:
        """Every request from someone this user can see, decided or not."""

        visible = HierarchyService.visible_user_ids(current_user, db)

        query = db.query(SalesApproval)

        if visible is not None:
            query = query.filter(
                SalesApproval.requested_by.in_([UUID(uid) for uid in visible])
            )

        return query.order_by(SalesApproval.id.desc()).all()

    # ------------------------------------------------------------------
    # Telling people
    # ------------------------------------------------------------------
    @staticmethod
    def _chain_emails(approval: SalesApproval, db: Session) -> tuple[list[str], list[str]]:
        """Who to write to: the approver on point, then everyone above.

        The person the step is waiting on is the recipient. The requester
        and their managers are copied, so the whole reporting line can see
        where the deal has got to.
        """

        to = [
            approver.email
            for approver in ApprovalService.approvers_for_step(approval, db)
            if approver.email
        ]

        cc: list[str] = []

        requester = (
            db.query(User)
            .filter(User.id == approval.requested_by)
            .first()
            if approval.requested_by
            else None
        )

        if requester and requester.email:
            cc.append(requester.email)

        for manager in ApprovalService._managers_of(approval.requested_by, db):
            if manager.email:
                cc.append(manager.email)

        # Never copy someone the message is already addressed to.
        cc = [address for address in dict.fromkeys(cc) if address not in to]

        return list(dict.fromkeys(to)), cc

    @staticmethod
    def _announce(
        approval: SalesApproval,
        db: Session,
        event: str,
        actor: str | None = None,
    ) -> None:
        """Email the step's approver and the reporting line.

        Never allowed to break the decision it is reporting - a mail
        server that is down must not stop a deal being approved.
        """

        try:
            label = DOCUMENT_LABELS.get(approval.document_type, "Document")
            reference = approval.document_number or f"#{approval.document_id}"
            link = DOCUMENT_LINKS.get(approval.document_type, "").format(
                id=approval.document_id
            )

            to, cc = ApprovalService._chain_emails(approval, db)

            if not to and not cc:
                return

            if event == "raised":
                waiting_on = approval.steps[approval.current_step]["role"]
                subject = f"Approval needed - {label} {reference}"
                greeting = f"Dear {waiting_on},"
                heading = f"{label} {reference} needs your approval"
                paragraphs = [
                    f"{approval.requested_by_name} has raised {label.lower()} "
                    f"{reference} and it is now with you to approve.",
                    describe_chain(
                        approval.price_type,
                        approval.discount_percent,
                        db,
                        approval.document_type,
                    ),
                ]
                note = None
                signed_by = approval.requested_by_name
            elif event == "approved" and approval.status == ApprovalStatus.APPROVED:
                subject = f"Approved - {label} {reference}"
                greeting = f"Dear {approval.requested_by_name},"
                heading = f"{label} {reference} is fully approved"
                paragraphs = [
                    f"{actor} has given the final approval. You can go ahead "
                    + (
                        "and send this quotation to the client."
                        if approval.document_type == ApprovalDocument.QUOTATION
                        else "and move this order into fulfilment."
                    ),
                ]
                note = None
                signed_by = actor
            elif event == "approved":
                waiting_on = approval.steps[approval.current_step]["role"]
                subject = f"Approval needed - {label} {reference}"
                greeting = f"Dear {waiting_on},"
                heading = f"{label} {reference} now needs your approval"
                paragraphs = [
                    f"{actor} has approved this, and it has come up to you.",
                    describe_chain(
                        approval.price_type,
                        approval.discount_percent,
                        db,
                        approval.document_type,
                    ),
                ]
                note = None
                signed_by = actor
            else:
                subject = f"Not approved - {label} {reference}"
                greeting = f"Dear {approval.requested_by_name},"
                heading = f"{label} {reference} was not approved"
                paragraphs = [
                    f"{actor} has declined this. It has gone back to draft so "
                    "the pricing can be reworked and raised again.",
                ]
                note = None
                signed_by = actor

            facts: list[tuple[str, str]] = [
                (f"{label} No.", reference),
                ("Raised by", approval.requested_by_name or "-"),
            ]

            if approval.document_value:
                facts.append(("Order value", inr(approval.document_value)))
            if approval.discount_amount:
                facts.append((
                    "Discount",
                    f"{approval.discount_percent:g}% ({inr(approval.discount_amount)})",
                ))
            if approval.orc_amount:
                facts.append((
                    "ORC",
                    f"{float(approval.orc_percent or 0):g}% ({inr(approval.orc_amount)})",
                ))
            if approval.price_type == PriceType.DP:
                facts.append(("Price type", "Dealer price (transfer price)"))
            if approval.remarks:
                facts.append(("Remarks", approval.remarks))

            # Where it has got to, so nobody has to open the CRM to find out.
            trail = " -> ".join(
                f"{step['role']}"
                + (
                    f" ({step['decision'].title()})"
                    if step.get("decision")
                    else " (pending)"
                )
                for step in approval.steps
            )
            facts.append(("Approval chain", trail))

            # One-click Approve and Reject, but only when the step is
            # waiting on exactly one person. A token names who it was
            # issued to, so a single mail addressed to several approvers
            # has nobody to issue it to - and guessing would hand one
            # person's authority to another. Those get the Open link,
            # which is where they were going anyway.
            decisions = None

            # "raised" is the first ask and the one that matters most -
            # it is the mail the first approver actually acts on. It was
            # spelled "requested" here, which is not an event this class
            # ever emits, so the buttons only appeared from the second
            # step onwards and the AVP never got them.
            if event in ("raised", "approved") and approval.status == ApprovalStatus.PENDING:
                on_point = ApprovalService.approvers_for_step(approval, db)

                # And only to somebody who actually holds the role this
                # step names. approvers_for_step falls back to every
                # holder, then to the super admins, so a gap in the
                # reporting lines cannot freeze a deal - but a fallback
                # is the wrong thing to hand a one-click signature to.
                # Those people open the CRM like anybody else.
                step_role = approval.steps[approval.current_step]["role"]

                if len(on_point) == 1 and step_role in _role_names(on_point[0]):
                    from app.services.approval_link_service import make_token

                    who = str(on_point[0].id)
                    step = approval.current_step

                    decisions = (
                        app_url(
                            "/api/v1/approvals/decide-by-link?token="
                            + make_token(approval.id, who, step, "approve"),
                            db,
                        ),
                        app_url(
                            "/api/v1/approvals/decide-by-link?token="
                            + make_token(approval.id, who, step, "reject"),
                            db,
                        ),
                    )

            letter = EmailLetter(
                db,
                heading=heading,
                greeting=greeting,
                paragraphs=paragraphs,
                facts=facts,
                decisions=decisions,
                action=(f"Open {label.lower()} {reference}", app_url(link, db)) if link else None,
                note=note,
                sign_off_name=signed_by,
            )

            ApprovalService._notify_in_app(
                approval, db, heading=heading, link=link, actor=signed_by
            )

            EmailService.send(
                to=to or cc,
                subject=subject,
                text_body=letter.text(),
                html_body=letter.html(),
                cc=cc if to else [],
            )
        except Exception:  # noqa: BLE001 - never block the decision
            logger.exception(
                "Could not send approval mail for %s %s",
                approval.document_type,
                approval.document_id,
            )


    @staticmethod
    def _notify_in_app(
        approval: SalesApproval,
        db: Session,
        *,
        heading: str,
        link: str,
        actor: str | None,
    ) -> None:
        """Ring the bell for the people this concerns.

        Pending goes to whoever is holding it up; a decision goes back to
        the person who raised it and their managers, so the outcome lands
        with the people waiting on it rather than only in an inbox.
        """

        module = (
            "quotation"
            if approval.document_type == ApprovalDocument.QUOTATION
            else "sales_order"
        )

        if approval.status == ApprovalStatus.PENDING:
            recipients = [
                approver.id
                for approver in ApprovalService.approvers_for_step(approval, db)
            ]
            action = "Approval Needed"
        else:
            recipients = [approval.requested_by] if approval.requested_by else []
            recipients += [
                manager.id
                for manager in ApprovalService._managers_of(approval.requested_by, db)
            ]
            action = (
                "Approved"
                if approval.status == ApprovalStatus.APPROVED
                else "Not Approved"
            )

        notify_users(
            db,
            recipients,
            module=module,
            entity_id=approval.document_id,
            action=action,
            message=heading,
            link=link or None,
            actor_name=actor,
        )


def serialize_approval(approval: SalesApproval, db=None) -> dict:
    waiting_on = (
        approval.steps[approval.current_step]["role"]
        if approval.status == ApprovalStatus.PENDING and approval.steps
        else None
    )

    return {
        "id": approval.id,
        "document_type": approval.document_type,
        "document_id": approval.document_id,
        "document_number": approval.document_number,
        "price_type": approval.price_type,
        "discount_percent": approval.discount_percent,
        "discount_amount": approval.discount_amount,
        "orc_percent": approval.orc_percent,
        "orc_amount": approval.orc_amount,
        "document_value": approval.document_value,
        "status": approval.status,
        "current_step": approval.current_step,
        "waiting_on": waiting_on,
        "steps": approval.steps or [],
        "requested_by": str(approval.requested_by) if approval.requested_by else None,
        "requested_by_name": approval.requested_by_name,
        "requested_at": approval.requested_at.isoformat() if approval.requested_at else None,
        "decided_at": approval.decided_at.isoformat() if approval.decided_at else None,
        "remarks": approval.remarks,
        "reason": describe_chain(
                        approval.price_type,
                        approval.discount_percent,
                        db,
                        approval.document_type,
                    ),
    }
