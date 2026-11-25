"""Stock added on the Inventory page, straight into the company's store.

A company has one store or several. When a product is added on the
Inventory page with an In-Stock quantity, that quantity - and its serials -
goes into the company's store as it is saved - its default store, or the
one chosen when the company has several - with no approval (the business
decided the product form is trusted for this).

Guarded all the same:
  * the caller may add products (inventory.create), works for the company,
    and may use the store (it may be kept for particular roles);
  * the product is in the catalogue for that company, or shared by all;
  * every serial is one of that product's serials in the catalogue;
  * a request id makes a retried save post once (the ledger's unique key).
"""

import re
from datetime import date
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.core.stock import OPENING_BALANCE, UNRESTRICTED
from app.models.company import Company
from app.models.warehouse import WarehouseBin
from app.services.company_scope_service import CompanyScopeService
from app.services.procurement.common import actor
from app.services.stock import posting
from app.services.stock.documents import catalogue_product
from app.services.warehouse_service import WarehouseService

SOURCE = "PRODUCT_FORM"


def put_product_in_stock(payload, current_user: dict, db: Session) -> dict:
    company_id = str(payload.company_id)
    CompanyScopeService.assert_company_allowed(current_user, company_id, db)
    company = db.query(Company).filter(Company.id == payload.company_id).first()
    if company is None:
        raise HTTPException(status_code=404, detail="Company not found.")

    sku = payload.sku.strip().upper()
    product = catalogue_product(sku, company_id)
    if product is None:
        raise HTTPException(status_code=400, detail=f"{sku} is not in {company.company_name}'s catalogue.")

    quantity = Decimal(str(payload.quantity))
    serials = [s.strip().upper() for s in payload.serials if s and s.strip()]
    if serials:
        if len(serials) != quantity:
            raise HTTPException(status_code=400, detail=f"{len(serials)} serial(s) for a quantity of {quantity.normalize()}.")
        from app.services.inventory_service import InventoryService

        pattern = {"$regex": f"^{re.escape(sku)}$", "$options": "i"}
        known = {
            str(d.get("serial_number") or "").strip().upper()
            for d in InventoryService.items_col.find(
                {"attributes.model_number": pattern, "company_id": {"$in": [company_id, None]}},
                {"serial_number": 1},
            )
        }
        stranger = next((s for s in serials if s not in known), None)
        if stranger:
            raise HTTPException(status_code=400, detail=f"Serial {stranger} is not on {sku} in the catalogue.")

    if payload.warehouse_id:
        # A company with several stores: the one chosen, if it is this
        # company's and one the caller may work in.
        store = CompanyScopeService.assert_warehouse_allowed(current_user, payload.warehouse_id, db)
        if store.company_id != company.id:
            raise HTTPException(status_code=400, detail=f"That store is not one of {company.company_name}'s.")
    else:
        store = WarehouseService.ensure_company_store(db, company)
        db.flush()
        # Its default store may be kept for other roles.
        CompanyScopeService.assert_warehouse_allowed(current_user, store.id, db)
    db.flush()
    bin_row = db.query(WarehouseBin).filter(WarehouseBin.warehouse_id == store.id, WarehouseBin.code == "STORE").first()
    user_id, name = actor(current_user, db)
    try:
        posting.post(db, posting.Posting(
            source_doc_type=SOURCE,
            source_doc_id=str(payload.request_id),
            source_doc_number=sku,
            movement_date=date.today(),
            created_by=str(user_id) if user_id else None,
            created_by_name=name,
            approved_by=str(user_id) if user_id else None,
            approved_by_name=name,
            remarks="Added with the product on the Inventory page",
            movements=[posting.Movement(
                company_id=company.id, warehouse_id=store.id, bin_id=bin_row.id, sku=sku,
                product_name=product.get("name"), stock_status=UNRESTRICTED, quantity=quantity,
                movement_type=OPENING_BALANCE, source_line_id="1", serials=serials,
            )],
        ))
    except HTTPException:
        db.rollback()
        raise
    db.commit()
    return {"company_name": company.company_name, "store": store.location_name, "sku": sku,
            "quantity": float(quantity), "serials": serials}
