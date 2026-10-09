"""Attached files: who may open one, and what reaches a customer.

Run seed_sales_team.py first. Two questions, both of which had the wrong
answer before this suite existed:

  * A file follows the record it hangs off. The North Area Manager's
    purchase order opens for their Zonal Head, their AVP and the CEO,
    and not for the other zone - exactly as the order itself does. Any
    signed-in person with the key used to be able to read any file.

  * The documents listed in a send dialog actually go in the message.
    They used to be shown and left behind: the customer was told an
    annexure was attached and the message arrived with the proposal PDF
    alone, and dropping one from the list changed nothing.

The mailer is replaced while sending is checked, so nothing leaves the
machine. Everything this creates is removed again.

    docker exec -w /app backend_app python test_attachments.py
"""

import io
import sys
from pathlib import Path

import requests

from app.database.postgres import SessionLocal
from app.models.user import User
from app.services.email_service import EmailService
from seed_sales_team import BASE, TEAM_PASSWORD, email_for, login

ADMIN = ("superadmin@mailinator.com", "password123")

#: Small but real enough to be stored and handed back.
PDF = b"%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF"

passed, failed = [], []
section = ""


def banner(title: str) -> None:
    global section
    section = title
    print(f"\n{title}")


def check(name, ok, detail="") -> None:
    (passed if ok else failed).append(f"{section}: {name}")
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"   [{detail}]" if not ok else ""))


def api(method, path, token, **kw):
    return requests.request(
        method,
        f"{BASE}{path}",
        headers={"Authorization": f"Bearer {token}"},
        timeout=60,
        **kw,
    )


created_orders: list[int] = []
created_quotations: list[int] = []
created_keys: list[str] = []
sent: dict = {}

