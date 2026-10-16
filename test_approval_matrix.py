"""Who signs a sales order, for every role that can raise one.

The rule, as the business states it:

  * An Area Manager and a Zonal Head own no discount at all. Any discount
    they give goes up the whole line - AVP, then CEO, then the founder.
  * An AVP owns the first 10%. Inside that, nobody signs. Past it, the
    CEO and then the founder.
  * The CEO owns 20% the same way. Past it, the founder.
  * The founder owns any figure, so nothing of theirs needs approval.
  * A super admin stands outside the sales line and needs nobody.

And dealer price is a separate question: selling through the channel is
the founder's call, so a dealer order goes to them whatever the discount
- unless the founder raised it, or a super admin did.

Three things are checked for every combination, because a chain that is
right on paper is still broken if the person it names cannot open the
order, or if an order needing nobody cannot be confirmed:

  1. the chain the order actually got,
  2. that whoever it is waiting on can open it,
  3. that an order needing no approval can be confirmed straight away.

Run seed_sales_team.py first.

    docker exec -w /app backend_app python test_approval_matrix.py
"""

import sys
import uuid

sys.path.insert(0, "/app")

from seed_sales_team import (  # noqa: E402
    ADMIN_EMAIL,
    ADMIN_PASSWORD,
    TEAM_PASSWORD,
    api,
    login,
    rows,
)

TAG = uuid.uuid4().hex[:5].upper()

#: Every role that raises orders, with the demo account that holds it.
RAISERS = [
    ("Area Manager", "am.north.1@mailinator.com"),
    ("Zonal Head", "zh.north@mailinator.com"),
    ("AVP", "avp@mailinator.com"),
    ("CEO", "ceo@mailinator.com"),
    ("Founder", "founder@mailinator.com"),
    ("Super Admin", ADMIN_EMAIL),
]

#: raiser -> case -> the chain the rule asks for.
#: An empty list means nobody signs and the order is theirs to confirm.
EXPECTED = {
    "Area Manager": {
        "ECP 0%": [], "ECP 8%": ["AVP", "CEO", "Founder"],
        "ECP 18%": ["AVP", "CEO", "Founder"], "ECP 25%": ["AVP", "CEO", "Founder"],
        "DTP 0%": ["Founder"], "DTP 25%": ["Founder"],
    },
    "Zonal Head": {
        "ECP 0%": [], "ECP 8%": ["AVP", "CEO", "Founder"],
        "ECP 18%": ["AVP", "CEO", "Founder"], "ECP 25%": ["AVP", "CEO", "Founder"],
        "DTP 0%": ["Founder"], "DTP 25%": ["Founder"],
    },
    # Owns the first 10%.
    "AVP": {
        "ECP 0%": [], "ECP 8%": [],
        "ECP 18%": ["CEO", "Founder"], "ECP 25%": ["CEO", "Founder"],
        "DTP 0%": ["Founder"], "DTP 25%": ["Founder"],
    },
    # Owns the first 20%.
    "CEO": {
        "ECP 0%": [], "ECP 8%": [], "ECP 18%": [], "ECP 25%": ["Founder"],
        "DTP 0%": ["Founder"], "DTP 25%": ["Founder"],
    },
    # Owns any figure, and dealer price is their own call.
    "Founder": {
        "ECP 0%": [], "ECP 8%": [], "ECP 18%": [], "ECP 25%": [],
        "DTP 0%": [], "DTP 25%": [],
    },
    # Outside the sales line.
    "Super Admin": {
        "ECP 0%": [], "ECP 8%": [], "ECP 18%": [], "ECP 25%": [],
        "DTP 0%": [], "DTP 25%": [],
    },
}

CASES = [
    ("ECP 0%", "ECP", 0),
    ("ECP 8%", "ECP", 8),
    ("ECP 18%", "ECP", 18),
    ("ECP 25%", "ECP", 25),
    ("DTP 0%", "DP", 0),
    ("DTP 25%", "DP", 25),
]

