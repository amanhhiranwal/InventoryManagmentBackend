import logging
import smtplib
from email.message import EmailMessage
from email.utils import formataddr

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
        to: str | list[str],
        subject: str,
        text_body: str,
        html_body: str | None = None,
        cc: list[str] | None = None,
        bcc: list[str] | None = None,
        attachments: list[dict] | None = None,
    ) -> bool:
        """Send one message.

        ``to`` accepts a single address or a list. ``cc``/``bcc`` are
        optional; BCC is passed to the server as an envelope recipient only
        and deliberately never written into a header, so blind copies stay
        blind. ``attachments`` take ``{filename, content (bytes), mime_type}``.
        """

        recipients = [to] if isinstance(to, str) else list(to or [])
        cc = list(cc or [])
        bcc = list(bcc or [])

        if not recipients:
            logger.warning("Email not sent: no recipients. Subject: %s", subject)
            return False

        if not settings.SMTP_HOST:
            logger.warning(
                "SMTP is not configured. Email to %s not sent.\nSubject: %s\n%s",
                ", ".join(recipients),
                subject,
                text_body,
            )
            return False

        sender = settings.SMTP_FROM or settings.SMTP_USER

        message = EmailMessage()
        message["Subject"] = subject
        # Give the mailbox a readable display name so recipients see
        # "Synergy CRM Portal" rather than the raw SMTP account.
        message["From"] = (
            formataddr((settings.SMTP_FROM_NAME, sender))
            if settings.SMTP_FROM_NAME
            else sender
        )
        message["To"] = ", ".join(recipients)

        if cc:
            message["Cc"] = ", ".join(cc)

        message.set_content(text_body)

        if html_body:
            message.add_alternative(html_body, subtype="html")

        for attachment in attachments or []:
            content = attachment.get("content")

            if not content:
                continue

            mime_type = attachment.get("mime_type") or "application/octet-stream"
            maintype, _, subtype = mime_type.partition("/")

            message.add_attachment(
                content,
                maintype=maintype or "application",
                subtype=subtype or "octet-stream",
                filename=attachment.get("filename") or "attachment",
            )

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

                # Passing the envelope explicitly is what delivers BCC
                # without the addresses appearing in any header.
                server.send_message(
                    message,
                    to_addrs=recipients + cc + bcc,
                )

            return True

        except Exception:
            logger.exception(
                "Failed to send email to %s",
                ", ".join(recipients),
            )
            return False
