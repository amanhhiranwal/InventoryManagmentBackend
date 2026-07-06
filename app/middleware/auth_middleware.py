from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from fastapi.security import HTTPBearer

from app.services.jwt_service import JWTService

security = HTTPBearer()


def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
):

    token = credentials.credentials

    payload = JWTService.verify_token(token)

    if payload is None:

        raise HTTPException(
            status_code=401,
            detail="Invalid Token",
        )

    return payload
