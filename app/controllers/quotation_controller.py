from uuid import UUID

from sqlalchemy.orm import Session

from app.utils.user_names import get_user_names_helper
from app.services.quotation_service import (
    QuotationService,
    serialize_quotation,
)


def _serialize_merged_activity(row: dict, names_map: dict[str, str]) -> dict:
    """Shape one Activity History entry for the quotation detail page.

    ``source`` says whether the entry belongs to the quotation itself or to
    the opportunity it was raised from, so the page can label the two apart.
    """

    created_by = row.get("created_by")
    created_at = row.get("created_at")

    return {
        "id": row["id"],
        "source": row["source"],
        "action": row["action"],
        "description": row.get("description"),
        "from_status": row.get("from_status"),
        "to_status": row.get("to_status"),
        "created_by": created_by,
        "created_by_name": names_map.get(created_by) if created_by else None,
        "created_at": created_at.isoformat() if created_at else None,
    }


class QuotationController:

    @staticmethod
    def get_activities(quotation_id: int, current_user: dict, db: Session):
        rows = QuotationService.get_activities(quotation_id, current_user, db)

        names_map = get_user_names_helper(
            list({row["created_by"] for row in rows if row.get("created_by")}),
            db,
        )

        return {
            "success": True,
            "data": [_serialize_merged_activity(r, names_map) for r in rows],
        }

    @staticmethod
    def log_activity(quotation_id: int, request, current_user: dict, db: Session):
        quotation, activity = QuotationService.log_activity(
            quotation_id,
            request,
            current_user,
            db,
        )

        created_by = str(activity.created_by) if activity.created_by else None

        names_map = get_user_names_helper([created_by] if created_by else [], db)

        return {
            "success": True,
            "message": "Activity logged successfully.",
            "data": {
                "activity": _serialize_merged_activity(
                    {
                        "id": f"qt-{activity.id}",
                        "source": "quotation",
                        "action": activity.action,
                        "description": activity.description,
                        "from_status": activity.from_status,
                        "to_status": activity.to_status,
                        "created_by": created_by,
                        "created_at": activity.created_at,
                    },
                    names_map,
                ),
                "quotation": serialize_quotation(quotation),
            },
        }

    @staticmethod
    def get_all(current_user: dict, db: Session):
        quotations = QuotationService.get_visible(current_user, db)

        return {
            "success": True,
            "data": [serialize_quotation(q) for q in quotations],
        }

    @staticmethod
    def get_by_id(quotation_id: int, db: Session):
        quotation = QuotationService.get_by_id(quotation_id, db)

        return {
            "success": True,
            "data": serialize_quotation(quotation),
        }

    @staticmethod
    def create(request, current_user: dict, db: Session):
        quotation = QuotationService.create(
            request,
            UUID(current_user["user_id"]),
            db,
            current_user,
        )

        return {
            "success": True,
            "message": f"Quotation {quotation.quote_number} created successfully.",
            "data": serialize_quotation(quotation),
        }

    @staticmethod
    def update(quotation_id: int, request, current_user: dict, db: Session):
        quotation = QuotationService.update(
            quotation_id,
            request,
            current_user,
            db,
        )

        return {
            "success": True,
            "message": "Quotation updated successfully.",
            "data": serialize_quotation(quotation),
        }

    @staticmethod
    def update_status(
        quotation_id: int,
        request,
        current_user: dict,
        db: Session,
    ):
        quotation = QuotationService.update_status(
            quotation_id,
            request,
            current_user,
            db,
        )

        return {
            "success": True,
            "message": f"Quotation marked as {quotation.status}.",
            "data": serialize_quotation(quotation),
        }

    @staticmethod
    def send(quotation_id: int, request, current_user: dict, db: Session):
        result = QuotationService.send(
            quotation_id,
            request,
            current_user,
            db,
        )

        recipients = ", ".join(result["recipients"])

        message = (
            f"Test quotation email sent to {recipients}."
            if result["test_only"]
            else f"Quotation emailed to {recipients}."
        )

        return {
            "success": True,
            "message": message,
            "data": serialize_quotation(result["quotation"]),
        }
