"""Telling somebody their account exists, and what to sign in with.

Two letters, one shape: here is your email address, here is a password
that works once, change it when you arrive. They are the only place a
temporary password is ever written down - it is not returned by the API
and not shown on the screen that triggered it, because an administrator
reading it off their monitor is how it ends up on a sticky note.
"""

import logging

from sqlalchemy.orm import Session

from app.services.company_profile_service import app_url
from app.services.email_service import EmailService
from app.services.email_templates import EmailLetter

logger = logging.getLogger(__name__)


def _send(
    user,
    db: Session,
    *,
    heading: str,
    opening: str,
    password: str,
) -> bool:
    """The letter itself. Never allowed to break what it is reporting."""

    sign_in = app_url("/login", db)

    try:
        letter = EmailLetter(
            db,
            heading=heading,
            greeting=f"Dear {user.first_name},",
            paragraphs=[
                opening,
                "Sign in with the details below. You will be asked to "
                "choose your own password straight away - the one here "
                "stops working the moment you do.",
            ],
            facts=[
                ("Sign in at", sign_in),
                ("Email address", user.email),
                ("Temporary password", password),
            ],
            action=("Sign in", sign_in),
            note=(
                "If you were not expecting this, tell whoever administers "
                "the CRM. Nobody from Synergy will ask you for this "
                "password."
            ),
        )

        return EmailService.send(
            to=user.email,
            subject=heading,
            text_body=letter.text(),
            html_body=letter.html(),
        )
    except Exception:  # noqa: BLE001 - the account is already made
        logger.exception("Could not send account mail to %s", user.email)

        return False


def send_welcome(user, password: str, db: Session) -> bool:
    """A new account, with the password to get into it the first time."""

    return _send(
        user,
        db,
        heading="Your Synergy CRM account is ready",
        opening=(
            "An account has been created for you on the Synergy CRM."
        ),
        password=password,
    )


def send_reset(user, password: str, db: Session) -> bool:
    """A password somebody reset on their behalf."""

    return _send(
        user,
        db,
        heading="Your Synergy CRM password has been reset",
        opening=(
            "Your password has been reset, so the one you were using no "
            "longer works."
        ),
        password=password,
    )
