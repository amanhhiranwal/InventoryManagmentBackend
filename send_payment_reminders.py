"""Remind accounts about PO payments that are due, every day until paid.

Run once a day by cron. Each unpaid instalment of an approved PO that falls
due within 3 days, or is already overdue, puts one notification in the
bell of everyone who records payments (purchase_order.pay) and of whoever
raised the PO. Running it twice on the same day sends nothing new.

    docker exec -w /app backend_app python send_payment_reminders.py

On the server, a crontab line such as (09:00 every day):

    0 9 * * * docker exec -w /app backend_app python send_payment_reminders.py >> /var/log/payment_reminders.log 2>&1
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
