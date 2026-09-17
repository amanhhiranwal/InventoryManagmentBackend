from sqlalchemy.orm import Session

from app.services.proforma_invoice_service import (
    ProformaInvoiceService,
    serialize_proforma_invoice,
)
from app.utils.user_names import get_user_names_helper


def _serialize_activity(row: dict, names_map: dict[str, str]) -> dict:
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
        "created_at": (
            created_at.isoformat() if hasattr(created_at, "isoformat") else created_at
        ),
    }


class ProformaInvoiceController:

    @staticmethod
    def get_all(current_user: dict, db: Session, sales_order_id: int | None = None):
        invoices = ProformaInvoiceService.get_visible(current_user, db, sales_order_id)

        return {
            "success": True,
            "data": [serialize_proforma_invoice(i) for i in invoices],
        }

    @staticmethod
    def get_by_id(invoice_id: int, db: Session):
        return {
            "success": True,
            "data": serialize_proforma_invoice(
                ProformaInvoiceService.get_by_id(invoice_id, db)
            ),
        }

    @staticmethod
    def company_profile():
        return {"success": True, "data": ProformaInvoiceService.company_profile()}

    @staticmethod
    def create(request, current_user: dict, db: Session):
        invoice = ProformaInvoiceService.create(request, current_user, db)

        return {
            "success": True,
            "message": f"Proforma invoice {invoice.pi_number} created.",
            "data": serialize_proforma_invoice(invoice),
        }

    @staticmethod
    def update(invoice_id: int, request, current_user: dict, db: Session):
        invoice = ProformaInvoiceService.update(invoice_id, request, current_user, db)

        return {
            "success": True,
            "message": "Proforma invoice updated.",
            "data": serialize_proforma_invoice(invoice),
        }

    @staticmethod
    def generate(invoice_id: int, current_user: dict, db: Session):
        invoice = ProformaInvoiceService.generate(invoice_id, current_user, db)

        return {
            "success": True,
            "message": f"Proforma invoice {invoice.pi_number} generated.",
            "data": serialize_proforma_invoice(invoice),
        }

    @staticmethod
    def update_status(invoice_id: int, request, current_user: dict, db: Session):
        invoice = ProformaInvoiceService.update_status(
            invoice_id, request, current_user, db
        )

        return {
            "success": True,
            "message": f"Proforma invoice moved to {invoice.status}.",
            "data": serialize_proforma_invoice(invoice),
        }

    @staticmethod
    def send(invoice_id: int, request, current_user: dict, db: Session):
        result = ProformaInvoiceService.send(invoice_id, request, current_user, db)

        recipients = ", ".join(result["recipients"])

        return {
            "success": True,
            "message": (
                f"Test email sent to {recipients}."
                if result["test_only"]
                else f"Proforma invoice emailed to {recipients}."
            ),
            "data": serialize_proforma_invoice(result["invoice"]),
        }

    @staticmethod
    def get_activities(invoice_id: int, current_user: dict, db: Session):
        rows = ProformaInvoiceService.get_activities(invoice_id, current_user, db)

        names_map = get_user_names_helper(
            list({row["created_by"] for row in rows if row.get("created_by")}),
            db,
        )

        return {
            "success": True,
            "data": [_serialize_activity(row, names_map) for row in rows],
        }

    @staticmethod
    def log_activity(invoice_id: int, request, current_user: dict, db: Session):
        invoice, activity = ProformaInvoiceService.log_activity(
            invoice_id, request, current_user, db
        )

        created_by = str(activity.created_by) if activity.created_by else None
        names_map = get_user_names_helper([created_by] if created_by else [], db)

        return {
            "success": True,
            "message": "Activity logged successfully.",
            "data": {
                "activity": _serialize_activity(
                    {
                        "id": f"pi-{activity.id}",
                        "source": "proforma_invoice",
                        "action": activity.action,
                        "description": activity.description,
                        "from_status": activity.from_status,
                        "to_status": activity.to_status,
                        "created_by": created_by,
                        "created_at": activity.created_at,
                    },
                    names_map,
                ),
                "invoice": serialize_proforma_invoice(invoice),
            },
        }

    @staticmethod
    def delete(invoice_id: int, current_user: dict, db: Session):
        ProformaInvoiceService.delete(invoice_id, current_user, db)

        return {"success": True, "message": "Proforma invoice deleted."}
