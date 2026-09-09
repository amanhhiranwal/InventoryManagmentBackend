from uuid import UUID

from sqlalchemy.orm import Session

from app.services.quotation_service import (
    QuotationService,
    serialize_quotation,
)


class QuotationController:

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
