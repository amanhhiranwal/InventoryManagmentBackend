"""The Product Categories master: what vendors can be said to supply."""

import json
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.procurement import ProductCategory, Vendor
from app.services.procurement.common import text

#: The stock item list the master starts from (836 names), kept beside
#: this module (a folder named data/ is git-ignored here).
SEED_FILE = Path(__file__).with_suffix(".json")

MAX_NAME = 200


def serialize(row: ProductCategory) -> dict:
    return {"id": row.id, "name": row.name, "is_active": row.is_active}


def list_categories(db: Session, *, include_inactive: bool = False, search: str | None = None) -> list[dict]:
    query = db.query(ProductCategory)
    if not include_inactive:
        query = query.filter(ProductCategory.is_active.is_(True))
    if search and search.strip():
        query = query.filter(ProductCategory.name.ilike(f"%{search.strip()}%"))
    return [serialize(row) for row in query.order_by(func.lower(ProductCategory.name)).all()]


def save_category(payload: dict, db: Session, category_id: int | None = None) -> dict:
    name = text(payload.get("name"))
    if not name:
        raise HTTPException(status_code=400, detail="A product category needs a name.")
    name = " ".join(name.split())
    if len(name) > MAX_NAME:
        raise HTTPException(status_code=400, detail=f"Keep the name to {MAX_NAME} characters.")

    clash = db.query(ProductCategory).filter(func.lower(ProductCategory.name) == name.lower())
    if category_id:
        clash = clash.filter(ProductCategory.id != category_id)
    if clash.first():
        raise HTTPException(status_code=409, detail=f"{name} is already in the list.")

    if category_id:
        row = db.query(ProductCategory).filter(ProductCategory.id == category_id).first()
        if row is None:
            raise HTTPException(status_code=404, detail="Product category not found.")
        row.name = name
        if "is_active" in payload:
            row.is_active = bool(payload.get("is_active"))
    else:
        row = ProductCategory(name=name, is_active=bool(payload.get("is_active", True)))
        db.add(row)

    db.commit()
    db.refresh(row)
    return serialize(row)


def delete_category(category_id: int, db: Session) -> None:
    """Removed only when no vendor carries it; otherwise switch it off."""

    row = db.query(ProductCategory).filter(ProductCategory.id == category_id).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Product category not found.")

    wanted = row.name.lower()
    used = sum(
        1
        for (categories,) in db.query(Vendor.product_categories).all()
        if any(str(c).strip().lower() == wanted for c in (categories or []))
    )
    if used:
        raise HTTPException(
            status_code=409,
            detail=f"{used} vendor(s) supply {row.name}, so it cannot be removed. Switch it off instead.",
        )

    db.delete(row)
    db.commit()


#: Set once the master has been filled, so a category deleted under
#: Masters is not added back on the next deploy.
SEEDED_MARKER = "product_categories_seeded"


def seed_from_file(db: Session) -> int:
    """Fill the master from the seed file, once. Names already there (in
    any case) are left as they are. After the first run it does nothing,
    so later deletions and renames under Masters stick."""

    from app.models.app_setting import AppSetting

    if db.query(AppSetting).filter(AppSetting.key == SEEDED_MARKER).first():
        return 0

    names = json.loads(SEED_FILE.read_text(encoding="utf-8"))
    have = {name.lower() for (name,) in db.query(ProductCategory.name).all()}
    added = 0

    for name in names:
        clean = " ".join(str(name).split())[:MAX_NAME]
        if clean and clean.lower() not in have:
            db.add(ProductCategory(name=clean, is_active=True))
            have.add(clean.lower())
            added += 1

    db.add(AppSetting(key=SEEDED_MARKER, value="1"))
    db.commit()
    return added
