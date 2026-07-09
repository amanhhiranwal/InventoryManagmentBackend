from typing import Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr


class CreateCompanyRequest(BaseModel):
    company_name: str
    company_code: str
    email: EmailStr
    phone_number: str
    website: Optional[str] = None
    gst_number: Optional[str] = None
    address_line_1: str
    address_line_2: Optional[str] = None
    city: str
    state: str
    country: str
    postal_code: str
    is_active: bool = True


class UpdateCompanyRequest(BaseModel):
    company_name: Optional[str] = None
    company_code: Optional[str] = None
    email: Optional[EmailStr] = None
    phone_number: Optional[str] = None
    website: Optional[str] = None
    gst_number: Optional[str] = None
    address_line_1: Optional[str] = None
    address_line_2: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    country: Optional[str] = None
    postal_code: Optional[str] = None
    is_active: Optional[bool] = None


class CompanyResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    company_name: str
    company_code: str
    email: EmailStr
    phone_number: str
    website: Optional[str]
    gst_number: Optional[str]
    address_line_1: str
    address_line_2: Optional[str]
    city: str
    state: str
    country: str
    postal_code: str
    is_active: bool