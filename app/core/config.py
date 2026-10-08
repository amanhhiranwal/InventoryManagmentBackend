from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    APP_NAME: str
    APP_VERSION: str
    ENV: str

    POSTGRES_HOST: str
    POSTGRES_PORT: int
    POSTGRES_DB: str
    POSTGRES_USER: str
    POSTGRES_PASSWORD: str

    #: Used only by sync_schema.py, and only for schema changes. The
    #: application's own user needs no rights beyond reading and writing;
    #: adding a column needs ownership of the table, which is a different
    #: and much larger thing to hand the running process.
    #:
    #: Leave unset and the schema sync uses the application's connection,
    #: which works wherever that user already owns the tables.
    POSTGRES_ADMIN_USER: str | None = None
    POSTGRES_ADMIN_PASSWORD: str | None = None

    MONGO_URI: str
    MONGO_DB: str

    REDIS_HOST: str
    REDIS_PORT: int

    JWT_SECRET_KEY: str
    JWT_ALGORITHM: str
    ACCESS_TOKEN_EXPIRE_MINUTES: int

    # -----------------------------
    # Password reset / outbound email
    # -----------------------------
    FRONTEND_URL: str = "http://localhost:3000"

    PASSWORD_RESET_TOKEN_EXPIRE_MINUTES: int = 30

    SMTP_HOST: str = ""
    SMTP_PORT: int = 587
    SMTP_USER: str = ""
    SMTP_PASSWORD: str = ""
    SMTP_FROM: str = ""
    #: Display name on outbound mail. Recipients see this rather than the
    #: raw mailbox, and it is what the Send Quotation dialog shows as the
    #: sender - mail always leaves through this one SMTP account, so the
    #: sender is not per-user and is not selectable in the UI.
    SMTP_FROM_NAME: str = "Synergy CRM Portal"
    SMTP_TLS: bool = True
    SMTP_SSL: bool = False

    # -----------------------------
    # Seller identity printed on proforma invoices
    # -----------------------------
    #: Deliberately blank by default. These are what a customer pays into, so
    #: a placeholder account number that survived into a real invoice would
    #: send money to the wrong place; an unset value shows as "Not configured"
    #: instead.
    COMPANY_LEGAL_NAME: str = "SYNERGY GLOBAL PRIVATE LIMITED"
    #: Multi-line address; separate lines with "|" in .env.
    COMPANY_ADDRESS: str = (
        "B-66 Sector 65, Noida, Gautambuddha Nagar|Uttar Pradesh 201301"
    )
    COMPANY_GSTIN: str = "09AAACQ7899Q1Z6"
    BANK_BENEFICIARY_NAME: str = ""
    BANK_NAME: str = ""
    BANK_BRANCH: str = ""
    BANK_ACCOUNT_NUMBER: str = ""
    BANK_IFSC: str = ""
    BANK_UPI_VPA: str = ""
    #: The "About us" paragraph on the quotation proposal PDF. Unset falls
    #: back to a neutral description rather than leaving the section blank.
    COMPANY_ABOUT: str = ""
    #: Printed in the footer bar on every page of the proposal.
    COMPANY_WEBSITE: str = ""
    #: What the company sells, one per line or separated with "|". Listed
    #: on the proposal's About page.
    COMPANY_OFFERINGS: str = ""
    #: Brand logo for the proposal. Relative paths resolve against the
    #: backend root; unset uses the bundled app/assets/brand-logo.jpg.
    COMPANY_LOGO_PATH: str = ""
    #: Optional picture for the proposal cover, under the addresses. Unset
    #: simply leaves the cover without one.
    COMPANY_COVER_IMAGE: str = ""

    #: The seller's state. Its code decides whether a sale is taxed as
    #: CGST + SGST or as IGST, so it is not decoration.
    COMPANY_STATE_NAME: str = "Uttar Pradesh"
    COMPANY_STATE_CODE: str = "09"

    #: The rest of what a remittance needs. The account itself is already
    #: declared above as BANK_*; these only add what a GST document asks
    #: for and the payment screen shows.
    BANK_SWIFT: str = ""
    #: A QR image for BANK_UPI_VPA, relative to the backend root.
    UPI_QR_PATH: str = ""
    #: Contact number printed on the proposal's signature block.
    COMPANY_PHONE: str = ""
    #: Who signs the invoice. Falls back to the invoice's creator when unset.
    SIGNATORY_NAME: str = ""
    SIGNATORY_TITLE: str = ""

    BACKEND_CORS_ORIGINS: list[str] = [
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:8000",
        "http://127.0.0.1:8000",
        "http://synergy-sync.com",
        "https://synergy-sync.com",
        "http://www.synergy-sync.com",
        "https://www.synergy-sync.com",
        "http://crm.synergy-global.io",
        "https://crm.synergy-global.io",
        "http://45.195.159.94",
        "https://45.195.159.94",
    ]

    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore"
    )


settings = Settings()