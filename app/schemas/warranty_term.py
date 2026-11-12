from typing import Optional

from pydantic import BaseModel, Field


class CreateWarrantyTermRequest(BaseModel):
    name: str
    years: int = 0
    is_default: bool = False
    #: Rupees per unit, before GST.
    price: float = Field(default=0.0, ge=0)
    description: Optional[str] = None


class UpdateWarrantyTermRequest(BaseModel):
    name: Optional[str] = None
    years: Optional[int] = None
    is_default: Optional[bool] = None
    price: Optional[float] = Field(default=None, ge=0)
    description: Optional[str] = None
    is_active: Optional[bool] = None


class WarrantyTermResponse(BaseModel):
    id: str
    name: str
    years: int
    is_default: bool
    description: Optional[str] = None
    is_active: bool

    class Config:
        from_attributes = True
