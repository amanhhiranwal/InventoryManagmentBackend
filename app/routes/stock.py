"""Warehouse stock: balances, the ledger, and opening stock.

Every route checks its permission, and every query is limited to the
warehouses the caller works in (see app/services/stock/queries.scope).
"""

from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.stock import (
    ADJUSTMENT_IN,
    DOCUMENT_PERMISSIONS,
    OPENING,
    VENDOR_RETURN_DOC,
    VIEW_STOCK,
)
from app.database.dependencies import get_db
from app.middleware.auth_middleware import get_current_user
from app.middleware.permission_middleware import require_permission, require_super_admin
from app.schemas.stock import DocumentCreate, DocumentUpdate, ProductStockIn, RejectRequest
from app.services.company_scope_service import CompanyScopeService
from app.services.procurement.common import has_permission
from app.services.stock import queries
from app.services.stock.documents import StockDocumentService, company_catalogue

router = APIRouter(prefix="/stock", tags=["Stock"])


@router.get("/ledger-status")
def ledger_status(db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    """Whether the stock ledger is live: dispatch orders, not the order
    desks, take stock out. The screens show dispatch orders only then."""

    from app.services.stock.dispatch import ledger_live

    return {"success": True, "data": {"live": ledger_live(db)}}


@router.get("/summary")
def stock_summary(
    company_id: Optional[str] = None,
    warehouse_id: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission(VIEW_STOCK)),
):
    return {"success": True, "data": queries.summary(current_user, db, company_id=company_id, warehouse_id=warehouse_id)}


@router.get("/balances")
def stock_balances(
    company_id: Optional[str] = None,
    warehouse_id: Optional[str] = None,
    status: Optional[str] = None,
    search: Optional[str] = None,
    include_zero: bool = False,
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=200),
    db: Session = Depends(get_db),
    current_user=Depends(require_permission(VIEW_STOCK)),
):
    result = queries.list_balances(
        current_user, db, company_id=company_id, warehouse_id=warehouse_id, status=status,
        search=search, include_zero=include_zero, page=page, size=size,
    )
    return {"success": True, **result}


@router.get("/ledger")
def stock_ledger(
    company_id: Optional[str] = None,
    warehouse_id: Optional[str] = None,
    movement_type: Optional[str] = None,
    search: Optional[str] = None,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=200),
    db: Session = Depends(get_db),
    current_user=Depends(require_permission(VIEW_STOCK)),
):
    result = queries.list_ledger(
        current_user, db, company_id=company_id, warehouse_id=warehouse_id, movement_type=movement_type,
        search=search, date_from=date_from, date_to=date_to, page=page, size=size,
    )
    return {"success": True, **result}


@router.get("/reconcile")
def stock_reconcile(
    db: Session = Depends(get_db),
    current_user=Depends(require_super_admin),
):
    """Balances that differ from their ledger. Empty means all agree."""

    mismatches = queries.reconcile(db)
    return {"success": True, "data": mismatches, "in_step": not mismatches}


