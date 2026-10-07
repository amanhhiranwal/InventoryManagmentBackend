from typing import Literal, Optional

from pydantic import BaseModel


class CreateWarrantyTermRequest(BaseModel):
    name: str
    years: int = 0
    rate_mode: Literal["PERCENT", "AMOUNT"] = "PERCENT"
    rate: float = 0.0
    is_default: bool = False
    description: Optional[str] = None


class UpdateWarrantyTermRequest(BaseModel):
    name: Optional[str] = None
    years: Optional[int] = None
    rate_mode: Optional[Literal["PERCENT", "AMOUNT"]] = None
    rate: Optional[float] = None
    is_default: Optional[bool] = None
    description: Optional[str] = None
    is_active: Optional[bool] = None


class WarrantyTermResponse(BaseModel):
    id: str
    name: str
    years: int
    rate_mode: str
    rate: float
    is_default: bool
    description: Optional[str] = None
    is_active: bool

    class Config:
        from_attributes = True
