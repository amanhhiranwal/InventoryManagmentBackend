from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.database.mongodb import sync_mongo_db
from app.middleware.permission_middleware import require_super_admin

router = APIRouter(prefix="/inventory/units", tags=["Units Master"])

class UnitCreate(BaseModel):
    name: str

@router.get("")
def get_units():
    units_col = sync_mongo_db["inventory_units"]
    units = list(units_col.find())
    if not units:
        # Auto-seed standard units
        defaults = ["Kg", "Ltr", "Box", "Unit", "Pack", "Bag", "Bottle"]
        for name in defaults:
            units_col.insert_one({"name": name})
        units = list(units_col.find())
    return {"success": True, "data": [u["name"] for u in units]}

# Adding and removing units is a Masters change, so super admin only.
@router.post("", dependencies=[Depends(require_super_admin)])
def add_unit(request: UnitCreate):
    units_col = sync_mongo_db["inventory_units"]
    name = request.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Unit name cannot be empty.")
    if units_col.find_one({"name": name}):
        raise HTTPException(status_code=400, detail="Unit already exists.")
    units_col.insert_one({"name": name})
    return {"success": True, "message": "Unit created successfully."}

@router.delete("/{name}", dependencies=[Depends(require_super_admin)])
def delete_unit(name: str):
    units_col = sync_mongo_db["inventory_units"]
    if not units_col.find_one({"name": name}):
        raise HTTPException(status_code=404, detail="Unit not found.")
    units_col.delete_one({"name": name})
    return {"success": True, "message": "Unit deleted successfully."}
