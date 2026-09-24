"""End-to-end check of the reporting hierarchy and per-company products.

Builds a throwaway Sales team under the seeded hierarchy:

    CEO -> AVP -> Zonal Head North -> Area Manager 1, Area Manager 2
                  Zonal Head South -> Area Manager 3

and checks that a record follows the reporting line: a Zonal Head sees
their own Area Managers' work and never the other Zonal Head's.

Then files the same product under two companies and checks each side only
sees its own.

Everything it creates is removed again in the finally block.

Run it against a running backend:  python test_hierarchy_and_company_scope.py
"""

import sys
import uuid

import requests

BASE = "http://localhost:8000/api/v1"
ADMIN = ("syn-crm-9f3a2@mailinator.com", "password123")
PASSWORD = "Password@123"
TAG = uuid.uuid4().hex[:6]

passed, failed = [], []


def check(name, condition, detail=""):
    (passed if condition else failed).append(name)
    print(("  PASS  " if condition else "  FAIL  ") + name + (f"   {detail}" if detail and not condition else ""))


def login(email, password):
    r = requests.post(f"{BASE}/auth/login", json={"email": email, "password": password})
    r.raise_for_status()
    body = r.json()
    return body.get("data", body)["access_token"]


def head(token):
    return {"Authorization": f"Bearer {token}"}


def api(method, path, token, **kw):
    return requests.request(method, f"{BASE}{path}", headers=head(token), **kw)


def rows(response):
    """The list in a response, whether it is bare or wrapped in data."""

    body = response.json()
    return body if isinstance(body, list) else body.get("data", [])


created_users, created_leads, created_items, created_companies = [], [], [], []
created_opportunities = []
temp_type_id = None

