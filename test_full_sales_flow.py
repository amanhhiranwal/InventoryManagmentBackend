"""The whole flow, run by the seeded Sales team.

Run seed_sales_team.py first. This drives Customer -> Lead -> Opportunity
-> Quotation -> Sales Order -> Proforma Invoice as one Area Manager, and
after each step checks who can see it:

    their Zonal Head yes, the other Zonal Head no, the Area Manager next to
    them no, the AVP and CEO yes.

It also covers the screens a super admin owns - Masters, Workflows and
Roles & Access - and products kept apart by company.

The team and the two companies stay; everything else this creates is
removed at the end.

    docker exec -w /app backend_app python test_full_sales_flow.py
"""

import sys
import uuid
from datetime import datetime, timedelta, timezone

import requests

from seed_sales_team import BASE, TEAM_PASSWORD, api, email_for, login, rows

ADMIN = ("superadmin@mailinator.com", "password123")
TAG = uuid.uuid4().hex[:6]

passed, failed = [], []
section = ""


def check(name, condition, detail=""):
    (passed if condition else failed).append(f"{section}: {name}")
    print(("  PASS  " if condition else "  FAIL  ") + name + (f"   [{detail}]" if detail and not condition else ""))


def banner(title):
    global section
    section = title
    print(f"\n{title}")


#: Who should and should not see an Area Manager North 1 record.
SEES = ["zh_north", "avp", "ceo"]
BLIND = ["zh_south", "am_north_2", "am_south_1"]


def visibility(label, path, record_id, key="id"):
    """One record, checked against everybody's list."""

    def ids(who):
        return {str(row.get(key)) for row in rows(api("get", path, token[who]))}

    for who in SEES:
        check(f"{label}: {who} sees it", str(record_id) in ids(who))
    for who in BLIND:
        check(f"{label}: {who} does not", str(record_id) not in ids(who))


created = {
    "customers": [], "leads": [], "opportunities": [], "quotations": [],
    "orders": [], "invoices": [], "items": [], "masters": [],
}

#: (role_id, permission_id) ticked during the run and unticked at the end.
granted = []

token = {}

