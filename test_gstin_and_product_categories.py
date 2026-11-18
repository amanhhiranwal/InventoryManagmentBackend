"""The GSTIN check and the Product Categories master.

GSTIN, checked offline (the GST network's own search needs a GSP account):
  * a real GSTIN passes; a wrong check digit or state code is refused, on
    the field check and on a vendor save;
  * /gst/verify names the state and PAN for a valid one, explains a bad
    one, says the live status is not configured, and needs a sign-in.

Product Categories:
  * filled from the stock item list (836 names) by the release step;
  * anyone who can see vendors can read the list; only a procurement-
    masters manager can change it;
  * a duplicate name (any case) is refused; switching one off takes it
    out of the active list;
  * one a vendor supplies cannot be deleted; an unused one can.

Everything it creates is removed again.

    docker exec -w /app backend_app python test_gstin_and_product_categories.py
"""

import json
import sys
import uuid

import requests
from fastapi import HTTPException

from app.core.field_checks import check_gstin, gstin_check_digit
from app.services.procurement.product_categories import SEED_FILE
from seed_sales_team import BASE, TEAM_PASSWORD, api, email_for, login

ADMIN = ("superadmin@mailinator.com", "password123")
TAG = uuid.uuid4().hex[:6]
REAL = "27AAPFU0939F1ZV"  # a published, real GSTIN

passed, failed = [], []


def check(name, condition, detail=""):
    (passed if condition else failed).append(name)
    print(("  PASS  " if condition else "  FAIL  ") + name + (f"   [{detail}]" if detail and not condition else ""))


def refused(value):
    try:
        check_gstin(value)
    except HTTPException as error:
        return error.detail
    return None


admin = login(*ADMIN)
salesperson = login(email_for("am_north_1"), TEAM_PASSWORD)
made_categories, made_vendors = [], []

try:
    print("\nThe GSTIN check")
    check("a real GSTIN passes", refused(REAL) is None, str(refused(REAL)))
    wrong_digit = REAL[:14] + ("A" if REAL[14] != "A" else "B")
    why = refused(wrong_digit)
    check("a wrong check digit is refused", bool(why) and "check digit" in why, str(why))
    check("and the message names the right digit", bool(why) and f"should be {REAL[14]}" in why, str(why))
    bad_state = "00" + REAL[2:14]
    bad_state += gstin_check_digit(bad_state)
    why = refused(bad_state)
    check("a state code that does not exist is refused", bool(why) and "state code" in why, str(why))
    check("lower case and spaces are tidied first", refused(f"  {REAL.lower()} ") is None)

    print("\n/gst/verify")
    r = api("get", "/gst/verify", salesperson, params={"gstin": REAL})
    body = r.json().get("data", {}) if r.status_code == 200 else {}
    check("a valid GSTIN is reported valid", body.get("valid") is True, r.text[:160])
    check("with its state", body.get("state") == "Maharashtra", str(body.get("state")))
    check("and the PAN inside it", body.get("pan") == REAL[2:12], str(body.get("pan")))
    check("and says the live status was not checked", body.get("live", {}).get("status") == "not_configured", str(body.get("live")))
    r = api("get", "/gst/verify", salesperson, params={"gstin": wrong_digit})
    body = r.json().get("data", {}) if r.status_code == 200 else {}
    check("a wrong one is reported invalid, with the reason", body.get("valid") is False and "check digit" in body.get("reason", ""), r.text[:160])
    r = requests.get(f"{BASE}/gst/verify", params={"gstin": REAL})
    check("it needs a sign-in", r.status_code in (401, 403), str(r.status_code))

    print("\nThe Product Categories master")
    seeded = json.loads(SEED_FILE.read_text(encoding="utf-8"))
    r = api("get", "/product-categories", admin)
    names = {row["name"] for row in r.json()["data"]}
    check("the stock item list is loaded", len(seeded) == 836 and set(seeded) <= names, f"{len(seeded)} in file, {len(names)} listed")
    r = api("get", "/product-categories", salesperson)
    check("a salesperson without vendor access cannot read it", r.status_code == 403, str(r.status_code))
    r = api("post", "/product-categories", salesperson, json={"name": f"Nope {TAG}"})
    check("nor add to it", r.status_code == 403, str(r.status_code))

    r = api("post", "/product-categories", admin, json={"name": f"  Test   Category {TAG} "})
    check("the super admin adds one", r.status_code == 200, r.text[:160])
    new = r.json()["data"] if r.status_code == 200 else {}
    if new:
        made_categories.append(new["id"])
    check("its spaces are tidied", new.get("name") == f"Test Category {TAG}", str(new.get("name")))
    r = api("post", "/product-categories", admin, json={"name": f"TEST CATEGORY {TAG}"})
    check("the same name in another case is refused", r.status_code == 409, f"{r.status_code} {r.text[:120]}")
    r = api("post", "/product-categories", admin, json={"name": "   "})
    check("an empty name is refused", r.status_code == 400, str(r.status_code))

    r = api("put", f"/product-categories/{new['id']}", admin, json={"name": new["name"], "is_active": False})
    check("it can be switched off", r.status_code == 200 and r.json()["data"]["is_active"] is False, r.text[:120])
    active = {row["name"] for row in api("get", "/product-categories", admin).json()["data"]}
    every = {row["name"] for row in api("get", "/product-categories", admin, params={"active": False}).json()["data"]}
    check("and then leaves the active list", new["name"] not in active and new["name"] in every)

    print("\nA vendor")
    r = api("post", "/vendors", admin, json={
        "name": f"GST Test Vendor {TAG}", "gstin": wrong_digit, "product_categories": [new["name"]],
    })
    check("a vendor with a wrong GSTIN check digit is refused", r.status_code == 400 and "check digit" in r.text, f"{r.status_code} {r.text[:160]}")
    if r.status_code == 200:
        made_vendors.append(r.json()["data"]["id"])
    r = api("post", "/vendors", admin, json={
        "name": f"GST Test Vendor {TAG}", "gstin": REAL, "product_categories": [new["name"], seeded[0]],
    })
    check("with a real GSTIN it is saved", r.status_code == 200, f"{r.status_code} {r.text[:160]}")
    if r.status_code == 200:
        vendor = r.json()["data"]
        made_vendors.append(vendor["id"])
        check("carrying the categories picked", vendor.get("product_categories") == [new["name"], seeded[0]], str(vendor.get("product_categories")))

    r = api("delete", f"/product-categories/{new['id']}", admin)
    check("a category a vendor supplies cannot be deleted", r.status_code == 409, f"{r.status_code} {r.text[:120]}")

    spare = api("post", "/product-categories", admin, json={"name": f"Spare {TAG}"}).json()["data"]
    r = api("delete", f"/product-categories/{spare['id']}", admin)
    check("an unused one can", r.status_code == 200, r.text[:120])
finally:
    from sqlalchemy import text

    from app.database.postgres import SessionLocal

    session = SessionLocal()
    if made_vendors:
        session.execute(text("delete from procurement_activity where entity_type = 'vendor' and entity_id = any(:ids)"), {"ids": made_vendors})
        session.execute(text("delete from vendor where id = any(:ids)"), {"ids": made_vendors})
    session.execute(text("delete from product_category where name like :tag"), {"tag": f"%{TAG}%"})
    session.commit()
    session.close()
    print(f"\n   removed {len(made_vendors)} vendor(s) and this run's categories")

print(f"\n{len(passed)} passed, {len(failed)} failed")
sys.exit(1 if failed else 0)
