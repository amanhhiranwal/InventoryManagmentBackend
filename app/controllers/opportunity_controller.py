from uuid import UUID

from sqlalchemy.orm import Session

from app.services.opportunity_service import (
    OpportunityService,
    serialize_opportunity,
)
from app.utils.user_names import get_user_names_helper


def serialize_activity(activity, names_map: dict[str, str] | None = None) -> dict:
    """Shape one Activity History entry for the Opportunity drawer."""

    names_map = names_map or {}
    created_by = str(activity.created_by) if activity.created_by else None

    return {
        "id": str(activity.id),
        "action": activity.action,
        "description": activity.description,
        "from_status": activity.from_status,
        "to_status": activity.to_status,
        "created_by": created_by,
        "created_by_name": names_map.get(created_by) if created_by else None,
        "created_at": activity.created_at.isoformat(),
    }


class OpportunityController:

    @staticmethod
    def _with_names(opportunities: list, db: Session) -> list[dict]:
        """Serialize and resolve owner names in one bulk lookup.

        The payload carried assigned_to_id but never a name, so every board
        card and list row fell back to the literal string "Sales Team"
        regardless of who the opportunity actually belonged to.
        """

        names_map = get_user_names_helper(
            list(
                {
                    str(uid)
                    for o in opportunities
                    for uid in (o.assigned_to_id, o.creator_id)
                    if uid
                }
            ),
            db,
        )

        rows = []

        for opportunity in opportunities:
            row = serialize_opportunity(opportunity)

            row["assigned_to_name"] = (
                names_map.get(str(opportunity.assigned_to_id))
                if opportunity.assigned_to_id
                else None
            )
            row["creator_name"] = (
                names_map.get(str(opportunity.creator_id))
                if opportunity.creator_id
                else None
            )

            rows.append(row)

        return rows

    @staticmethod
    def get_all(current_user: dict, db: Session):
        opportunities = OpportunityService.get_visible(current_user, db)

        return {
            "success": True,
            "data": OpportunityController._with_names(opportunities, db),
        }

    @staticmethod
    def get_by_id(opportunity_id: int, db: Session, current_user: dict | None = None):
        opportunity = OpportunityService.get_by_id(opportunity_id, db)

        # Opening a record by id follows the same hierarchy as the list.
        if current_user is not None:
            OpportunityService.assert_can_edit(opportunity, current_user, db)

        return {
            "success": True,
            "data": OpportunityController._with_names([opportunity], db)[0],
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
            "data": OpportunityController._with_names([opportunity], db)[0],
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
            "data": OpportunityController._with_names([opportunity], db)[0],
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
            "data": OpportunityController._with_names([opportunity], db)[0],
        }

    @staticmethod
    def get_activities(opportunity_id: int, current_user: dict, db: Session):
        activities = OpportunityService.get_activities(
            opportunity_id,
            current_user,
            db,
        )

        names_map = get_user_names_helper(
            list({str(a.created_by) for a in activities if a.created_by}),
            db,
        )

        return {
            "success": True,
            "data": [serialize_activity(a, names_map) for a in activities],
        }

    @staticmethod
    def log_activity(
        opportunity_id: int,
        request,
        current_user: dict,
        db: Session,
    ):
        opportunity, activity = OpportunityService.log_activity(
            opportunity_id,
            request,
            current_user,
            db,
        )

        names_map = get_user_names_helper(
            [str(activity.created_by)] if activity.created_by else [],
            db,
        )

        return {
            "success": True,
            "message": "Activity logged successfully.",
            "data": {
                "activity": serialize_activity(activity, names_map),
                "opportunity": OpportunityController._with_names(
                    [opportunity], db
                )[0],
            },
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
            "data": OpportunityController._with_names([opportunity], db)[0],
        }
