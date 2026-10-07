"""Put the founder at the top of the chart, and give them the desk.

The discount bands have named a founder since they were written, but no
such role existed: an order past the CEO's ceiling, or any dealer order,
named an approver the database did not have. It fell through to the super
admins, which works and says nothing about who actually owns the decision.

This creates the role, places it above the CEO on the Sales chart - which
is what role_levels reads to decide seniority - and grants it what the CEO
holds plus the approval desk.

Safe to run again: everything is checked before it is added.

    docker exec -w /app backend_app python seed_founder_role.py
"""

import sys

from app.core.approvals import FOUNDER
from app.database.postgres import SessionLocal
from app.models.role import Role
from app.models.role_permission import RolePermission
from app.models.workflow import Workflow

#: The chart the sales hierarchy is read from.
CHART = "Sales"


def ensure_role(db) -> Role:
    role = db.query(Role).filter(Role.role_name == FOUNDER).first()

    if role:
        print(f"  role {FOUNDER} already exists")
        return role

    role = Role(
        role_name=FOUNDER,
        description=(
            "Signs what nobody below can: discounts past the CEO's ceiling, "
            "and every order written at dealer transfer price."
        ),
    )

    db.add(role)
    db.commit()
    db.refresh(role)

    print(f"  created role {FOUNDER}")

    return role


def ensure_top_of_chart(db, role: Role) -> None:
    """Place the founder above the CEO, so seniority reads correctly.

    role_levels walks these edges to work out who outranks whom, and the
    chain inserts an approver by that ranking. A founder who is not on the
    chart has no level, and sorts as though they were nobody.
    """

    chart = db.query(Workflow).filter(Workflow.name == CHART).first()

    if chart is None:
        print(f"  no {CHART} chart to place them on - skipped")
        return

    nodes = list(chart.nodes or [])
    edges = list(chart.edges or [])

    if any((n.get("data") or {}).get("role_id") == str(role.id) for n in nodes):
        print(f"  {FOUNDER} is already on the {CHART} chart")
        return

    ceo = next(
        (n for n in nodes if (n.get("data") or {}).get("label") == "CEO"), None
    )

    node_id = f"node-{FOUNDER.lower()}"

    # Above the CEO, so the chart reads the way the authority runs.
    nodes.insert(
        0,
        {
            "id": node_id,
            "x": (ceo or {}).get("x", 240),
            "y": (ceo or {}).get("y", 80) - 130,
            "data": {"label": FOUNDER, "role_id": str(role.id)},
        },
    )

    if ceo:
        edges.insert(
            0,
            {
                "id": f"edge-{FOUNDER.lower()}",
                "source": node_id,
                "target": ceo["id"],
            },
        )

    # Reassigned rather than mutated: SQLAlchemy compares the new value
    # against the old one, and an in-place edit is the same object.
    chart.nodes = nodes
    chart.edges = edges

    db.commit()

    print(f"  placed {FOUNDER} above the CEO on the {CHART} chart")


def ensure_permissions(db, role: Role) -> None:
    """Whatever the CEO holds, plus the approval desk."""

    ceo = db.query(Role).filter(Role.role_name == "CEO").first()

    if ceo is None:
        print("  no CEO role to copy permissions from - skipped")
        return

    theirs = {
        str(r.permission_id)
        for r in db.query(RolePermission).filter(RolePermission.role_id == ceo.id)
    }

    held = {
        str(r.permission_id)
        for r in db.query(RolePermission).filter(RolePermission.role_id == role.id)
    }

    added = 0

    for permission_id in sorted(theirs - held):
        db.add(RolePermission(role_id=role.id, permission_id=permission_id))
        added += 1

    db.commit()

    print(f"  granted {added} permissions ({len(theirs | held)} held in total)")


def main() -> int:
    db = SessionLocal()

    print(f"Setting up {FOUNDER}\n")

    role = ensure_role(db)
    ensure_top_of_chart(db, role)
    ensure_permissions(db, role)

    print(
        f"\n{FOUNDER} now signs discounts past the CEO's ceiling and every "
        "dealer order.\nCreate a user and give them the role to man the desk."
    )

    return 0


if __name__ == "__main__":
    sys.exit(main())