try:
    token = {
        key: login(email_for(key), TEAM_PASSWORD)
        for key in ("ceo", "avp", "zh_north", "zh_south", "am_north_1", "am_south_1")
    }
    token["admin"] = login(*ADMIN)
    token["accounts"] = login("accounts@mailinator.com", TEAM_PASSWORD)

    def upload(who: str, name: str) -> str:
        response = api(
            "post",
            "/attachments",
            token[who],
            files={"file": (name, io.BytesIO(PDF), "application/pdf")},
        )
        response.raise_for_status()
        key = response.json()["data"]["key"]
        created_keys.append(key)

        return key

    # ------------------------------------------------ before any record
    banner("1. A file that is not on a record yet belongs to its uploader")

    key = upload("am_north_1", "Customer PO.pdf")

    check("it is stored", bool(key))
    check(
        "the person who uploaded it can open it",
        api("get", f"/attachments/{key}", token["am_north_1"]).status_code == 200,
    )
    check(
        "so can their Zonal Head, who oversees their work",
        api("get", f"/attachments/{key}", token["zh_north"]).status_code == 200,
    )
    check(
        "the other zone cannot",
        api("get", f"/attachments/{key}", token["am_south_1"]).status_code == 404,
    )

    # --------------------------------------------- once it is on a record
    banner("2. On an order, the file follows the order")

    order = api(
        "post",
        "/orders",
        token["am_north_1"],
        json={
            "customer_name": "Attachment Access Check",
            "order_date": "2026-01-01T00:00:00Z",
            "items": [
                {
                    "sku": "SG-IFP-65-SPX-V100",
                    "product": "Interactive Flat Panel",
                    "qty": 1,
                    "rate": 0,
                    "tax_rate": 18,
                }
            ],
            "attachments": [
                {
                    "name": "Customer PO.pdf",
                    "size": len(PDF),
                    "type": "application/pdf",
                    "key": key,
                }
            ],
        },
    )

    check("the order is raised", order.status_code == 200, order.text[:140])
    order_id = order.json()["data"]["id"]
    created_orders.append(order_id)

    def sees_order(who: str) -> bool:
        return api("get", f"/orders/{order_id}", token[who]).status_code == 200

    def opens_file(who: str) -> bool:
        return api("get", f"/attachments/{key}", token[who]).status_code == 200

    for who in ("am_north_1", "zh_north", "avp", "ceo", "admin", "accounts"):
        on_record = sees_order(who)
        check(
            f"{who}: the file answers the same as the order "
            f"({'open' if on_record else 'closed'})",
            opens_file(who) == on_record,
            f"order {on_record}, file {opens_file(who)}",
        )

    for who in ("zh_south", "am_south_1"):
        check(
            f"{who} can open neither the order nor the file",
            not sees_order(who) and not opens_file(who),
            f"order {sees_order(who)}, file {opens_file(who)}",
        )

    banner("3. A key nobody issued")

    check(
        "an invented key is refused",
        api(
            "get",
            "/attachments/00000000000000000000000000000000.pdf",
            token["ceo"],
        ).status_code
        == 404,
    )
    check(
        "a path dressed up as a key is refused",
        api("get", "/attachments/..%2f..%2f.env", token["ceo"]).status_code == 404,
    )
    check(
        "and nothing is readable without signing in",
        requests.get(f"{BASE}/attachments/{key}", timeout=30).status_code == 401,
    )

    # ------------------------------------------------- what the mail takes
    banner("4. The documents named in a send dialog reach the message")

    def capture(to, subject, text_body, html_body=None, cc=None, attachments=None, **kw):
        sent.clear()
        sent.update({"to": to, "attachments": attachments or []})

        return True

    really_send = EmailService.send
    EmailService.send = staticmethod(capture)

    try:
        ours = upload("am_north_1", "Site Survey.pdf")
        foreign = upload("am_south_1", "Someone Elses Contract.pdf")

        db = SessionLocal()
        me = db.query(User).filter(User.email == email_for("am_north_1")).first()

        opportunities = (api("get", "/opportunities", token["am_north_1"]).json()
                         .get("data") or [])

        if not opportunities:
            raise RuntimeError("no opportunity to hang a proposal off")

        from app.models.quotation import Quotation

        quotation = Quotation(
            quote_number=f"QT-ATT-{ours[:6]}",
            opportunity_id=opportunities[0]["id"],
            organization_name="Attachment Email Check",
            contact_name="Test Contact",
            email="nobody@example.invalid",
            status="DRAFT",
            creator_id=me.id,
            attachments=[
                {"name": "Site Survey.pdf", "type": "application/pdf", "key": ours}
            ],
            items=[],
        )

        db.add(quotation)
        db.commit()
        db.refresh(quotation)
        created_quotations.append(quotation.id)

        class Request:
            to = ["nobody@example.invalid"]
            cc = None
            bcc = None
            subject = "Attachment check"
            body = "Please find attached."
            body_html = None
            attachment_keys = [ours, foreign]
            track_opens = False
            alert_on_download = False
            attach_gst_audit_trail = False
            notify_lead_owner = False
            test_only = False

        from app.services.quotation_service import QuotationService

        QuotationService.send(quotation.id, Request(), {"user_id": str(me.id)}, db)

        names = [a["filename"] for a in sent.get("attachments", [])]

        check("the annexure the sender kept is in it", "Site Survey.pdf" in names, str(names))
        check(
            "the proposal PDF still travels",
            any("Site Survey" not in name for name in names),
            str(names),
        )
        check(
            "a file from another zone is not sent, though it was asked for",
            "Someone Elses Contract.pdf" not in names,
            str(names),
        )

        Request.attachment_keys = []
        QuotationService.send(quotation.id, Request(), {"user_id": str(me.id)}, db)

        names = [a["filename"] for a in sent.get("attachments", [])]
        check(
            "dropping it from the list leaves it out of the message",
            "Site Survey.pdf" not in names,
            str(names),
        )
    finally:
        EmailService.send = really_send

finally:
    banner("5. Clearing up")

    try:
        from sqlalchemy import text

        db = SessionLocal()

        def run(sql, **params):
            try:
                db.execute(text(sql), params)
            except Exception as error:  # noqa: BLE001
                db.rollback()
                print("   could not clear:", str(error).splitlines()[0][:90])

        if created_orders:
            run("delete from sales_order_activity where sales_order_id = any(:i)", i=created_orders)
            run(
                "delete from notifications where module = 'sales_order'"
                " and entity_id = any(:i)",
                i=created_orders,
            )
            run("delete from sales_order where id = any(:i)", i=created_orders)

        if created_quotations:
            run(
                "delete from sales_quotation_activity where quotation_id = any(:i)",
                i=created_quotations,
            )
            run("delete from sales_quotation where id = any(:i)", i=created_quotations)

        if created_keys:
            run("delete from sales_attachment where key = any(:k)", k=created_keys)

        db.commit()

        for stored in created_keys:
            (Path(__file__).resolve().parent / "uploads" / "attachments" / stored).unlink(
                missing_ok=True
            )

        print(
            f"   removed {len(created_orders)} orders, "
            f"{len(created_quotations)} proposals, {len(created_keys)} files"
        )
    except Exception as error:  # noqa: BLE001
        print("   cleanup problem:", error)

print(f"\n{len(passed)} passed, {len(failed)} failed")

for name in failed:
    print("  failed:", name)

sys.exit(1 if failed else 0)
