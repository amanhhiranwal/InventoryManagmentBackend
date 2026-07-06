from pydantic import BaseModel, EmailStr, Field


class RegisterSuperAdminRequest(BaseModel):

    first_name: str = Field(..., min_length=2)

    last_name: str = Field(..., min_length=2)

    email: EmailStr

    password: str = Field(..., min_length=8)

    phone_number: str

    employee_id: str


class LoginRequest(BaseModel):

    email: EmailStr

    password: str


class TokenResponse(BaseModel):

    access_token: str

    token_type: str = "Bearer"


class UserResponse(BaseModel):

    id: str

    first_name: str

    last_name: str

    email: EmailStr

    role: str


class LoginResponse(BaseModel):

    access_token: str

    token_type: str

    user: UserResponse