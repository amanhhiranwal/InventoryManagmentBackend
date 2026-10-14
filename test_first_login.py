"""A new account signs in once, changes its password, and only then works.

An account is created with no password chosen for it, and the only
place the temporary one is ever written down is the letter to the
person it belongs to - not the response, not the screen that asked for
the account. Until it has been changed the account can do exactly two
things: sign in, and choose a new password.

The mailer is replaced, so nothing leaves the machine; what is checked
is what would have been in the envelope. The account is created in this
process for the same reason - a replacement here does nothing to the
server's mailer, and sending a real email to prove an email is sent is
not a test, it is a letter.

Run seed_sales_team.py first.

    docker exec -w /app backend_app python test_first_login.py
"""

import re
import sys
import uuid

import requests

sys.path.insert(0, "/app")

from app.services.email_service import EmailService  # noqa: E402
from seed_sales_team import BASE, api, login, rows  # noqa: E402

ADMIN = ("superadmin@mailinator.com", "password123")
TAG = uuid.uuid4().hex[:6]

sent: list[dict] = []


def capture(to, subject, text_body, html_body=None, **kw):
    sent.append({"to": to, "subject": subject, "body": text_body or ""})
    return True


EmailService.send = staticmethod(capture)

passed, failed = [], []


def check(name, ok, detail=""):
    (passed if ok else failed).append(name)
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"   [{detail}]" if not ok else ""))


created = []