try:
    admin = login(*ADMIN)
    everyone = ["ceo", "avp", "zh_north", "zh_south", "am_north_1", "am_north_2", "am_south_1"]

    for key in everyone:
        token[key] = login(email_for(key), TEAM_PASSWORD)
    print("Signed in as the super admin and all seven of the team.")

    people = {u["email"]: u for u in rows(api("get", "/users/", admin, params={"size": 500}))}
    user_id = {key: people[email_for(key)]["id"] for key in everyone}

    companies = {c["company_code"]: c["id"] for c in rows(api("get", "/companies/", admin, params={"size": 200}))}
    north, south = companies["SYN-NORTH"], companies["SYN-SOUTH"]

    # ================================================================ users
    banner("1. Who each person can see on the Users page")

    def visible_users(who):
        return {u["email"] for u in rows(api("get", "/users/", token[who], params={"size": 500}))}

    seen = {who: visible_users(who) for who in everyone}

    check("the CEO sees their whole line", {email_for(k) for k in everyone} <= seen["ceo"])
    check(
        "Zonal Head North sees both of their Area Managers",
        {email_for("am_north_1"), email_for("am_north_2")} <= seen["zh_north"],
    )
    check(
        "Zonal Head North does not see the South zone",
        not ({email_for("zh_south"), email_for("am_south_1")} & seen["zh_north"]),
    )
    check("Zonal Head North does not see their own AVP", email_for("avp") not in seen["zh_north"])
    check("an Area Manager does not see the one next to them", email_for("am_north_2") not in seen["am_north_1"])
    check("nobody but a super admin sees the super admin", ADMIN[0] not in seen["ceo"])

    r = api("put", f"/users/{user_id['am_south_1']}/role", token["zh_north"], json={
        "role_ids": [], "company_ids": [],
    })
    check("a Zonal Head cannot change the other zone's Area Manager", r.status_code == 403, f"got {r.status_code}")

    # ======================================================= roles & access
    banner("2. Roles & Access")

    admin_roles = {r["role_name"] for r in rows(api("get", "/rbac/roles", admin))}
    check(
        "a super admin sees every role - the sales chart and both desks",
        admin_roles
        >= {
            "Super Admin", "CEO", "AVP", "Zonal Head", "Area Manager",
            "Accounts", "Inventory",
        },
        str(sorted(admin_roles)),
    )

    zh_roles = rows(api("get", "/rbac/roles", token["zh_north"]))
    check("a Zonal Head sees only the roles below theirs", {r["role_name"] for r in zh_roles} == {"Area Manager"}, str([r["role_name"] for r in zh_roles]))

    all_roles = {r["role_name"]: r for r in rows(api("get", "/rbac/roles", admin))}
    area_role, ceo_role = all_roles["Area Manager"], all_roles["CEO"]
    zh_role = all_roles["Zonal Head"]

    permissions = {p["permission_name"]: p["id"] for p in rows(api("get", "/rbac/permissions", admin))}

    # Roles & Access decides who reaches these screens at all: without the
    # tick even a Zonal Head is turned away. Grant it here, then put the
    # roles back as they were at the end.
    r = api("get", f"/rbac/roles/{area_role['id']}/permissions", token["zh_north"])
    check("without the tick, a Zonal Head cannot open Roles & Access", r.status_code == 403, f"got {r.status_code}")

    for role_id, permission in (
        (zh_role["id"], "role.read"),
        (zh_role["id"], "workflow.read"),
        (area_role["id"], "workflow.read"),
    ):
        r = api("post", f"/rbac/roles/{role_id}/permissions/{permissions[permission]}", admin)
        check(f"a super admin ticks {permission} for that role", r.status_code == 200, f"{r.status_code} {r.text[:120]}")
        granted.append((role_id, permissions[permission]))

    token["zh_north"] = login(email_for("zh_north"), TEAM_PASSWORD)
    token["am_north_1"] = login(email_for("am_north_1"), TEAM_PASSWORD)

    r = api("get", f"/rbac/roles/{area_role['id']}/permissions", token["zh_north"])
    check("with the tick, they can open an Area Manager's permissions", r.status_code == 200, f"got {r.status_code}")

    r = api("get", f"/rbac/roles/{ceo_role['id']}/permissions", token["zh_north"])
    check("but never the CEO's, which is above them", r.status_code == 403, f"got {r.status_code}")

    r = api("post", "/rbac/roles", token["ceo"], json={"role_name": f"Sneaky {TAG}", "description": "x"})
    check("only a super admin can add a role", r.status_code == 403, f"got {r.status_code}")

    r = api("post", f"/rbac/roles/{area_role['id']}/permissions/{permissions['dashboard.read']}", token["zh_north"])
    check("only a super admin can tick a permission", r.status_code == 403, f"got {r.status_code}")

    # =========================================================== workflows
    banner("3. Workflows, the hierarchy chart")

    charts = rows(api("get", "/workflows/", admin))
    check("a super admin sees the chart", len(charts) >= 1)

    if charts:
        full = charts[0]
        labels = {(n.get("data") or {}).get("label") for n in full["nodes"]}
        check(
            "it holds the whole line CEO to Area Manager",
            labels == {"CEO", "AVP", "Zonal Head", "Area Manager"},
            str(sorted(labels)),
        )

        zh_charts = rows(api("get", "/workflows/", token["zh_north"]))
        zh_labels = {(n.get("data") or {}).get("label") for c in zh_charts for n in c["nodes"]}
        check(
            "a Zonal Head sees their own node and below, not above",
            zh_labels == {"Zonal Head", "Area Manager"},
            str(sorted(zh_labels)),
        )

        am_charts = rows(api("get", "/workflows/", token["am_north_1"]))
        am_labels = {(n.get("data") or {}).get("label") for c in am_charts for n in c["nodes"]}
        check("an Area Manager sees only their own node", am_labels == {"Area Manager"}, str(sorted(am_labels)))

        r = api("put", f"/workflows/{full['id']}", token["ceo"], json={
            "name": "Hijacked", "description": "", "nodes": full["nodes"], "edges": full["edges"],
        })
        check("only a super admin can change the chart", r.status_code == 403, f"got {r.status_code}")

        r = api("post", "/workflows/", admin, json={
            "name": f"Loop {TAG}",
            "description": "",
            "nodes": [
                {"id": "n1", "x": 0, "y": 0, "data": {"label": "Area Manager", "role_id": area_role["id"]}},
                {"id": "n2", "x": 0, "y": 100, "data": {"label": "CEO", "role_id": ceo_role["id"]}},
            ],
            "edges": [{"id": "e1", "source": "n1", "target": "n2"}],
        })
        check(
            "a chart that puts a role above itself is refused",
            r.status_code == 400,
            f"got {r.status_code} {r.text[:120]}",
        )
        if r.status_code == 200:
            api("delete", f"/workflows/{r.json().get('data', {}).get('id')}", admin)

    # ============================================================= masters
    banner("4. Masters are the super admin's")

    master_writes = [
        ("a company", "post", "/companies/", {
            "company_name": f"Blocked {TAG}", "company_code": f"BLK{TAG}",
            "email": f"blocked.{TAG}@mailinator.com", "phone_number": "9000000000",
            "address_line_1": "1 Road", "city": "Pune", "state": "Maharashtra",
            "country": "India", "postal_code": "411001",
        }),
        ("a location", "post", "/locations/", {"location_name": f"Blocked {TAG}", "location_code": f"BL{TAG}"}),
        ("a product type", "post", "/product-types/", {"name": f"Blocked {TAG}", "code": f"BP{TAG}", "category": "x"}),
        ("a category group", "post", "/category-groups/", {"name": f"Blocked {TAG}", "code": f"BC{TAG}"}),
        ("a customer type", "post", "/customer-types/", {"name": f"Blocked {TAG}", "code": f"BT{TAG}"}),
        ("a unit", "post", "/inventory/units", {"name": f"BU{TAG}"}),
        ("a state", "post", "/states/", {"name": f"Blocked {TAG}", "code": f"BS{TAG}"}),
    ]

    for label, method, path, payload in master_writes:
        r = api(method, path, token["ceo"], json=payload)
        check(f"a CEO cannot add {label}", r.status_code == 403, f"got {r.status_code} {r.text[:100]}")

    master_reads = [
        ("companies", "/companies/"), ("locations", "/locations/"),
        ("product types", "/product-types/"), ("category groups", "/category-groups/"),
        ("customer types", "/customer-types/"), ("units", "/inventory/units"),
        ("states", "/states/"), ("lead sources", "/lead-sources/"),
    ]
    for label, path in master_reads:
        r = api("get", path, token["am_north_1"])
        check(f"an Area Manager can still read {label} for their forms", r.status_code == 200, f"got {r.status_code}")

    # The product type the flow below needs.
    r = api("post", "/product-types/", admin, json={
        "name": f"Flow Type {TAG}", "code": f"FT{TAG}".upper(), "category": "Flow Category",
    })
    check("a super admin can add a product type", r.status_code == 200, f"got {r.status_code} {r.text[:120]}")
    product_type = r.json()["data"] if r.status_code == 200 else None
    if product_type:
        created["masters"].append(("product-types", product_type["id"]))

    # ======================================================== the full flow
    banner("5. Customer -> Lead -> Opportunity -> Quotation -> Order -> Invoice")
    owner = "am_north_1"

    r = api("post", "/customers", token[owner], json={
        "name": f"Greenfield Farms {TAG}",
        "contact_name": "Ramesh Gupta",
        "email": f"greenfield.{TAG}@mailinator.com",
        "phone": "+91 9812345670",
        "city": "Delhi",
        "state": "Delhi",
        "country": "India",
        "lead_source": "Marketing",
        "create_lead": True,
    })
    check("the Area Manager adds a customer and creates its lead", r.status_code == 200, f"{r.status_code} {r.text[:200]}")

    customer = r.json()["data"] if r.status_code == 200 else None
    if customer:
        created["customers"].append(customer["id"])
        lead_id = customer.get("lead_id")
        check("the lead came with it", bool(lead_id), str(customer.get("lead_id")))
        if lead_id:
            created["leads"].append(lead_id)

        visibility("the customer", "/customers", customer["id"])

        if lead_id:
            visibility("the lead", "/leads/", lead_id)

            r = api("get", f"/leads/{lead_id}", token["zh_south"])
            check("the other zone cannot open the lead by id", r.status_code == 403, f"got {r.status_code}")

            for status in ("CONTACTED", "QUALIFIED"):
                r = api("put", f"/leads/{lead_id}/progress", token[owner], json={"stage": "lead", "status": status})
                check(f"the lead moves to {status}", r.status_code == 200, f"{r.status_code} {r.text[:120]}")

            r = api("put", f"/leads/{lead_id}/progress", token["zh_south"], json={"stage": "lead", "status": "LOST"})
            check("the other zone cannot progress the lead", r.status_code == 403, f"got {r.status_code}")

            # ------------------------------------------------ opportunity
            r = api("post", "/opportunities/", token[owner], json={
                "lead_id": lead_id,
                "title": f"Greenfield Expansion {TAG}",
                "deal_value": 250000,
                "organization_name": f"Greenfield Farms {TAG}",
                "contact_name": "Ramesh Gupta",
                "email": f"greenfield.{TAG}@mailinator.com",
                "mobile_number": "+91 9812345670",
            })
            check("the lead becomes an opportunity", r.status_code == 200, f"{r.status_code} {r.text[:200]}")

            opportunity = r.json()["data"] if r.status_code == 200 else None
            if opportunity:
                created["opportunities"].append(opportunity["id"])
                visibility("the opportunity", "/opportunities/", opportunity["id"])

                # -------------------------------------------- quotation
                r = api("post", "/quotations/", token[owner], json={
                    "opportunity_id": opportunity["id"],
                    "organization_name": f"Greenfield Farms {TAG}",
                    "contact_name": "Ramesh Gupta",
                    "email": f"greenfield.{TAG}@mailinator.com",
                    "mobile_number": "+91 9812345670",
                    "quotation_date": datetime.now(timezone.utc).isoformat(),
                    "validation_date": (datetime.now(timezone.utc) + timedelta(days=30)).isoformat(),
                    "items": [
                        {"product": "Fertilizer", "model": "NPK 19-19-19", "sku": f"SKU-{TAG}",
                         "quantity": 100, "unit_price": 450, "tax": 18},
                    ],
                })
                check("a quotation is raised against it", r.status_code == 200, f"{r.status_code} {r.text[:200]}")

                quotation = r.json()["data"] if r.status_code == 200 else None
                if quotation:
                    created["quotations"].append(quotation["id"])
                    visibility("the quotation", "/quotations/", quotation["id"])

                    # 100 x 450 = 45,000 before GST.
                    check(
                        "its total is worked out from the line",
                        float(quotation.get("subtotal") or 0) == 45000
                        and float(quotation.get("total_payable") or 0) > 45000,
                        f"subtotal {quotation.get('subtotal')}, payable {quotation.get('total_payable')}",
                    )

                    r = api("get", f"/quotations/{quotation['id']}", token["am_north_2"])
                    check("the Area Manager next to them cannot open it", r.status_code == 403, f"got {r.status_code}")

                    r = api("put", f"/quotations/{quotation['id']}/status", token["zh_north"], json={"status": "SENT"})
                    check("their Zonal Head can move it to Sent", r.status_code == 200, f"{r.status_code} {r.text[:150]}")

                # ------------------------------------------ sales order
                r = api("post", "/orders", token[owner], json={
                    "customer_name": f"Greenfield Farms {TAG}",
                    "opportunity_id": opportunity["id"],
                    "company_name": "Synergy North Agro",
                    "state": "Delhi",
                    "order_date": datetime.now(timezone.utc).isoformat(),
                    "items": [
                        {"product": "Fertilizer", "model": "NPK 19-19-19", "sku": f"SKU-{TAG}",
                         "qty": 100, "rate": 450, "tax_rate": 18},
                    ],
                })
                check("the quotation turns into a sales order", r.status_code == 200, f"{r.status_code} {r.text[:200]}")

                order = r.json()["data"] if r.status_code == 200 else None
                if order:
                    created["orders"].append(order["id"])
                    visibility("the sales order", "/orders", order["id"])

                    # An invoice can only go on a confirmed order.
                    r = api("put", f"/orders/{order['id']}/status", token[owner], json={"status": "CONFIRMED"})
                    check("the order is confirmed", r.status_code == 200, f"{r.status_code} {r.text[:150]}")

                    r = api("post", "/proforma-invoices", token["zh_south"], json={
                        "sales_order_id": order["id"], "status": "DRAFT",
                    })
                    check("the other zone cannot invoice the order", r.status_code == 403, f"got {r.status_code}")
                    if r.status_code == 200:
                        created["invoices"].append(r.json()["data"]["id"])

                    # -------------------------------- proforma invoice
                    r = api("post", "/proforma-invoices", token[owner], json={
                        "sales_order_id": order["id"],
                        "status": "DRAFT",
                    })
                    check("a proforma invoice is raised on the order", r.status_code == 200, f"{r.status_code} {r.text[:200]}")

                    invoice = r.json()["data"] if r.status_code == 200 else None
                    if invoice:
                        created["invoices"].append(invoice["id"])
                        visibility("the proforma invoice", "/proforma-invoices", invoice["id"])

                        r = api("get", f"/proforma-invoices/{invoice['id']}", token["zh_south"])
                        check("the other zone cannot open the invoice", r.status_code == 403, f"got {r.status_code}")

    # ============================================================ products
    banner("6. The same product stocked by both companies")

    if product_type:
        serial = f"NPK-{TAG}"
        code, category = product_type["code"], product_type["category"]

        def add_product(who, company_id, label):
            return api("post", "/inventory/items", token[who], json={
                "name": f"{label} {TAG}", "serial_number": serial,
                "product_type_code": code, "category": category,
                "attributes": {"rate": 450, "unit": "Kg", "instock": 500, "case_size": 50},
                "company_id": company_id,
            })

        r_north = add_product("am_north_1", north, "NPK 19-19-19")
        check("the North Area Manager stocks it for their company", r_north.status_code == 200, f"{r_north.status_code} {r_north.text[:160]}")
        if r_north.status_code == 200:
            created["items"].append(r_north.json()["data"]["_id"])

        r_south = add_product("am_south_1", south, "NPK 19-19-19")
        check("the South company stocks the same serial number", r_south.status_code == 200, f"{r_south.status_code} {r_south.text[:160]}")
        if r_south.status_code == 200:
            created["items"].append(r_south.json()["data"]["_id"])

        r = add_product("am_north_2", north, "Duplicate")
        check("the same serial twice in one company is refused", r.status_code == 400, f"got {r.status_code}")
        if r.status_code == 200:
            created["items"].append(r.json()["data"]["_id"])

        if r_north.status_code == 200 and r_south.status_code == 200:
            id_north = r_north.json()["data"]["_id"]
            id_south = r_south.json()["data"]["_id"]

            def product_ids(who):
                return {row["_id"] for row in rows(api("get", "/inventory/items", token[who]))}

            north_side, south_side = product_ids("am_north_1"), product_ids("am_south_1")

            check("North sees its own copy", id_north in north_side)
            check("North does not see the South copy", id_south not in north_side)
            check("South sees its own copy", id_south in south_side)
            check("South does not see the North copy", id_north not in south_side)
            check("the CEO, who is in both, sees both", {id_north, id_south} <= product_ids("ceo"))
            check(
                "the Zonal Head sees their own zone's copy only",
                id_north in product_ids("zh_north") and id_south not in product_ids("zh_north"),
            )

            r = api("delete", f"/inventory/items/{id_south}", token["am_north_1"])
            check("deleting the other company's product is refused", r.status_code == 403, f"got {r.status_code}")

            offered = rows(api("get", "/inventory/companies", token["am_north_1"]))
            check("the form offers only their own company", [c["id"] for c in offered] == [north], str(offered))

            offered = rows(api("get", "/inventory/companies", token["ceo"]))
            check("someone in both companies is offered both", {north, south} <= {c["id"] for c in offered})