# ----------------------------------------------------------------------
# Stock documents: opening stock, adjustments, vendor returns
# ----------------------------------------------------------------------
@router.get("/catalogue")
def stock_catalogue(
    warehouse_id: str,
    search: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Products the warehouse's company may stock, one per SKU. For anyone
    who may draft some stock document, a GRN, a transfer or a count there."""

    if not any(StockDocumentService.can_draft(t, current_user, db) for t in (OPENING, ADJUSTMENT_IN)) and not any(
        has_permission(current_user, p, db) for p in ("grn.create", "transfer.create", "stock.count.create")
    ):
        raise HTTPException(status_code=403, detail="Permission denied.")
    warehouse = CompanyScopeService.assert_warehouse_allowed(current_user, warehouse_id, db)
    return {"success": True, "data": company_catalogue(str(warehouse.company_id), search)}


def _document_routes(path: str, doc_type: str, noun: str) -> None:
    """The same eight routes for each kind of stock document, each checked
    against that kind's own draft and approve permissions."""

    create_perm, approve_perm = DOCUMENT_PERMISSIONS[doc_type]

    @router.get(f"/{path}", name=f"list_{path}")
    def list_documents(
        company_id: Optional[str] = None,
        warehouse_id: Optional[str] = None,
        status: Optional[str] = None,
        search: Optional[str] = None,
        page: int = Query(1, ge=1),
        size: int = Query(20, ge=1, le=200),
        db: Session = Depends(get_db),
        current_user=Depends(require_permission(VIEW_STOCK)),
    ):
        result = StockDocumentService.list_documents(
            doc_type, current_user, db, company_id=company_id, warehouse_id=warehouse_id, status=status,
            search=search, page=page, size=size,
        )
        return {"success": True, **result}

    @router.get(f"/{path}/{{doc_id}}", name=f"get_{path}")
    def get_document(doc_id: int, db: Session = Depends(get_db), current_user=Depends(require_permission(VIEW_STOCK))):
        return {"success": True, "data": StockDocumentService.get(doc_type, doc_id, current_user, db)}

    @router.post(f"/{path}", name=f"create_{path}")
    def create_document(request: DocumentCreate, db: Session = Depends(get_db), current_user=Depends(require_permission(create_perm))):
        return {"success": True, "message": f"{noun} saved as a draft.", "data": StockDocumentService.create(doc_type, request, current_user, db)}

    @router.put(f"/{path}/{{doc_id}}", name=f"update_{path}")
    def update_document(doc_id: int, request: DocumentUpdate, db: Session = Depends(get_db),
                        current_user=Depends(require_permission(create_perm))):
        return {"success": True, "message": "Draft updated.", "data": StockDocumentService.update(doc_type, doc_id, request, current_user, db)}

    @router.delete(f"/{path}/{{doc_id}}", name=f"delete_{path}")
    def delete_document(doc_id: int, db: Session = Depends(get_db), current_user=Depends(require_permission(create_perm))):
        StockDocumentService.delete(doc_type, doc_id, current_user, db)
        return {"success": True, "message": "Draft deleted."}

    @router.post(f"/{path}/{{doc_id}}/submit", name=f"submit_{path}")
    def submit_document(doc_id: int, db: Session = Depends(get_db), current_user=Depends(require_permission(create_perm))):
        return {"success": True, "message": "Sent for approval.", "data": StockDocumentService.submit(doc_type, doc_id, current_user, db)}

    @router.post(f"/{path}/{{doc_id}}/approve", name=f"approve_{path}")
    def approve_document(doc_id: int, db: Session = Depends(get_db), current_user=Depends(require_permission(approve_perm))):
        return {"success": True, "message": "Approved and posted to stock.", "data": StockDocumentService.approve(doc_type, doc_id, current_user, db)}

    @router.post(f"/{path}/{{doc_id}}/reject", name=f"reject_{path}")
    def reject_document(doc_id: int, request: RejectRequest, db: Session = Depends(get_db),
                        current_user=Depends(require_permission(approve_perm))):
        return {"success": True, "message": "Rejected.", "data": StockDocumentService.reject(doc_type, doc_id, request.reason, current_user, db)}


_document_routes("opening", OPENING, "Opening stock")
_document_routes("adjustments", ADJUSTMENT_IN, "Adjustment")
_document_routes("vendor-returns", VENDOR_RETURN_DOC, "Vendor return")


# ----------------------------------------------------------------------
# Reports and the inventory overview
# ----------------------------------------------------------------------
@router.get("/overview")
def stock_overview(
    company_id: Optional[str] = None,
    warehouse_id: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission(VIEW_STOCK)),
):
    from app.services.stock.reports import StockReportService

    return {"success": True, "data": StockReportService.overview(current_user, db, company_id=company_id, warehouse_id=warehouse_id)}


@router.get("/reports/{kind}")
def stock_report(
    kind: str,
    company_id: Optional[str] = None,
    warehouse_id: Optional[str] = None,
    group_by: str = "product",
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    days: Optional[int] = Query(None, ge=1, le=3650),
    format: str = Query("json", pattern="^(json|csv)$"),
    db: Session = Depends(get_db),
    current_user=Depends(require_permission(VIEW_STOCK)),
):
    """One report as rows, or as a CSV download (needs stock.report.export)."""

    from fastapi.responses import Response

    from app.core.stock import REPORT_EXPORT
    from app.services.stock.reports import StockReportService

    if format == "csv" and not has_permission(current_user, REPORT_EXPORT, db):
        raise HTTPException(status_code=403, detail="Downloading reports needs its own permission.")
    result = StockReportService.run(kind, current_user, db, company_id=company_id, warehouse_id=warehouse_id,
                                    group_by=group_by, date_from=date_from, date_to=date_to, days=days)
    if format == "csv":
        return Response(
            content=StockReportService.to_csv(result),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="stock_{kind}_{date.today().isoformat()}.csv"'},
        )
    return {"success": True, "data": result}


@router.post("/product-stock")
def product_stock(
    request: ProductStockIn,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("inventory.create")),
):
    """A product's In-Stock, saved on the Inventory page, into its company's store."""

    from app.services.stock.product_stock import put_product_in_stock

    return {"success": True, "data": put_product_in_stock(request, current_user, db)}
