from typing import Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class LocationBase(BaseModel):

    company_id: UUID

    location_name: str = Field(
        ...,
        min_length=2,
        max_length=255,
    )

    location_code: str = Field(
        ...,
        min_length=2,
        max_length=50,
    )

    email: Optional[EmailStr] = None

    phone_number: Optional[str] = Field(
        default=None,
        max_length=20,
    )

    address_line_1: Optional[str] = Field(
        default=None,
        max_length=255,
    )

    address_line_2: Optional[str] = Field(
        default=None,
        max_length=255,
    )

    city: Optional[str] = Field(
        default=None,
        max_length=100,
    )

    state: Optional[str] = Field(
        default=None,
        max_length=100,
    )

    country: Optional[str] = Field(
        default=None,
        max_length=100,
    )

    postal_code: Optional[str] = Field(
        default=None,
        max_length=20,
    )

    is_default: bool = False


class LocationCreate(LocationBase):
    pass


class LocationUpdate(BaseModel):

    location_name: Optional[str] = None

    email: Optional[EmailStr] = None

    phone_number: Optional[str] = None

    address_line_1: Optional[str] = None

    address_line_2: Optional[str] = None

    city: Optional[str] = None

    state: Optional[str] = None

    country: Optional[str] = None

    postal_code: Optional[str] = None

    is_default: Optional[bool] = None

    is_active: Optional[bool] = None


class LocationResponse(LocationBase):

    id: UUID

    is_active: bool

    model_config = ConfigDict(
        from_attributes=True,
    )