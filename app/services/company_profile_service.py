"""The seller's own identity: who the proposals and emails come from.

Held in the database so a super admin edits it on the Company Profile
screen rather than in a .env file nobody outside the deployment can touch.
Every field falls back to its environment variable, so an installation
that has never opened the screen still prints and emails sensibly.
"""

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.app_setting import AppSetting

#: setting key -> the environment variable standing behind it.
FIELDS: dict[str, str] = {
    "company_legal_name": "COMPANY_LEGAL_NAME",
    "company_address": "COMPANY_ADDRESS",
    "company_gstin": "COMPANY_GSTIN",
    "company_website": "COMPANY_WEBSITE",
    "company_email": "SMTP_FROM",
    "company_phone": "COMPANY_PHONE",
    "company_about": "COMPANY_ABOUT",
    "company_offerings": "COMPANY_OFFERINGS",
    "company_logo_path": "COMPANY_LOGO_PATH",
    "company_cover_image": "COMPANY_COVER_IMAGE",
    "signatory_name": "SIGNATORY_NAME",
    "signatory_title": "SIGNATORY_TITLE",

    # Where this CRM answers, for the links in the emails it sends. Held
    # here rather than only in the environment so moving the site does
    # not need a deployment to stop the approval emails pointing at
    # somebody's laptop.
    "app_base_url": "FRONTEND_URL",

    # Where the money is to be sent. On the profile rather than printed
    # into the invoice template, because an account number changes and a
    # proforma invoice carrying the old one is how a payment goes astray.
    "company_state_name": "COMPANY_STATE_NAME",
    "company_state_code": "COMPANY_STATE_CODE",
    "bank_account_name": "BANK_BENEFICIARY_NAME",
    "bank_name": "BANK_NAME",
    "bank_account_number": "BANK_ACCOUNT_NUMBER",
    "bank_branch": "BANK_BRANCH",
    "bank_ifsc": "BANK_IFSC",
    "bank_swift": "BANK_SWIFT",
    "upi_vpa": "BANK_UPI_VPA",
    "upi_qr_path": "UPI_QR_PATH",
}

#: Written as one block and split on "|" or newlines when read.
MULTILINE = ("company_address", "company_about", "company_offerings")


class CompanyProfileService:

    @staticmethod
    def raw(db: Session | None) -> dict[str, str]:
        """Every field as a plain string, database first then environment."""

        stored: dict[str, str] = {}

        if db is not None:
            try:
                stored = {
                    row.key: (row.value or "")
                    for row in db.query(AppSetting).all()
                }
            except Exception:  # pragma: no cover - a fresh install has no table
                stored = {}

        return {
            key: (stored.get(key) or getattr(settings, env, "") or "").strip()
            for key, env in FIELDS.items()
        }

    @staticmethod
    def save(values: dict, db: Session) -> dict[str, str]:
        """Store the fields given and leave the rest alone."""

        for key in FIELDS:
            if key not in values:
                continue

            value = values.get(key)
            text = "" if value is None else str(value)

            row = db.query(AppSetting).filter(AppSetting.key == key).first()

            if row is None:
                db.add(AppSetting(key=key, value=text))
            else:
                row.value = text

        db.commit()

        return CompanyProfileService.raw(db)

    @staticmethod
    def save_raw(key: str, value: str, db: Session) -> None:
        """Store one setting that is not part of the company profile.

        The approval bands live here too: it is the same table, and they
        are set on the same kind of Masters screen.
        """

        row = db.query(AppSetting).filter(AppSetting.key == key).first()

        if row is None:
            db.add(AppSetting(key=key, value=value))
        else:
            row.value = value

        db.commit()

    @staticmethod
    def as_lists(db: Session | None) -> dict:
        """The profile with the multi-line fields already split."""

        raw = CompanyProfileService.raw(db)

        def pieces(key: str) -> list[str]:
            return [
                piece.strip()
                for piece in raw[key].replace("|", "\n").splitlines()
                if piece.strip()
            ]

        return {
            **raw,
            "company_address_lines": pieces("company_address"),
            "company_about_paragraphs": pieces("company_about"),
            "company_offering_list": pieces("company_offerings"),
        }


def app_url(path: str, db=None) -> str:
    """A path on the CRM as a full link, for a button in an email.

    Read from the company profile first, where a super admin can change
    it, and only then from the environment. A link that points at
    localhost - or at the address the site used to answer on - reaches
    somebody who cannot act on it, and moving the site should not need a
    deployment to fix that.

    It lives here, beside the field it reads, because every email that
    carries a link has to agree on where the CRM is: an approval that
    says synergy-sync.com and a password reset that says somewhere else
    are the same bug twice.
    """

    base = ""

    if db is not None:
        try:
            base = (CompanyProfileService.raw(db).get("app_base_url") or "").strip()
        except Exception:  # noqa: BLE001 - a link is not worth failing a send
            base = ""

    if not base:
        base = settings.FRONTEND_URL or ""

    base = base.rstrip("/")

    return f"{base}{path}" if base else path
