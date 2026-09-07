import logging
import smtplib
from email.message import EmailMessage

from app.core.config import settings

logger = logging.getLogger(__name__)


class EmailService:
    """Minimal SMTP sender.

    When SMTP_HOST is not configured the message is written to the
    application log instead, so the password reset flow stays testable
    in local development without a mail server.
    """

    @staticmethod
    def send(
        to: str,
        subject: str,
        text_body: str,
        html_body: str | None = None,
    ) -> bool:

        if not settings.SMTP_HOST:
            logger.warning(
                "SMTP is not configured. Email to %s not sent.\nSubject: %s\n%s",
                to,
                subject,
                text_body,
            )
            return False

        message = EmailMessage()
        message["Subject"] = subject
        message["From"] = settings.SMTP_FROM or settings.SMTP_USER
        message["To"] = to
        message.set_content(text_body)

        if html_body:
            message.add_alternative(html_body, subtype="html")

        try:
            if settings.SMTP_SSL:
                server = smtplib.SMTP_SSL(
                    settings.SMTP_HOST,
                    settings.SMTP_PORT,
                    timeout=15,
                )
            else:
                server = smtplib.SMTP(
                    settings.SMTP_HOST,
                    settings.SMTP_PORT,
                    timeout=15,
                )

            with server:
                if settings.SMTP_TLS and not settings.SMTP_SSL:
                    server.starttls()

                if settings.SMTP_USER:
                    server.login(
                        settings.SMTP_USER,
                        settings.SMTP_PASSWORD,
                    )

                server.send_message(message)

            return True

        except Exception:
            logger.exception("Failed to send email to %s", to)
            return False
