"""The super admin's welcome email: once per person, a new password, everyone signed out.

  * only a super admin may send it;
  * it emails a temporary password, which is the only one that works after;
  * every sign-in from before is ended, in every service's copy of the user;
  * a second send is refused, and two at once send one email;
  * a send that fails changes nothing;
  * not to yourself, and not to a switched-off account.

The mailer is replaced in this process, so nothing leaves the machine;
the sends are made in process for that reason (see test_first_login).
Run seed_sales_team.py first.

    docker exec -w /app backend_app python test_welcome_email.py
"""

import re
import sys
import threading
import uuid

import requests

sys.path.insert(0, "/app")

from app.services.email_service import EmailService  # noqa: E402
from seed_sales_team import BASE, TEAM_PASSWORD, api, login, rows  # noqa: E402

ADMIN = ("superadmin@mailinator.com", "password123")
TAG = uuid.uuid4().hex[:6]
KNOWN = "Known@12345"

sent: list[dict] = []
mail_works = True


def capture(to, subject, text_body, html_body=None, **kw):
    if not mail_works:
        return False
    sent.append({"to": to, "subject": subject, "body": text_body or ""})
    return True


EmailService.send = staticmethod(capture)

passed, failed = [], []


def check(name, ok, detail=""):
    (passed if ok else failed).append(name)
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"   [{detail}]" if not ok else ""))


def password_from(body: str) -> str | None:
    for line in body.splitlines():
        if "Temporary password" in line:
            return re.split(r"\s{2,}", line.strip())[-1].strip()
    return None


from fastapi import HTTPException  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.database.postgres import SessionLocal  # noqa: E402
from app.services.password_service import PasswordService  # noqa: E402
from app.services.user_service import UserService  # noqa: E402

DATABASES = ("solutions", "auth_db", "crm_db", "inventory_db", "sales_db")
created: list[str] = []
admin_id = None


def other_db(name):
    from urllib.parse import quote_plus

    from sqlalchemy import create_engine

    from app.core.config import settings

    return create_engine(
        f"postgresql+psycopg2://{settings.POSTGRES_USER}:{quote_plus(settings.POSTGRES_PASSWORD)}"
        f"@{settings.POSTGRES_HOST}:{settings.POSTGRES_PORT}/{name}"
    )


def make_user(label):
    """A test account with a password we know, signed in already."""

    session = SessionLocal()
    roles = {r["role_name"]: r["id"] for r in rows(api("get", "/rbac/roles", admin))}

    class NewUser:
        first_name = "Welcome"
        last_name = label
        email = f"welcome.{label.lower()}.{TAG}@mailinator.com"
        phone_number = "9800000222"
        employee_id = f"EMP-WE-{label}-{TAG}"
        location = None
        role_ids = [roles["Area Manager"]]
        company_ids = []
        reports_to_id = None
        password = KNOWN

    uid = str(UserService.create(NewUser(), session, {"is_super_admin": True}).id)
    created.append(uid)

    # As if they had already signed in and chosen their own password.
    session.execute(text("update users set must_change_password = false where id = :i"), {"i": uid})
    session.commit()
    session.close()

    return uid, NewUser.email


def send(user_id, as_user=None):
    session = SessionLocal()
    try:
        return UserService.send_welcome_email(user_id, session, as_user or {"user_id": admin_id, "is_super_admin": True})
    finally:
        session.close()


def refused(user_id, status, as_user=None):
    try:
        send(user_id, as_user)
    except HTTPException as error:
        return error.status_code == status, f"{error.status_code} {error.detail}"
    return False, "it was sent"