try:
    admin = login(*ADMIN)

    print("\n1. An account is created without a password being chosen")

    roles = {r["role_name"]: r["id"] for r in rows(api("get", "/rbac/roles", admin))}
    email = f"first.login.{TAG}@mailinator.com"

    # Created in process rather than over HTTP: the mailer is replaced
    # here, and a replacement in this process does nothing to the
    # server's. Sending a real email to prove an email is sent is not a
    # test, it is a letter.
    from app.database.postgres import SessionLocal
    from app.services.user_service import UserService

    class NewUser:
        first_name = "First"
        last_name = "Login"
        phone_number = "9800000111"
        employee_id = f"EMP-FL-{TAG}"
        location = None
        role_ids = [roles["Area Manager"]]
        company_ids = []
        reports_to_id = None
        password = None

    NewUser.email = email

    session = SessionLocal()
    made = UserService.create(NewUser(), session, {"is_super_admin": True})

    check("it is created with no password field", made is not None)

    user_id = str(made.id)
    created.append(user_id)

    check("one email went out", len(sent) == 1, str(len(sent)))
    check("to the person, not the administrator", sent and sent[0]["to"] == email,
          str(sent[0]["to"] if sent else None))
    check("carrying their email address", email in sent[0]["body"])

    # The password is the only thing in that letter we cannot predict, so
    # it is read back out of it - which is also the proof it is there.
    def password_from(body: str) -> str | None:
        """The letter lays its facts out in columns, not with colons."""

        for line in body.splitlines():
            if "Temporary password" in line:
                return re.split(r"\s{2,}", line.strip())[-1].strip()

        return None

    temporary = password_from(sent[0]["body"])

    check("and a temporary password", bool(temporary), sent[0]["body"][:200])

    print("\n2. The password works once, and opens nothing")

    signed_in = requests.post(
        f"{BASE}/auth/login", json={"email": email, "password": temporary}, timeout=30
    )
    check("they can sign in with it", signed_in.status_code == 200,
          f"{signed_in.status_code} {signed_in.text[:140]}")

    body = signed_in.json()
    token = body.get("access_token")

    check(
        "and the response says a password is still to be chosen",
        body.get("user", {}).get("must_change_password") is True,
        str(body.get("user")),
    )

    blocked = api("get", "/leads/", token)
    check("the CRM itself is refused", blocked.status_code == 403,
          f"{blocked.status_code} {blocked.text[:120]}")

    allowed = api("get", "/auth/me", token)
    check("but their own account still answers", allowed.status_code == 200,
          f"{allowed.status_code}")

    print("\n3. Choosing their own password")

    wrong = api("post", "/auth/change-password", token, json={
        "current_password": "not-the-one", "new_password": "Chosen@12345",
    })
    check("the old password has to be right", wrong.status_code == 400,
          f"{wrong.status_code} {wrong.text[:120]}")

    short = api("post", "/auth/change-password", token, json={
        "current_password": temporary, "new_password": "short",
    })
    check("and the new one long enough", short.status_code == 400,
          f"{short.status_code} {short.text[:120]}")

    same = api("post", "/auth/change-password", token, json={
        "current_password": temporary, "new_password": temporary,
    })
    check("and not the one they were just sent", same.status_code == 400,
          f"{same.status_code} {same.text[:120]}")

    changed = api("post", "/auth/change-password", token, json={
        "current_password": temporary, "new_password": "Chosen@12345",
    })
    check("then it is accepted", changed.status_code == 200,
          f"{changed.status_code} {changed.text[:140]}")

    check("and the CRM opens", api("get", "/leads/", token).status_code == 200)

    print("\n4. The temporary one is spent")

    again = requests.post(
        f"{BASE}/auth/login", json={"email": email, "password": temporary}, timeout=30
    )
    check("the emailed password no longer signs in", again.status_code == 401,
          f"{again.status_code}")

    theirs = requests.post(
        f"{BASE}/auth/login", json={"email": email, "password": "Chosen@12345"}, timeout=30
    )
    check("their own does", theirs.status_code == 200, f"{theirs.status_code}")
    check(
        "and nothing is outstanding now",
        theirs.json().get("user", {}).get("must_change_password") is False,
        str(theirs.json().get("user")),
    )

    print("\n5. An administrator resets it for them")

    sent.clear()
    session.expire_all()

    # The endpoint is reached over HTTP, for an account that does not
    # exist. A 404 means the route imported, ran, and got as far as
    # looking the person up; a 500 means it fell over before that - which
    # is exactly what a route referring to a name it never imported did,
    # while the service underneath it passed every test it had.
    #
    # No real reset, so no real email: the behaviour itself is exercised
    # in process below, where the mailer is replaced.
    nobody = api(
        "post",
        "/users/00000000-0000-4000-8000-000000000000/reset-password",
        admin,
        json={},
    )

    check(
        "the reset endpoint is wired up",
        nobody.status_code == 404,
        f"{nobody.status_code} {nobody.text[:160]}",
    )

    reset = UserService.reset_password(
        user_id, session, {"is_super_admin": True, "user_id": user_id}
    )

    check("the reset goes through", reset is not None)
    check("an email went to them", len(sent) == 1 and sent[0]["to"] == email,
          str([m["to"] for m in sent]))

    fresh = password_from(sent[0]["body"] if sent else "")

    check("with a different password than before", bool(fresh) and fresh != temporary)

    stale = requests.post(
        f"{BASE}/auth/login", json={"email": email, "password": "Chosen@12345"}, timeout=30
    )
    check("the password they chose stops working", stale.status_code == 401,
          f"{stale.status_code}")

    back = requests.post(
        f"{BASE}/auth/login", json={"email": email, "password": fresh}, timeout=30
    )
    check("the new temporary one works", back.status_code == 200, f"{back.status_code}")
    check(
        "and they are asked to choose again",
        back.json().get("user", {}).get("must_change_password") is True,
        str(back.json().get("user")),
    )

finally:
    print("\n6. Clearing up")

    try:
        from sqlalchemy import text

        from app.database.postgres import SessionLocal

        db = SessionLocal()

        for uid in created:
            db.execute(text("delete from user_roles where user_id = :i"), {"i": uid})
            db.execute(text("delete from user_companies where user_id = :i"), {"i": uid})
            db.execute(text("delete from users where id = :i"), {"i": uid})

        db.commit()
        print(f"   removed {len(created)} account(s)")
    except Exception as error:  # noqa: BLE001
        print("   cleanup problem:", error)

print(f"\n{len(passed)} passed, {len(failed)} failed")

for name in failed:
    print("  failed:", name)

sys.exit(1 if failed else 0)