passed, failed = [], []
made = []


def check(name, ok, detail=""):
    (passed if ok else failed).append(name)
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"   [{detail}]" if not ok else ""))


try:
    admin = login(ADMIN_EMAIL, ADMIN_PASSWORD)

    tokens = {}

    for role, email in RAISERS:
        try:
            tokens[role] = (
                admin if email == ADMIN_EMAIL else login(email, TEAM_PASSWORD)
            )
        except Exception:
            print(f"  cannot sign in as {email} - run seed_sales_team.py")

    for role, _ in RAISERS:
        if role not in tokens:
            continue

        print(f"\n{role} raises an order")

        who = tokens[role]

        for label, price_type, discount in CASES:
            expected = EXPECTED[role][label]

            order = api("post", "/orders", who, json={
                "customer_name": f"Matrix {TAG} {role}",
                "company_name": "Synergy North Agro",
                "customer_type": "Dealer" if price_type == "DP" else "End Customer",
                "state": "Delhi",
                "items": [{
                    "product": "Panel",
                    "sku": f"MATRIX-{TAG}",
                    "qty": 1,
                    "rate": 100000,
                    "tax_rate": 18,
                    # The field the form sends and the order stores. The
                    # guard reads the deepest line discount off the saved
                    # order, so a test writing it under another name
                    # would be testing nothing.
                    "discount": discount,
                }],
            })

            placed = (order.json() or {}).get("data") or {}

            if not placed.get("id"):
                check(f"{label}: the order is raised", False,
                      f"{order.status_code} {order.text[:90]}")
                continue

            made.append(placed["id"])

            up = api("post", "/approvals", who, json={
                "document_type": "SALES_ORDER",
                "document_id": placed["id"],
                "document_number": placed.get("order_number"),
                "price_type": price_type,
                "discount_percent": discount,
                "document_value": placed.get("grand_total") or 0,
            })

            approval = (up.json() or {}).get("data") or {}

            # An approval that needed nobody is recorded as approved, not
            # left absent: the order still has to be confirmable, and a
            # sign-off nobody can point at is not a sign-off.
            chain = [
                step["role"]
                for step in approval.get("steps", [])
                if step.get("decision") is None
            ]

            check(
                f"{label}: {' then '.join(expected) if expected else 'nobody signs'}",
                chain == expected,
                f"got {chain or 'nobody'}",
            )

            if expected:
                # Whoever it is waiting on has to be able to open it.
                waiting = chain[0]

                if waiting in tokens:
                    opened = api("get", f"/orders/{placed['id']}", tokens[waiting])

                    check(
                        f"{label}: the {waiting} can open what they must decide",
                        opened.status_code == 200,
                        str(opened.status_code),
                    )

                held = api("put", f"/orders/{placed['id']}/status", who,
                           json={"status": "CONFIRMED"})

                check(
                    f"{label}: and it cannot be confirmed until they have",
                    held.status_code == 409,
                    str(held.status_code),
                )
            else:
                # Needing nobody has to mean it can actually move.
                confirmed = api("put", f"/orders/{placed['id']}/status", who,
                                json={"status": "CONFIRMED"})

                check(
                    f"{label}: and the order confirms without asking anyone",
                    confirmed.status_code == 200,
                    f"{confirmed.status_code} {confirmed.text[:110]}",
                )

finally:
    print("\nClearing up")

    try:
        admin = login(ADMIN_EMAIL, ADMIN_PASSWORD)

        gone = sum(
            1
            for order_id in made
            if api("delete", f"/orders/{order_id}", admin).status_code < 400
        )

        print(f"   removed {gone} of {len(made)} orders")
    except Exception as caught:  # pragma: no cover
        print("   cleanup problem:", caught)

print(f"\n{len(passed)} passed, {len(failed)} failed")

for name in failed:
    print("  -", name)

sys.exit(1 if failed else 0)
