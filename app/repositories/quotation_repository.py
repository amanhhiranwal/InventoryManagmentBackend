from sqlalchemy.orm import Session

from app.models.quotation import Quotation


class QuotationRepository:

    @staticmethod
    def create(db: Session, quotation: Quotation) -> Quotation:
        db.add(quotation)
        db.commit()
        db.refresh(quotation)
        return quotation

    @staticmethod
    def save(db: Session, quotation: Quotation) -> Quotation:
        db.add(quotation)
        db.commit()
        db.refresh(quotation)
        return quotation

    @staticmethod
    def get_by_id(db: Session, quotation_id: int) -> Quotation | None:
        return (
            db.query(Quotation)
            .filter(Quotation.id == quotation_id)
            .first()
        )

    @staticmethod
    def get_by_opportunity_id(
        db: Session,
        opportunity_id: int,
    ) -> list[Quotation]:
        return (
            db.query(Quotation)
            .filter(Quotation.opportunity_id == opportunity_id)
            .order_by(Quotation.id.desc())
            .all()
        )

    @staticmethod
    def next_quote_number(db: Session) -> str:
        """Allocate the next QT-#### reference.

        Derived from the highest existing id rather than a row count so
        deleting a quotation cannot hand its number to a later one.
        """

        last = db.query(Quotation).order_by(Quotation.id.desc()).first()
        next_id = (last.id if last else 0) + 1

        return f"QT-{3000 + next_id}"

    @staticmethod
    def get_visible(
        db: Session,
        visible_creator_ids: list[str],
        user_id: str | None = None,
    ) -> list[Quotation]:
        """Return quotations the caller may see.

        Same convention as OpportunityRepository.get_visible: an empty
        ``visible_creator_ids`` means unrestricted super admin access.
        """

        from uuid import UUID

        from sqlalchemy import or_

        query = db.query(Quotation)

        if visible_creator_ids:
            creator_uuids = [UUID(uid) for uid in visible_creator_ids]
            conditions = [Quotation.creator_id.in_(creator_uuids)]

            if user_id:
                conditions.append(Quotation.assigned_to_id == UUID(user_id))

            query = query.filter(or_(*conditions))

        return query.order_by(Quotation.id.desc()).all()
