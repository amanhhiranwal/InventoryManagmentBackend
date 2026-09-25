"""Shorten the demo accounts to one name in front of one domain.

The seeded team started on syn-crm-9f3a2@ and @synergy-demo.mailinator.com,
which nobody can type from memory. They are now just the role:

    superadmin@mailinator.com      am.north.1@mailinator.com
    ceo@mailinator.com             am.north.2@mailinator.com
    avp@mailinator.com             am.south.1@mailinator.com
    zh.north@mailinator.com        accounts@mailinator.com
    zh.south@mailinator.com        inventory@mailinator.com

Passwords are untouched. Safe to run again: an account already on the new
address is left alone, and one whose new address is taken by somebody else
is reported rather than overwritten.

Mailinator inboxes are public - anyone who knows the address can read it.
These are throwaway demo accounts, so nothing confidential should ever be
addressed to one of them.

    docker exec -w /app backend_app python rename_demo_emails.py
"""

import sys

from seed_sales_team import MAIL_DOMAIN

#: old address -> the name it becomes, in front of MAIL_DOMAIN.
RENAMES = {
    "syn-crm-9f3a2@mailinator.com": "superadmin",
    "ceo@synergy-demo.mailinator.com": "ceo",
    "avp@synergy-demo.mailinator.com": "avp",
    "zh.north@synergy-demo.mailinator.com": "zh.north",
    "zh.south@synergy-demo.mailinator.com": "zh.south",
    "am.north.1@synergy-demo.mailinator.com": "am.north.1",
    "am.north.2@synergy-demo.mailinator.com": "am.north.2",
    "am.south.1@synergy-demo.mailinator.com": "am.south.1",
    "accounts@synergy-demo.mailinator.com": "accounts",
    "inventory@synergy-demo.mailinator.com": "inventory",
}


def main() -> int:
    from app.database.postgres import SessionLocal
    from app.models.user import User

    db = SessionLocal()

    try:
        taken = {
            email
            for (email,) in db.query(User.email).all()
            if email
        }

        moved = 0

        for old, name in RENAMES.items():
            new = f"{name}@{MAIL_DOMAIN}"

            if new in taken and old not in taken:
                print(f"  {new} already in use - nothing to do")
                continue

            user = db.query(User).filter(User.email == old).first()

            if user is None:
                print(f"  {old} not found")
                continue

            if new in taken:
                print(f"  cannot move {old}: {new} belongs to somebody else")
                continue

            user.email = new
            taken.discard(old)
            taken.add(new)
            moved += 1

            print(f"  {old}  ->  {new}")

        db.commit()

        print(f"\n{moved} account{'' if moved == 1 else 's'} moved.\n")
        print("Sign in with:")

        for name in RENAMES.values():
            password = "password123" if name == "superadmin" else "Synergy@123"
            print(f"  {name}@{MAIL_DOMAIN:<20}  {password}")

        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