finally:
    banner("7. Clearing what the flow created")
    try:
        admin = login(*ADMIN)

        for item_id in created["items"]:
            api("delete", f"/inventory/items/{item_id}", admin)
        for invoice_id in created["invoices"]:
            api("delete", f"/proforma-invoices/{invoice_id}", admin)
        for order_id in created["orders"]:
            api("delete", f"/orders/{order_id}", admin)
        for customer_id in created["customers"]:
            api("delete", f"/customers/{customer_id}", admin)
        for path, master_id in created["masters"]:
            api("delete", f"/{path}/{master_id}", admin)
        for role_id, permission_id in granted:
            api("delete", f"/rbac/roles/{role_id}/permissions/{permission_id}", admin)

        # Quotations, opportunities and leads have no delete endpoint, so
        # these go straight to the database - by id, so only this run's.
        from sqlalchemy import text

        from app.database.postgres import SessionLocal

        session = SessionLocal()

        def run(sql, **params):
            try:
                session.execute(text(sql), params)
            except Exception as exc:
                session.rollback()
                print("   could not clear:", str(exc).split("\n")[0][:90])

        for table, ids in (
            ("sales_quotation", created["quotations"]),
            ("sales_opportunity", created["opportunities"]),
            ("sales_lead", created["leads"]),
        ):
            if ids:
                run(f"delete from {table}_activity where {table.replace('sales_', '')}_id = any(:ids)", ids=ids)
                run(f"delete from {table} where id = any(:ids)", ids=ids)
        session.commit()

        left = {
            table: session.execute(text(f"select count(*) from {table} where id = any(:ids)"), {"ids": ids or [-1]}).scalar()
            for table, ids in (
                ("sales_quotation", created["quotations"]),
                ("sales_opportunity", created["opportunities"]),
                ("sales_lead", created["leads"]),
            )
        }
        session.close()

        print("   removed " + ", ".join(f"{len(v)} {k}" for k, v in created.items() if v))
        print(f"   untied {len(granted)} permissions that were ticked for the test")
        print("   left behind:", left)
        print("   the team and the two companies were kept")
    except Exception as exc:
        print("   cleanup problem:", exc)

print(f"\n{len(passed)} passed, {len(failed)} failed")
for name in failed:
    print("  failed:", name)
sys.exit(1 if failed else 0)
