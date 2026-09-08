from sqlalchemy.orm import Session

from app.models.opportunity import Opportunity


class OpportunityRepository:

    @staticmethod
    def create(db: Session, opportunity: Opportunity) -> Opportunity:
        db.add(opportunity)
        db.commit()
        db.refresh(opportunity)
        return opportunity

    @staticmethod
    def save(db: Session, opportunity: Opportunity) -> Opportunity:
        db.add(opportunity)
        db.commit()
        db.refresh(opportunity)
        return opportunity

    @staticmethod
    def get_by_id(db: Session, opportunity_id: int) -> Opportunity | None:
        return (
            db.query(Opportunity)
            .filter(Opportunity.id == opportunity_id)
            .first()
        )

    @staticmethod
    def get_by_lead_id(db: Session, lead_id: int) -> Opportunity | None:
        return (
            db.query(Opportunity)
            .filter(Opportunity.lead_id == lead_id)
            .first()
        )

    @staticmethod
    def get_visible(
        db: Session,
        visible_creator_ids: list[str],
        user_id: str | None = None,
    ) -> list[Opportunity]:
        """Return opportunities the caller may see.

        Follows the convention already used by ``get_visible_creator_user_ids``
        in lead_service: an empty ``visible_creator_ids`` means unrestricted
        super admin access. Otherwise the caller sees rows they created, rows
        assigned to them, and rows created by their juniors.
        """

        from uuid import UUID

        from sqlalchemy import or_

        query = db.query(Opportunity)

        if visible_creator_ids:
            creator_uuids = [UUID(uid) for uid in visible_creator_ids]
            conditions = [Opportunity.creator_id.in_(creator_uuids)]

            if user_id:
                conditions.append(Opportunity.assigned_to_id == UUID(user_id))

            query = query.filter(or_(*conditions))

        return query.order_by(Opportunity.id.desc()).all()
