"""Seed a Sales team that fills out the hierarchy chart.

                         CEO
                          |
                         AVP
                   /            \\
        Zonal Head North     Zonal Head South
          /         \\                |
   Area Manager  Area Manager   Area Manager

Each person reports to the one above, which is what decides whose records
they see: a Zonal Head sees their own Area Managers' leads, opportunities
and orders, and never the other Zonal Head's.

Two companies are seeded with them so products can be kept apart: North
people are in one, South people in the other, and the CEO and AVP are in
both.

Safe to run again - anyone who already exists is left alone.

    docker exec -w /app backend_app python seed_sales_team.py
"""

import sys

import requests

BASE = "http://localhost:8000/api/v1"
ADMIN_EMAIL = "syn-crm-9f3a2@mailinator.com"
ADMIN_PASSWORD = "password123"

#: Everyone seeded here shares one password, so the team can be tried out
#: from the login screen. Change it before this reaches anywhere shared.
TEAM_PASSWORD = "Synergy@123"

COMPANIES = [
    {
        "key": "north",
        "company_name": "Synergy North Agro",
        "company_code": "SYN-NORTH",
        "email": "north.synergy@mailinator.com",
        "phone_number": "9811000001",
        "address_line_1": "12 Ring Road",
        "city": "Delhi",
        "state": "Delhi",
        "country": "India",
        "postal_code": "110001",
    },
    {
        "key": "south",
        "company_name": "Synergy South Seeds",
        "company_code": "SYN-SOUTH",
        "email": "south.synergy@mailinator.com",
        "phone_number": "9811000002",
        "address_line_1": "40 MG Road",
        "city": "Bengaluru",
        "state": "Karnataka",
        "country": "India",
        "postal_code": "560001",
    },
]

#: key, name, role, who they report to, which companies they work for.
TEAM = [
    ("ceo", "Arjun", "Sethi", "CEO", None, ["north", "south"]),
    ("avp", "Rohit", "Malhotra", "AVP", "ceo", ["north", "south"]),
    ("zh_north", "Priya", "Nair", "Zonal Head", "avp", ["north"]),
    ("zh_south", "Sanjay", "Mehta", "Zonal Head", "avp", ["south"]),
    ("am_north_1", "Anil", "Kumar", "Area Manager", "zh_north", ["north"]),
    ("am_north_2", "Deepa", "Iyer", "Area Manager", "zh_north", ["north"]),
    ("am_south_1", "Vikram", "Rao", "Area Manager", "zh_south", ["south"]),
]


def login(email, password):
    r = requests.post(f"{BASE}/auth/login", json={"email": email, "password": password})
    r.raise_for_status()
    body = r.json()
    return body.get("data", body)["access_token"]


def api(method, path, token, **kw):
    return requests.request(
        method, f"{BASE}{path}", headers={"Authorization": f"Bearer {token}"}, **kw
    )


def rows(response):
    body = response.json()
    return body if isinstance(body, list) else body.get("data", [])


def email_for(key):
    return f"{key.replace('_', '.')}@synergy-demo.mailinator.com"


def seed(token) -> dict:
    """Create anything missing and return {key: id} for people and companies."""

    ids: dict[str, str] = {}

    # ---------------------------------------------------------- companies
    existing = {c["company_code"]: c["id"] for c in rows(api("get", "/companies/", token, params={"size": 200}))}

    for company in COMPANIES:
        key = company["key"]
        code = company["company_code"]

        if code in existing:
            ids[key] = existing[code]
            print(f"  company {company['company_name']} already there")
            continue

        payload = {k: v for k, v in company.items() if k != "key"}
        r = api("post", "/companies/", token, json=payload)
        if r.status_code >= 400:
            raise SystemExit(f"could not create {company['company_name']}: {r.status_code} {r.text}")

        ids[key] = r.json().get("data", r.json())["id"]
        print(f"  created company {company['company_name']}")

    # -------------------------------------------------------------- roles
    roles = {r["role_name"]: r["id"] for r in rows(api("get", "/rbac/roles", token))}
    missing = {role for _, _, _, role, _, _ in TEAM} - set(roles)
    if missing:
        raise SystemExit(f"missing roles: {', '.join(sorted(missing))}. Run check_and_seed_db.py first.")

    # --------------------------------------------------------------- team
    people = {u["email"]: u["id"] for u in rows(api("get", "/users/", token, params={"size": 500}))}

    for key, first, last, role, manager_key, companies in TEAM:
        email = email_for(key)

        if email in people:
            ids[key] = people[email]
            print(f"  {first} {last} ({role}) already there")
            continue

        payload = {
            "first_name": first,
            "last_name": last,
            "email": email,
            "password": TEAM_PASSWORD,
            "phone_number": "9800000000",
            "employee_id": f"EMP-{key.upper().replace('_', '-')}",
            "role_ids": [roles[role]],
            "company_ids": [ids[c] for c in companies],
            "reports_to_id": ids.get(manager_key) if manager_key else None,
        }

        r = api("post", "/users/", token, json=payload)
        if r.status_code >= 400:
            raise SystemExit(f"could not create {first} {last}: {r.status_code} {r.text}")

        ids[key] = r.json().get("data", r.json())["id"]
        manager = f", reporting to {manager_key}" if manager_key else ""
        print(f"  created {first} {last} - {role}{manager}")

    return ids


