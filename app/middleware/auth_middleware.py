from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.database.dependencies import get_db
from app.models.user import User
from app.services.jwt_service import JWTService

security = HTTPBearer()


#: What an account on a temporary password may still reach: the two
#: things it needs to stop being on one, and signing out. Everything
#: else waits until the password has been changed - an emailed password
#: has been through a mail server and somebody's inbox, and is not a
#: credential to work under.
WHILE_PASSWORD_IS_TEMPORARY = (
    "/auth/change-password",
    "/auth/me",
    "/auth/logout",
)


def get_current_user(
    request: Request,
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

        # Signed in before the account's sessions were ended (a welcome
        # email with a new password does that). A token from before this
        # check existed carries no version and reads as 0. Only older
        # tokens are refused: a service whose copy of the user missed the
        # bump must not lock out the sign-in that came after it.
        if int(payload.get("sv") or 0) < int(user.session_version or 0):
            raise HTTPException(
                status_code=401,
                detail="You have been signed out. Sign in again.",
            )

        if user.must_change_password and not str(
            request.url.path
        ).endswith(WHILE_PASSWORD_IS_TEMPORARY):
            raise HTTPException(
                status_code=403,
                detail=(
                    "Choose your own password before using the CRM. The "
                    "one you signed in with was emailed to you and is "
                    "temporary."
                ),
            )

    return payload
