from pydantic import BaseModel
from typing import Optional, List

class CreateMenuItemRequest(BaseModel):
    title: str
    icon: Optional[str] = None
    path: Optional[str] = None
    permission_key: Optional[str] = None
    parent_id: Optional[str] = None
    order_index: Optional[int] = 0
    is_active: Optional[bool] = True

class UpdateMenuItemRequest(BaseModel):
    title: Optional[str] = None
    icon: Optional[str] = None
    path: Optional[str] = None
    permission_key: Optional[str] = None
    parent_id: Optional[str] = None
    order_index: Optional[int] = None
    is_active: Optional[bool] = None

class MenuItemResponse(BaseModel):
    id: str
    title: str
    icon: Optional[str] = None
    path: Optional[str] = None
    permission_key: Optional[str] = None
    parent_id: Optional[str] = None
    order_index: int
    is_active: bool
    children: Optional[List["MenuItemResponse"]] = []