try:
    admin = login(*ADMIN)
    admin_id = api("get", "/auth/me", admin).json()["data"]["user_id"]

    print("\n1. Only a super admin")
    user_id, email = make_user("One")
    sent.clear()

    zonal = login("zh.north@mailinator.com", TEAM_PASSWORD)
    r = api("post", f"/users/{user_id}/welcome-email", zonal)
    check("a zonal head is refused", r.status_code == 403, f"{r.status_code}")
    r = requests.post(f"{BASE}/users/{user_id}/welcome-email", timeout=30)
    check("signed out, refused", r.status_code in (401, 403), f"{r.status_code}")
    check("and nothing was emailed", sent == [], str(sent))

    print("\n2. The email, the password, the sign-out")
    before = login(email, KNOWN)
    check("they are signed in beforehand", api("get", "/auth/me", before).status_code == 200)

    done = send(user_id)
    check("the super admin sends it", done.welcome_email_sent_at is not None)
    check("one email, to them", len(sent) == 1 and sent[0]["to"] == email, str([s["to"] for s in sent]))

    temporary = password_from(sent[0]["body"]) if sent else None
    check("carrying a temporary password", bool(temporary))

    me = api("get", "/auth/me", before)
    check("their earlier sign-in is ended", me.status_code == 401, f"{me.status_code} {me.text[:100]}")
    r = requests.post(f"{BASE}/auth/login", json={"email": email, "password": KNOWN}, timeout=30)
    check("their old password no longer works", r.status_code == 401, f"{r.status_code}")
    r = requests.post(f"{BASE}/auth/login", json={"email": email, "password": temporary}, timeout=30)
    check("the emailed one does", r.status_code == 200, f"{r.status_code} {r.text[:100]}")
    check("and must be changed first", (r.json().get("user") or {}).get("must_change_password") is True)

    versions = {}
    for name in DATABASES:
        engine = other_db(name)
        with engine.connect() as conn:
            versions[name] = conn.execute(
                text("select session_version from users where id = :i"), {"i": user_id}
            ).scalar()
        engine.dispose()
    present = {k: v for k, v in versions.items() if v is not None}
    check(
        "every service's copy of them is signed out too",
        len(set(present.values())) == 1 and set(present.values()) != {0},
        str(versions),
    )

    listed = [u for u in rows(api("get", "/users/", admin, params={"search": email}))]
    check(
        "the list says when it was sent",
        listed and listed[0].get("welcome_email_sent_at"),
        str(listed[:1]),
    )

    print("\n3. Once")
    ok, detail = refused(user_id, 409)
    check("a second send is refused", ok, detail)
    check("and nothing more is emailed", len(sent) == 1, str(len(sent)))

    two_id, two_email = make_user("Two")
    sent.clear()
    results: list = []

    def race():
        try:
            send(two_id)
            results.append(200)
        except HTTPException as error:
            results.append(error.status_code)

    threads = [threading.Thread(target=race) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    check("two at once: one is sent, one refused", sorted(results) == [200, 409], str(results))
    check("and one email goes", len(sent) == 1, str(len(sent)))

    print("\n4. A failed send changes nothing")
    three_id, three_email = make_user("Three")
    mail_works = False
    ok, detail = refused(three_id, 502)
    mail_works = True
    check("a mail failure is reported", ok, detail)
    r = requests.post(f"{BASE}/auth/login", json={"email": three_email, "password": KNOWN}, timeout=30)
    check("their password is untouched", r.status_code == 200, f"{r.status_code}")
    session = SessionLocal()
    still = session.execute(text("select welcome_email_sent_at from users where id = :i"), {"i": three_id}).scalar()
    session.close()
    check("and the button is still there to try again", still is None, str(still))

    print("\n5. Not to yourself, not to a switched-off account")
    ok, detail = refused(admin_id, 400)
    check("not to yourself", ok, detail)

    session = SessionLocal()
    session.execute(text("update users set is_active = false where id = :i"), {"i": three_id})
    session.commit()
    session.close()
    ok, detail = refused(three_id, 400)
    check("not to a switched-off account", ok, detail)

except Exception as exc:  # noqa: BLE001 - the report below still has to print
    failed.append(f"the run stopped - {exc}")
    print(f"\n  STOPPED  {exc}")

finally:
    for name in DATABASES:
        engine = other_db(name)
        with engine.begin() as conn:
            for uid in created:
                conn.execute(text("delete from user_roles where user_id = :i"), {"i": uid})
                conn.execute(text("delete from user_companies where user_id = :i"), {"i": uid})
                conn.execute(text("delete from users where id = :i"), {"i": uid})
        engine.dispose()
    print(f"\n   removed {len(created)} account(s)")
    print(f"\n{len(passed)} passed, {len(failed)} failed")
    for name in failed:
        print(f"  FAILED  {name}")
    sys.exit(1 if failed else 0)
