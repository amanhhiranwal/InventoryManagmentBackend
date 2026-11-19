"""The bank branch behind an IFSC, for the vendor form.

GET /ifsc/<code> asks Razorpay's public IFSC service (ifsc.razorpay.com):
  * ICIC0000253 comes back as ICICI Bank, its branch, city, state and MICR;
  * lower case and spaces are tidied first;
  * a code no branch has is 404, saying which code;
  * a code that is not an IFSC is 400, before anything is asked;
  * it needs a sign-in, and vendor access;
  * the service being unreachable is a 502 that says to type the details
    by hand - never an empty answer;
  * a vendor keeps each bank detail in its own field - holder, bank,
    branch, bank address, account number, account type, IFSC, MICR - and
    a bad MICR or account type is refused;
  * a verified vendor whose IFSC or account number changes is sent back
    for verification; any other change keeps it verified.

This calls the real service, so it needs the internet.

    docker exec -w /app backend_app python test_ifsc_lookup.py
"""

import sys

import requests
from fastapi import HTTPException

from app.services import ifsc_lookup
from seed_sales_team import BASE, TEAM_PASSWORD, api, email_for, login

ADMIN = ("superadmin@mailinator.com", "password123")

passed, failed = [], []


def check(name, condition, detail=""):
    (passed if condition else failed).append(name)
    print(("  PASS  " if condition else "  FAIL  ") + name + (f"   [{detail}]" if detail and not condition else ""))


admin = login(*ADMIN)
salesperson = login(email_for("am_north_1"), TEAM_PASSWORD)

print("\nA real IFSC")
r = api("get", "/ifsc/ICIC0000253", admin)
body = r.json().get("data", {}) if r.status_code == 200 else {}
check("it is found", r.status_code == 200, f"{r.status_code} {r.text[:160]}")
check("the bank is ICICI Bank", body.get("bank") == "ICICI Bank", str(body.get("bank")))
check("with its branch", body.get("branch") == "NOIDASECTOR SIXTY ONE", str(body.get("branch")))
check("its city and state", body.get("city") == "GAUTAM BUDDHA NAGAR" and body.get("state") == "UTTAR PRADESH",
      f"{body.get('city')} / {body.get('state')}")
check("and its MICR", body.get("micr") == "110229036", str(body.get("micr")))

r = api("get", "/ifsc/ icic0000253", admin)
check("lower case and spaces are tidied first",
      r.status_code == 200 and r.json()["data"]["ifsc"] == "ICIC0000253", f"{r.status_code} {r.text[:120]}")

print("\nWrong codes")
r = api("get", "/ifsc/ICIC0999999", admin)
check("a code no branch has is 404", r.status_code == 404, f"{r.status_code} {r.text[:120]}")
check("saying which code", "ICIC0999999" in r.text, r.text[:120])
r = api("get", "/ifsc/ICIC1000253", admin)
check("a code without the 0 is refused as not an IFSC", r.status_code == 400 and "not an IFSC" in r.text,
      f"{r.status_code} {r.text[:120]}")
r = api("get", "/ifsc/ICIC025", admin)
check("so is a short one", r.status_code == 400, f"{r.status_code} {r.text[:120]}")

print("\nWho may ask")
r = requests.get(f"{BASE}/ifsc/ICIC0000253", timeout=15)
check("it needs a sign-in", r.status_code in (401, 403), str(r.status_code))
r = api("get", "/ifsc/ICIC0000253", salesperson)
check("a salesperson without vendor access is refused", r.status_code == 403, str(r.status_code))

print("\nThe service being down")
original = ifsc_lookup.SERVICE
ifsc_lookup.SERVICE = "https://ifsc.invalid/"
try:
    ifsc_lookup.lookup("ICIC0000253")
    check("an unreachable service is reported", False, "no error raised")
except HTTPException as error:
    check("an unreachable service is a 502", error.status_code == 502, str(error.status_code))
    check("telling the user to type the details", "by hand" in str(error.detail), str(error.detail))
finally:
    ifsc_lookup.SERVICE = original

print("\nThe bank details, each in its own field on the vendor")
made = []
found = api("get", "/ifsc/ICIC0000253", admin).json()["data"]
bank = {
    "account_holder": "Bank Test Traders",
    "bank_name": found["bank"],
    "branch": found["branch"],
    "bank_address": found["address"],
    "account_number": "123456789012",
    "account_type": "CC",
    "ifsc": found["ifsc"],
    "micr": found["micr"],
}
try:
    r = api("post", "/vendors", admin, json={"name": "IFSC Bank Test Vendor", "vendor_type": "Distributor", **bank})
    check("a vendor is saved with every bank detail", r.status_code == 200, f"{r.status_code} {r.text[:160]}")
    if r.status_code == 200:
        made.append(r.json()["data"]["id"])
        saved = api("get", f"/vendors/{made[-1]}", admin).json()["data"]
        for key, value in bank.items():
            check(f"{key} reads back as saved", saved.get(key) == value, f"{saved.get(key)!r} vs {value!r}")

        print("\nRe-verification after a change")
        from sqlalchemy import text as sql

        from app.database.postgres import SessionLocal

        def mark_verified(vendor_id):
            session = SessionLocal()
            session.execute(sql("update vendor set documents_verified = true where id = :id"), {"id": vendor_id})
            session.commit()
            session.close()

        def verified(vendor_id):
            return api("get", f"/vendors/{vendor_id}", admin).json()["data"]["documents_verified"]

        vid = made[-1]
        body = {"name": "IFSC Bank Test Vendor", "vendor_type": "Distributor", **bank}
        mark_verified(vid)
        r = api("put", f"/vendors/{vid}", admin, json={**body, "remarks": "a note"})
        check("changing something else keeps the vendor verified", r.status_code == 200 and verified(vid) is True,
              f"{r.status_code} {r.text[:120]}")
        r = api("put", f"/vendors/{vid}", admin, json={**body, "ifsc": "HDFC0000001"})
        check("changing the IFSC sends it back for verification", r.status_code == 200 and verified(vid) is False,
              f"{r.status_code} {r.text[:120]}")
        mark_verified(vid)
        r = api("put", f"/vendors/{vid}", admin, json={**body, "ifsc": "HDFC0000001", "account_number": "999988887777"})
        check("so does changing the account number", r.status_code == 200 and verified(vid) is False,
              f"{r.status_code} {r.text[:120]}")

    r = api("post", "/vendors", admin, json={"name": "IFSC Bad MICR", "vendor_type": "Distributor", "micr": "12345"})
    check("a MICR that is not nine digits is refused", r.status_code == 400 and "MICR" in r.text, f"{r.status_code} {r.text[:120]}")
    if r.status_code == 200:
        made.append(r.json()["data"]["id"])
    r = api("post", "/vendors", admin, json={"name": "IFSC Bad Type", "vendor_type": "Distributor", "account_type": "Savings"})
    check("an account type other than Current, CC or OD is refused",
          r.status_code == 400 and "Account type" in r.text, f"{r.status_code} {r.text[:120]}")
    if r.status_code == 200:
        made.append(r.json()["data"]["id"])
finally:
    if made:
        from sqlalchemy import text

        from app.database.postgres import SessionLocal

        session = SessionLocal()
        session.execute(text("delete from procurement_activity where entity_type = 'vendor' and entity_id = any(:ids)"), {"ids": made})
        session.execute(text("delete from vendor where id = any(:ids)"), {"ids": made})
        session.commit()
        session.close()
    print(f"   removed {len(made)} test vendor(s)")

print(f"\n{len(passed)} passed, {len(failed)} failed")
sys.exit(1 if failed else 0)