try:
    admin = login(*ADMIN)
    print("Signed in as the super admin.\n")

    # ---------------------------------------------------------------- roles
    print("1. Roles offered when creating a user")
    by_name = {r["role_name"]: r for r in rows(api("get", "/rbac/roles", admin))}

    check(
        "the role list is exactly Super Admin, CEO, AVP, Zonal Head, Area Manager",
        sorted(by_name) == ["AVP", "Area Manager", "CEO", "Super Admin", "Zonal Head"],
        str(sorted(by_name)),
    )

    levels = {n: r.get("level") for n, r in by_name.items()}
    check(
        "the chart puts them in order CEO 1, AVP 2, Zonal Head 3, Area Manager 4",
        [levels["CEO"], levels["AVP"], levels["Zonal Head"], levels["Area Manager"]] == [1, 2, 3, 4],
        str(levels),
    )

    # ------------------------------------------------------------ companies
    print("\n2. Two companies to keep products apart")

    def make_company(label):
        payload = {
            "company_name": f"{label} {TAG}",
            "company_code": f"{label[:3].upper()}{TAG}",
            "email": f"{label.lower().replace(' ', '')}.{TAG}@mailinator.com",
            "phone_number": "9000000000",
            "address_line_1": "1 Test Street",
            "city": "Pune",
            "state": "Maharashtra",
            "country": "India",
            "postal_code": "411001",
        }
        r = api("post", "/companies/", admin, json=payload)
        r.raise_for_status()
        cid = r.json().get("data", r.json())["id"]
        created_companies.append(cid)
        return cid

    company_a = make_company("Alpha Agro")
    company_b = make_company("Beta Seeds")
    check("two companies created", bool(company_a and company_b))

    # ---------------------------------------------------------------- users
    print("\n3. The team, each under their manager")

    def make_user(label, role, manager_id=None, companies=()):
        payload = {
            "first_name": label,
            "last_name": TAG,
            "email": f"{label.lower()}.{TAG}@mailinator.com",
            "password": PASSWORD,
            "phone_number": "9000000001",
            "employee_id": f"EMP-{label.upper()}-{TAG}",
            "role_ids": [by_name[role]["id"]],
            "company_ids": list(companies),
            "reports_to_id": manager_id,
        }
        r = api("post", "/users/", admin, json=payload)
        if r.status_code >= 400:
            raise RuntimeError(f"{label}: {r.status_code} {r.text}")
        uid = r.json().get("data", r.json())["id"]
        created_users.append(uid)
        return uid, payload["email"]

    ceo_id, ceo_email = make_user("Ceo", "CEO", None, [company_a, company_b])
    avp_id, avp_email = make_user("Avp", "AVP", ceo_id, [company_a, company_b])
    zh_n_id, zh_n_email = make_user("Zonalnorth", "Zonal Head", avp_id, [company_a])
    zh_s_id, zh_s_email = make_user("Zonalsouth", "Zonal Head", avp_id, [company_b])
    am1_id, am1_email = make_user("Areaone", "Area Manager", zh_n_id, [company_a])
    am2_id, am2_email = make_user("Areatwo", "Area Manager", zh_n_id, [company_a])
    am3_id, am3_email = make_user("Areathree", "Area Manager", zh_s_id, [company_b])
    check("the whole team was created", len(created_users) == 7)

    # A manager may only be someone senior: an Area Manager under an Area
    # Manager has to be refused.
    bad = api("post", "/users/", admin, json={
        "first_name": "Bad", "last_name": TAG,
        "email": f"bad.{TAG}@mailinator.com", "password": PASSWORD,
        "phone_number": "9000000002", "employee_id": f"EMP-BAD-{TAG}",
        "role_ids": [by_name["Area Manager"]["id"]],
        "company_ids": [company_a], "reports_to_id": am1_id,
    })
    check(
        "an Area Manager cannot be put under another Area Manager",
        bad.status_code == 400,
        f"got {bad.status_code}",
    )
    if bad.status_code < 400:
        created_users.append(bad.json().get("data", bad.json())["id"])

    tokens = {
        "CEO": login(ceo_email, PASSWORD),
        "AVP": login(avp_email, PASSWORD),
        "ZH North": login(zh_n_email, PASSWORD),
        "ZH South": login(zh_s_email, PASSWORD),
        "AM 1": login(am1_email, PASSWORD),
        "AM 2": login(am2_email, PASSWORD),
        "AM 3": login(am3_email, PASSWORD),
    }
    print("   everyone can sign in")

    # ---------------------------------------------------------------- leads
    print("\n4. A lead each, then who can see it")

    def make_lead(who, label):
        r = api("post", "/leads/", tokens[who], json={
            "title": f"{label} {TAG}",
            "organization_name": f"{label} Org {TAG}",
            "contact_name": label,
            "email": f"{label.lower().replace(' ', '')}.{TAG}@mailinator.com",
            "mobile_number": "+91 9000000003",
        })
        if r.status_code >= 400:
            raise RuntimeError(f"{who} lead: {r.status_code} {r.text}")
        lead = r.json().get("data", r.json())
        created_leads.append(lead["id"])
        return lead["id"]

    lead_am1 = make_lead("AM 1", "Lead North One")
    lead_am2 = make_lead("AM 2", "Lead North Two")
    lead_am3 = make_lead("AM 3", "Lead South Three")

    def lead_ids(who):
        return {row["id"] for row in rows(api("get", "/leads/", tokens[who]))}

    mine = {who: lead_ids(who) for who in tokens}

    check("AM 1 sees their own lead", lead_am1 in mine["AM 1"])
    check("AM 1 does not see AM 2's lead", lead_am2 not in mine["AM 1"])
    check("AM 1 does not see AM 3's lead", lead_am3 not in mine["AM 1"])

    check(
        "Zonal Head North sees both of their Area Managers' leads",
        {lead_am1, lead_am2} <= mine["ZH North"],
    )
    check(
        "Zonal Head North does not see the other zone's lead",
        lead_am3 not in mine["ZH North"],
    )
    check("Zonal Head South sees their own zone's lead", lead_am3 in mine["ZH South"])
    check(
        "Zonal Head South does not see the North zone's leads",
        not ({lead_am1, lead_am2} & mine["ZH South"]),
    )
    check("the AVP sees all three", {lead_am1, lead_am2, lead_am3} <= mine["AVP"])
    check("the CEO sees all three", {lead_am1, lead_am2, lead_am3} <= mine["CEO"])

    # Opening one by id follows the same line as the list.
    r = api("get", f"/leads/{lead_am3}", tokens["ZH North"])
    check(
        "opening the other zone's lead by its id is refused",
        r.status_code == 403,
        f"got {r.status_code}",
    )
    r = api("get", f"/leads/{lead_am1}", tokens["ZH North"])
    check("their own Area Manager's lead opens", r.status_code == 200, f"got {r.status_code}")

    # -------------------------------------------------------- opportunities
    print("\n5. The same line applies once a lead becomes an opportunity")

    # A lead has to be qualified before it can become an opportunity.
    for status in ("CONTACTED", "QUALIFIED"):
        step = api("put", f"/leads/{lead_am1}/progress", tokens["AM 1"], json={"stage": "lead", "status": status})
        if step.status_code >= 400:
            print(f"   could not move the lead to {status}:", step.status_code, step.text[:200])

    r = api("post", "/opportunities/", tokens["AM 1"], json={
        "lead_id": lead_am1,
        "title": f"Opportunity North {TAG}",
        "expected_value": 50000,
    })
    if r.status_code >= 400:
        print("   could not create the opportunity:", r.status_code, r.text[:200])
        opp_id = None
    else:
        opp_id = r.json().get("data", r.json())["id"]
        created_opportunities.append(opp_id)

    if opp_id:
        def opp_ids(who):
            return {row["id"] for row in rows(api("get", "/opportunities/", tokens[who]))}

        check("Zonal Head North sees the opportunity", opp_id in opp_ids("ZH North"))
        check("Zonal Head South does not", opp_id not in opp_ids("ZH South"))
        check("the CEO sees it", opp_id in opp_ids("CEO"))

    # ------------------------------------------------------------- products
    print("\n6. The same product under two companies")

    types = rows(api("get", "/product-types/", admin))

    # Nothing in the masters yet - add one to test against, and take it away
    # again at the end.
    if not types:
        r = api("post", "/product-types/", admin, json={
            "name": f"Test Type {TAG}",
            "code": f"TT{TAG}".upper(),
            "category": "Test Category",
        })
        if r.status_code == 200:
            temp_type_id = r.json().get("data", {}).get("id")
            types = rows(api("get", "/product-types/", admin))
        else:
            print("   could not add a product type:", r.status_code, r.text[:200])

    if not types:
        print("   no product types available - skipping the product checks")
    else:
        type_code = types[0]["code"]
        category = types[0].get("category") or "General"
        serial = f"SN-{TAG}"

        def make_product(who, company_id, label):
            return api("post", "/inventory/items", tokens[who], json={
                "name": f"{label} {TAG}",
                "serial_number": serial,
                "product_type_code": type_code,
                "category": category,
                "attributes": {"rate": 100, "unit": "Kg", "instock": 10, "case_size": 1},
                "company_id": company_id,
            })

        r1 = make_product("AM 1", company_a, "Shared Product Alpha")
        check("Area Manager 1 adds the product for their company", r1.status_code == 200, f"{r1.status_code} {r1.text[:160]}")
        if r1.status_code == 200:
            created_items.append(r1.json()["data"]["_id"])

        r2 = make_product("AM 3", company_b, "Shared Product Beta")
        check(
            "the other company can carry the same serial number",
            r2.status_code == 200,
            f"{r2.status_code} {r2.text[:160]}",
        )
        if r2.status_code == 200:
            created_items.append(r2.json()["data"]["_id"])

        r3 = make_product("AM 2", company_a, "Duplicate Within Company")
        check(
            "the same serial twice inside one company is refused",
            r3.status_code == 400,
            f"got {r3.status_code}",
        )
        if r3.status_code == 200:
            created_items.append(r3.json()["data"]["_id"])

        if r1.status_code == 200 and r2.status_code == 200:
            id_a = r1.json()["data"]["_id"]
            id_b = r2.json()["data"]["_id"]

            def product_ids(who):
                return {row["_id"] for row in rows(api("get", "/inventory/items", tokens[who]))}

            a_side = product_ids("AM 1")
            b_side = product_ids("AM 3")

            check("company A sees its own product", id_a in a_side)
            check("company A does not see company B's", id_b not in a_side)
            check("company B sees its own product", id_b in b_side)
            check("company B does not see company A's", id_a not in b_side)
            check("someone in both companies sees both", {id_a, id_b} <= product_ids("CEO"))

            r = api("put", f"/inventory/items/{id_b}", tokens["AM 1"], json={
                "name": "Hijack",
                "serial_number": serial,
                "product_type_code": type_code,
                "category": category,
                "attributes": {"rate": 1, "unit": "Kg", "instock": 1, "case_size": 1},
            })
            check(
                "editing the other company's product is refused",
                r.status_code == 403,
                f"got {r.status_code}",
            )

            offered = rows(api("get", "/inventory/companies", tokens["AM 1"]))
            check(
                "the product form only offers the companies on the profile",
                [c["id"] for c in offered] == [company_a],
                str(offered),
            )

