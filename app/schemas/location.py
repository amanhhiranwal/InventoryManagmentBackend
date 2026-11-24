from typing import Literal, Optional
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

    #: WAREHOUSE holds stock and gets bins; OFFICE does not.
    location_type: Literal["WAREHOUSE", "OFFICE"] = "WAREHOUSE"


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

    location_type: Optional[Literal["WAREHOUSE", "OFFICE"]] = None


class BinCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    code: str = Field(..., min_length=1, max_length=40, pattern=r"^[A-Za-z0-9][A-Za-z0-9_\-/]*$")
    name: str = Field(..., min_length=1, max_length=120)
    bin_type: Literal["STORE", "QC", "REJECT"] = "STORE"
    zone: Optional[str] = Field(default=None, max_length=60)
    rack: Optional[str] = Field(default=None, max_length=60)
    shelf: Optional[str] = Field(default=None, max_length=60)


class BinUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    code: Optional[str] = Field(default=None, min_length=1, max_length=40, pattern=r"^[A-Za-z0-9][A-Za-z0-9_\-/]*$")
    name: Optional[str] = Field(default=None, min_length=1, max_length=120)
    bin_type: Optional[Literal["STORE", "QC", "REJECT"]] = None
    zone: Optional[str] = Field(default=None, max_length=60)
    rack: Optional[str] = Field(default=None, max_length=60)
    shelf: Optional[str] = Field(default=None, max_length=60)
    is_active: Optional[bool] = None


class LocationResponse(LocationBase):

    id: UUID

    is_active: bool

    model_config = ConfigDict(
        from_attributes=True,
    )