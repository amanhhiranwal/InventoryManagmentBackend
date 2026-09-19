from typing import Optional

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.controllers.proforma_invoice_controller import ProformaInvoiceController
from app.database.dependencies import get_db
from app.middleware.auth_middleware import get_current_user
from app.schemas.proforma_invoice import (
    CreateProformaInvoiceRequest,
    LogProformaInvoiceActivityRequest,
    SendProformaInvoiceRequest,
    UpdateProformaInvoiceRequest,
    UpdateProformaInvoiceStatusRequest,
)

router = APIRouter(
    prefix="/proforma-invoices",
    tags=["Proforma Invoices"],
)


@router.get("")
def get_proforma_invoices(
    sales_order_id: Optional[int] = None,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """All visible proforma invoices, or those raised against one order."""

    return ProformaInvoiceController.get_all(current_user, db, sales_order_id)


@router.get("/company-profile")
def get_company_profile(current_user=Depends(get_current_user)):
    """Seller name, address, banking and signatory printed on the invoice."""

    return ProformaInvoiceController.company_profile()


@router.post("")
def create_proforma_invoice(
    request: CreateProformaInvoiceRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    return ProformaInvoiceController.create(request, current_user, db)


@router.get("/{invoice_id}")
def get_proforma_invoice(
    invoice_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    return ProformaInvoiceController.get_by_id(invoice_id, db, current_user)


@router.put("/{invoice_id}")
def update_proforma_invoice(
    invoice_id: int,
    request: UpdateProformaInvoiceRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    return ProformaInvoiceController.update(invoice_id, request, current_user, db)


@router.post("/{invoice_id}/generate")
def generate_proforma_invoice(
    invoice_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    return ProformaInvoiceController.generate(invoice_id, current_user, db)


@router.put("/{invoice_id}/status")
def update_proforma_invoice_status(
    invoice_id: int,
    request: UpdateProformaInvoiceStatusRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    return ProformaInvoiceController.update_status(invoice_id, request, current_user, db)


@router.post("/{invoice_id}/send")
def send_proforma_invoice(
    invoice_id: int,
    request: SendProformaInvoiceRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    return ProformaInvoiceController.send(invoice_id, request, current_user, db)


@router.get("/{invoice_id}/activities")
def get_proforma_invoice_activities(
    invoice_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    return ProformaInvoiceController.get_activities(invoice_id, current_user, db)


@router.post("/{invoice_id}/activities")
def log_proforma_invoice_activity(
    invoice_id: int,
    request: LogProformaInvoiceActivityRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    return ProformaInvoiceController.log_activity(invoice_id, request, current_user, db)


@router.delete("/{invoice_id}")
def delete_proforma_invoice(
    invoice_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    return ProformaInvoiceController.delete(invoice_id, current_user, db)
