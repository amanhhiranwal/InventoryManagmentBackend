from uuid import UUID

from sqlalchemy.orm import Session

from app.services.opportunity_service import (
    OpportunityService,
    serialize_opportunity,
)


class OpportunityController:

    @staticmethod
    def get_all(current_user: dict, db: Session):
        opportunities = OpportunityService.get_visible(current_user, db)

        return {
            "success": True,
            "data": [serialize_opportunity(o) for o in opportunities],
        }

    @staticmethod
    def get_by_id(opportunity_id: int, db: Session):
        opportunity = OpportunityService.get_by_id(opportunity_id, db)

        return {
            "success": True,
            "data": serialize_opportunity(opportunity),
        }

    @staticmethod
    def create(request, current_user: dict, db: Session):
        opportunity = OpportunityService.create(
            request,
            UUID(current_user["user_id"]),
            db,
            current_user,
        )

        return {
            "success": True,
            "message": "Opportunity created successfully.",
            "data": serialize_opportunity(opportunity),
        }

    @staticmethod
    def update(opportunity_id: int, request, current_user: dict, db: Session):
        opportunity = OpportunityService.update(
            opportunity_id,
            request,
            current_user,
            db,
        )

        return {
            "success": True,
            "message": "Opportunity updated successfully.",
            "data": serialize_opportunity(opportunity),
        }

    @staticmethod
    def update_status(
        opportunity_id: int,
        request,
        current_user: dict,
        db: Session,
    ):
        opportunity = OpportunityService.update_status(
            opportunity_id,
            request,
            current_user,
            db,
        )

        return {
            "success": True,
            "message": f"Opportunity moved to {opportunity.status}.",
            "data": serialize_opportunity(opportunity),
        }

    @staticmethod
    def convert_lead(lead_id: int, request, current_user: dict, db: Session):
        opportunity = OpportunityService.convert_lead(
            lead_id,
            request,
            current_user,
            db,
        )

        return {
            "success": True,
            "message": "Lead converted to opportunity successfully.",
            "data": serialize_opportunity(opportunity),
        }
