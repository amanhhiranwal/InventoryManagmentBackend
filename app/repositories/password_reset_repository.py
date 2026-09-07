import uuid
from datetime import datetime

from sqlalchemy.orm import Session

from app.models.password_reset_token import PasswordResetToken


class PasswordResetRepository:

    @staticmethod
    def invalidate_user_tokens(
        db: Session,
        user_id: uuid.UUID,
    ):
        """Mark every still-usable token of a user as consumed."""

        (
            db.query(PasswordResetToken)
            .filter(
                PasswordResetToken.user_id == user_id,
                PasswordResetToken.used_at.is_(None),
            )
            .update(
                {PasswordResetToken.used_at: datetime.utcnow()},
                synchronize_session=False,
            )
        )

        db.commit()

    @staticmethod
    def create_token(
        db: Session,
        user_id: uuid.UUID,
        token_hash: str,
        expires_at: datetime,
    ):
        token = PasswordResetToken(
            user_id=user_id,
            token_hash=token_hash,
            expires_at=expires_at,
        )

        db.add(token)
        db.commit()
        db.refresh(token)

        return token

    @staticmethod
    def get_valid_token(
        db: Session,
        token_hash: str,
    ):
        return (
            db.query(PasswordResetToken)
            .filter(
                PasswordResetToken.token_hash == token_hash,
                PasswordResetToken.used_at.is_(None),
                PasswordResetToken.expires_at > datetime.utcnow(),
            )
            .first()
        )

    @staticmethod
    def mark_used(
        db: Session,
        token: PasswordResetToken,
    ):
        token.used_at = datetime.utcnow()

        db.add(token)
        db.commit()
        db.refresh(token)

        return token