#: One lead each, so there is something on the Leads page to look at. The
#: owner is who creates it, which is what decides who else can see it.
DEMO_LEADS = [
    ("am_north_1", "Greenfield Farms", "Ramesh Gupta", "Delhi", True),
    ("am_north_2", "Riverside Agro", "Meena Joshi", "Jaipur", False),
    ("am_south_1", "Coastal Seeds", "Suresh Pillai", "Kochi", False),
]


def seed_demo_records() -> None:
    """A lead for each Area Manager, and one carried on to an opportunity.

    Created by the Area Manager themselves, so the Leads page shows the
    hierarchy at work: a super admin sees all three, Zonal Head North sees
    their two, Zonal Head South sees the one from their zone.
    """

    tokens = {key: login(email_for(key), TEAM_PASSWORD) for key, *_ in DEMO_LEADS}

    # Whatever is already there, so running again does not pile up copies.
    admin = login(ADMIN_EMAIL, ADMIN_PASSWORD)
    existing = {row.get("organization_name") for row in rows(api("get", "/leads/", admin))}

    for key, org, contact, city, carry_on in DEMO_LEADS:
        if org in existing:
            print(f"  lead for {org} already there")
            continue

        token = tokens[key]
        slug = org.lower().replace(" ", "")

        r = api("post", "/leads/", token, json={
            "title": f"{org} - enquiry",
            "organization_name": org,
            "contact_name": contact,
            "email": f"{slug}@mailinator.com",
            "mobile_number": "+91 9812345670",
            "city": city,
            "country": "India",
        })
        if r.status_code >= 400:
            print(f"  could not create the lead for {org}: {r.status_code} {r.text[:150]}")
            continue

        lead_id = r.json().get("data", {}).get("id")
        print(f"  created lead {org}, by {key}")

        if not carry_on:
            continue

        # Take one all the way to an opportunity so the later pages are not
        # empty either.
        for status in ("CONTACTED", "QUALIFIED"):
            api("put", f"/leads/{lead_id}/progress", token, json={"stage": "lead", "status": status})

        r = api("post", "/opportunities/", token, json={
            "lead_id": lead_id,
            "title": f"{org} expansion",
            "deal_value": 250000,
            "organization_name": org,
            "contact_name": contact,
            "email": f"{slug}@mailinator.com",
            "mobile_number": "+91 9812345670",
        })
        if r.status_code >= 400:
            print(f"  could not open the opportunity for {org}: {r.status_code} {r.text[:150]}")
        else:
            print(f"  carried {org} through to an opportunity")


if __name__ == "__main__":
    print("Seeding the Sales team\n")
    token = login(ADMIN_EMAIL, ADMIN_PASSWORD)
    ids = seed(token)

    print("\nSeeding a lead each, so the pages are not empty\n")
    seed_demo_records()

    print("\nSign in with any of these, password " + TEAM_PASSWORD + ":")
    for key, first, last, role, _, companies in TEAM:
        print(f"  {email_for(key):<45} {first} {last:<12} {role:<13} {', '.join(companies)}")

    sys.exit(0)
