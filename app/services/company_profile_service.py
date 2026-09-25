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
