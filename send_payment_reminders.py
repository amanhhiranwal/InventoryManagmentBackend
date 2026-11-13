"""Remind both teams about PO payments that are due, every day until paid.

Run by cron. Each unpaid instalment of an approved PO that falls due within
3 days, or is already overdue, puts one notification a day in the bell of
accounts (purchase_order.pay), procurement (purchase_order.manage) and
whoever raised the PO, saying where it stands. Running it again on the
same day sends nothing new.

    docker exec -w /app backend_app python send_payment_reminders.py

In production the deploy (.github/workflows/deploy.yml) installs it in the
deploy user's crontab to run hourly in the saas_backend container, logging
to /var/www/saas/logs/payment_reminders.log. Hourly is safe: an instalment
is reminded at most once a day.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.database.postgres import SessionLocal  # noqa: E402
import app.models  # noqa: E402,F401  Registers every model
from app.services.procurement.payment_calendar import send_reminders  # noqa: E402


def main() -> int:
    db = SessionLocal()
    try:
        count = send_reminders(db)
    finally:
        db.close()
    print(f"Payment reminders sent for {count} instalment(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
