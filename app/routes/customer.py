from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional, Any
from sqlalchemy.orm import Session
from bson import ObjectId

from app.database.mongodb import sync_mongo_db
from app.database.dependencies import get_db
from app.middleware.auth_middleware import get_current_user
from app.services.lead_service import get_visible_creator_user_ids

router = APIRouter(prefix="/customers", tags=["Customers Master"])


class CustomerCreate(BaseModel):
    name: Optional[str] = None
    customer_name: Optional[str] = None
    email: Optional[str] = ""
    phone: Optional[str] = ""
    address: Optional[str] = ""
    gst: Optional[str] = ""
    pan: Optional[str] = ""
    category: Optional[str] = "Agriculture"
    isRegistered: Optional[bool] = True
    kycDocs: Optional[list] = []


@router.get("")
def get_customers(
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    col = sync_mongo_db["customers"]
    visible_user_ids = get_visible_creator_user_ids(current_user, db)

    query = {}
    if visible_user_ids:
        query = {
            "$or": [
                {"creator_id": {"$in": visible_user_ids}},
                {"creator_id": {"$exists": False}},
                {"creator_id": None}
            ]
        }

    cursor = col.find(query).sort("_id", -1)
    data = []
    for doc in cursor:
        doc["id"] = str(doc["_id"])
        doc["_id"] = str(doc["_id"])
        data.append(doc)
    return {"success": True, "data": data}


@router.post("")
def create_customer(
    request: CustomerCreate,
    current_user=Depends(get_current_user),
):
    col = sync_mongo_db["customers"]
    doc = request.dict()
    cust_name = request.name or request.customer_name or "Unnamed Customer"
    doc["name"] = cust_name
    doc["customer_name"] = cust_name

    if col.find_one({"name": cust_name}):
        raise HTTPException(status_code=400, detail=f"Customer with name '{cust_name}' already exists.")

    first_name = current_user.get("first_name", "")
    last_name = current_user.get("last_name", "")
    creator_name = f"{first_name} {last_name}".strip() or "User"

    doc["creator_id"] = current_user.get("user_id")
    doc["creator_name"] = creator_name

    col.insert_one(doc)
    doc["id"] = str(doc["_id"])
    doc["_id"] = str(doc["_id"])
    return {"success": True, "message": "Customer created successfully.", "data": doc}


@router.delete("/{customer_id}")
def delete_customer(
    customer_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    col = sync_mongo_db["customers"]
    try:
        obj_id = ObjectId(customer_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid customer ID format.")

    doc = col.find_one({"_id": obj_id})
    if not doc:
        raise HTTPException(status_code=404, detail="Customer not found.")

    visible_user_ids = get_visible_creator_user_ids(current_user, db)
    if visible_user_ids:
        target_creator = doc.get("creator_id")
        if target_creator and target_creator not in visible_user_ids:
            raise HTTPException(status_code=403, detail="Permission denied. You can only delete customers created by yourself or your subordinates.")

    col.delete_one({"_id": obj_id})
    return {"success": True, "message": "Customer deleted successfully."}
