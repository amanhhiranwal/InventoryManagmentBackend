import hashlib
import logging
import secrets
from datetime import datetime, timedelta
from urllib.parse import quote, urlencode

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.core.config import settings
from app.repositories.auth_repository import AuthRepository
from app.repositories.password_reset_repository import PasswordResetRepository
from app.services.email_service import EmailService
from app.services.password_service import PasswordService

logger = logging.getLogger(__name__)


class PasswordResetService:

    @staticmethod
    def _hash_token(raw_token: str) -> str:
        return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()

    @staticmethod
    def _build_reset_link(raw_token: str, email: str) -> str:
        query = urlencode(
            {
                "token": raw_token,
                "email": email,
            },
            quote_via=quote,
        )

        return f"{settings.FRONTEND_URL.rstrip('/')}/reset-password?{query}"

    @staticmethod
    def forgot_password(email: str, db: Session):
        """Issue a reset link.

        Always reports success so the endpoint cannot be used to discover
        which email addresses have an account.
        """

        user = AuthRepository.get_user_by_email(db, email)

        if user is None or not user.is_active:
            logger.info(
                "Password reset requested for unknown or inactive account: %s",
                email,
            )
            return None

        # Any link sent earlier stops working once a new one is issued.
        PasswordResetRepository.invalidate_user_tokens(db, user.id)

        raw_token = secrets.token_urlsafe(48)

        PasswordResetRepository.create_token(
            db,
            user_id=user.id,
            token_hash=PasswordResetService._hash_token(raw_token),
            expires_at=datetime.utcnow()
            + timedelta(minutes=settings.PASSWORD_RESET_TOKEN_EXPIRE_MINUTES),
        )

        reset_link = PasswordResetService._build_reset_link(
            raw_token,
            user.email,
        )

        minutes = settings.PASSWORD_RESET_TOKEN_EXPIRE_MINUTES

        EmailService.send(
            to=user.email,
            subject="Reset your password",
            text_body=(
                f"Hi {user.first_name},\n\n"
                "We received a request to reset your password.\n"
                f"Open the link below to choose a new one. It expires in {minutes} minutes.\n\n"
                f"{reset_link}\n\n"
                "If you did not request this, you can safely ignore this email."
            ),
            html_body=(
                f"<p>Hi {user.first_name},</p>"
                "<p>We received a request to reset your password.</p>"
                f'<p><a href="{reset_link}">Reset your password</a></p>'
                f"<p>This link expires in {minutes} minutes.</p>"
                "<p>If you did not request this, you can safely ignore this email.</p>"
            ),
        )

        return reset_link

    @staticmethod
    def reset_password(
        token: str,
        new_password: str,
        db: Session,
        email: str | None = None,
    ):

        reset_token = PasswordResetRepository.get_valid_token(
            db,
            PasswordResetService._hash_token(token),
        )

        if reset_token is None:
            raise HTTPException(
                status_code=400,
                detail="This reset link is invalid or has expired. Please request a new one.",
            )

        user = AuthRepository.get_user_by_id(db, reset_token.user_id)

        if user is None or not user.is_active:
            raise HTTPException(
                status_code=400,
                detail="This reset link is invalid or has expired. Please request a new one.",
            )

        if email and email.strip().lower() != user.email.lower():
            raise HTTPException(
                status_code=400,
                detail="This reset link does not belong to that email address.",
            )

        user.password = PasswordService.hash_password(new_password)
        db.add(user)
        db.commit()

        PasswordResetRepository.mark_used(db, reset_token)

        return user