finally:
    print("\n7. Clearing the test data")
    try:
        admin = login(*ADMIN)
        for item_id in created_items:
            api("delete", f"/inventory/items/{item_id}", admin)
        if temp_type_id:
            api("delete", f"/product-types/{temp_type_id}", admin)

        # Leads and opportunities have no delete endpoint, and a user cannot
        # be removed while their records point at them, so the rest goes
        # straight to the database - by id, so only what this run made.
        from sqlalchemy import text

        from app.database.postgres import SessionLocal

        session = SessionLocal()

        def run(sql, **params):
            try:
                session.execute(text(sql), params)
            except Exception as exc:
                session.rollback()
                print("   could not clear:", str(exc).split("\n")[0][:90])

        if created_opportunities:
            run("delete from sales_opportunity_activity where opportunity_id = any(:ids)", ids=created_opportunities)
            run("delete from sales_opportunity where id = any(:ids)", ids=created_opportunities)
        if created_leads:
            run("delete from sales_lead_activity where lead_id = any(:ids)", ids=created_leads)
            run("delete from sales_lead where id = any(:ids)", ids=created_leads)
        if created_users:
            run("delete from notifications where user_id = any(cast(:ids as uuid[]))", ids=created_users)
            run("delete from user_roles where user_id = any(cast(:ids as uuid[]))", ids=created_users)
            run("delete from user_companies where user_id = any(cast(:ids as uuid[]))", ids=created_users)
            run("update users set reports_to_id = null where reports_to_id = any(cast(:ids as uuid[]))", ids=created_users)
            run("delete from users where id = any(cast(:ids as uuid[]))", ids=created_users)
        if created_companies:
            run("delete from user_companies where company_id = any(cast(:ids as uuid[]))", ids=created_companies)
            run("delete from companies where id = any(cast(:ids as uuid[]))", ids=created_companies)
        session.commit()

        left = {
            "users": session.execute(
                text("select count(*) from users where id = any(cast(:ids as uuid[]))"),
                {"ids": created_users or ["00000000-0000-0000-0000-000000000000"]},
            ).scalar(),
            "leads": session.execute(
                text("select count(*) from sales_lead where id = any(:ids)"),
                {"ids": created_leads or [-1]},
            ).scalar(),
        }
        session.close()

        print("   removed", len(created_items), "products,", len(created_leads), "leads,",
              len(created_opportunities), "opportunities,", len(created_users), "users,",
              len(created_companies), "companies")
        print("   left behind:", left)
    except Exception as exc:
        print("   cleanup problem:", exc)

print(f"\n{len(passed)} passed, {len(failed)} failed")
for name in failed:
    print("  failed:", name)
sys.exit(1 if failed else 0)
