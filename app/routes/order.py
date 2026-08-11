from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional, List
from sqlalchemy.orm import Session
from bson import ObjectId
import datetime

from app.database.mongodb import sync_mongo_db
from app.database.dependencies import get_db
from app.middleware.auth_middleware import get_current_user
from app.services.lead_service import get_visible_creator_user_ids

router = APIRouter(
    prefix="/orders",
    tags=["Orders"],
)


class OrderItem(BaseModel):
    product_id: str
    description: str
    rate: float
    quantity_case: float
    quantity_kg_ltr: float
    price: float


class CreateOrderRequest(BaseModel):
    customer_name: str
    aging_0_30: Optional[float] = 0.0
    aging_31_60: Optional[float] = 0.0
    aging_61_90: Optional[float] = 0.0
    aging_91_120: Optional[float] = 0.0
    aging_121_180: Optional[float] = 0.0
    aging_above_180: Optional[float] = 0.0
    items: List[OrderItem]
    total_amount: float
    gst_amount: float
    grand_total: float


@router.post("")
def create_order(
    request: CreateOrderRequest,
    current_user=Depends(get_current_user),
):
    orders_col = sync_mongo_db["sales_orders"]
    doc = request.dict()
    doc["created_at"] = datetime.datetime.utcnow().isoformat()

    first_name = current_user.get("first_name", "")
    last_name = current_user.get("last_name", "")
    creator_name = f"{first_name} {last_name}".strip() or "User"

    doc["creator_id"] = current_user.get("user_id")
    doc["creator_name"] = creator_name

    orders_col.insert_one(doc)
    doc["_id"] = str(doc["_id"])
    return {
        "success": True,
        "message": "Order created successfully.",
        "data": doc
    }


@router.get("")
def get_orders(
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    orders_col = sync_mongo_db["sales_orders"]
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

    cursor = orders_col.find(query).sort("_id", -1)
    orders = []
    for doc in cursor:
        doc["_id"] = str(doc["_id"])
        orders.append(doc)
    return {
        "success": True,
        "data": orders
    }


@router.delete("/{order_id}")
def delete_order(
    order_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    orders_col = sync_mongo_db["sales_orders"]
    try:
        obj_id = ObjectId(order_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid Order ID format.")

    doc = orders_col.find_one({"_id": obj_id})
    if not doc:
        raise HTTPException(status_code=404, detail="Order not found.")

    visible_user_ids = get_visible_creator_user_ids(current_user, db)
    if visible_user_ids:
        target_creator = doc.get("creator_id")
        if target_creator and target_creator not in visible_user_ids:
            raise HTTPException(status_code=403, detail="Permission denied. You can only delete orders created by yourself or your subordinates.")

    orders_col.delete_one({"_id": obj_id})
    return {
        "success": True,
        "message": "Order deleted successfully."
    }
