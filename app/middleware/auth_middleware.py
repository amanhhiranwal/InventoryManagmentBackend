from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.database.dependencies import get_db
from app.models.user import User
from app.services.jwt_service import JWTService

security = HTTPBearer()


def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: Session = Depends(get_db),
):

    token = credentials.credentials

    payload = JWTService.verify_token(token)

    if payload is None:

        raise HTTPException(
            status_code=401,
            detail="Invalid Token",
        )

    # A token already issued outlives the account it was issued for, so
    # deactivating somebody while they are signed in left them working
    # until their token expired. The flag is checked on every request
    # instead: the next thing they click signs them out.
    #
    # One lookup per authenticated request is the price of that. The
    # alternative is a token blacklist, which is the same lookup with
    # somewhere else to keep it.
    user_id = payload.get("user_id")

    if user_id:
        user = db.query(User).filter(User.id == user_id).first()

        if user is None or not user.is_active:
            raise HTTPException(
                status_code=401,
                detail="This account has been deactivated. Sign in again.",
            )

    return payload
