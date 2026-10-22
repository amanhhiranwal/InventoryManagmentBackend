"""A lead saved from an unfinished form.

Save as Draft used to close the page and throw the form away, which
looked exactly like a save. A draft is now a real lead with the status
DRAFT: it is in the list so the person who started it can find it, and
outside the pipeline so the sales team is not asked to work it.

What this pins down:

  * a lead can be created with almost nothing filled in, as long as it
    is marked DRAFT - that is the whole point of a draft
  * the draft is in the list, carrying DRAFT
  * it is not counted as a new lead on the cards
  * finishing it promotes it to NEW, and the card follows
  * a draft cannot skip into the middle of the pipeline: not Contacted,
    not Qualified, because nobody has worked it
  * it can be dropped

    docker exec -w /app backend_app python test_lead_draft.py
"""

import sys
import uuid

from seed_sales_team import api, login, rows

ADMIN = ("superadmin@mailinator.com", "password123")
TAG = uuid.uuid4().hex[:6]

passed = 0
failed = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global passed, failed

    if ok:
        passed += 1
        print(f"  PASS  {label}")
    else:
        failed += 1
        print(f"  FAIL  {label}" + (f" - {detail}" if detail else ""))


def card(token, name: str) -> int:
    """One of the four figures across the top of the Leads screen.

    The summary rides along with the list rather than having an endpoint
    of its own, which is how the screen gets both in one request.
    """

    body = api("get", "/leads/", token, params={"size": 1}).json()

    entry = (body.get("summary") or {}).get(name) or {}

    return int(entry.get("count") or 0)


def main() -> int:
    token = login(*ADMIN)

    if not token:
        print("Could not sign in as the super admin.")
        return 1

    print("\n1. A draft is saved from a form with almost nothing in it")

    new_before = card(token, "new")

    response = api(
        "post",
        "/leads",
        token,
        json={
            # Everything a half-filled form would send: a name and
            # nothing else that matters.
            "organization_name": f"Halfway Traders {TAG}",
            "status": "DRAFT",
        },
    )

    body = response.json() if response.status_code < 400 else {}
    lead = body.get("data", body) or {}
    lead_id = lead.get("id")

    check("the draft was created", bool(lead_id), response.text[:200])

    if not lead_id:
        return 1

    check(
        "it carries the status DRAFT",
        str(lead.get("status", "")).upper() == "DRAFT",
        f"status was {lead.get('status')!r}",
    )

    print("\n2. It is in the list, and not on the New card")

    listed = rows(api("get", "/leads/", token, params={"size": 200}))

    mine = next((r for r in listed if str(r.get("id")) == str(lead_id)), None)

    check("the draft appears in the list", mine is not None)

    check(
        "the list still calls it DRAFT",
        bool(mine) and str(mine.get("status", "")).upper() == "DRAFT",
        f"status was {(mine or {}).get('status')!r}",
    )

    check(
        "it did not raise the New count",
        card(token, "new") == new_before,
        f"new went {new_before} -> {card(token, 'new')}",
    )

    print("\n3. A draft cannot skip into the middle of the pipeline")

    for refused in ("CONTACTED", "QUALIFIED"):
        answer = api(
            "put",
            f"/leads/{lead_id}/progress",
            token,
            json={"status": refused},
        )

        check(
            f"a draft cannot be marked {refused.title()}",
            answer.status_code >= 400,
            f"got {answer.status_code}: {answer.text[:120]}",
        )

    print("\n4. Finishing it makes it a lead")

    api(
        "put",
        f"/leads/{lead_id}",
        token,
        json={
            "contact_name": "Asha Rane",
            "organization_name": f"Halfway Traders {TAG}",
            "email": f"asha.{TAG}@halfway.example",
            "mobile_number": "9812345670",
            "status": "NEW",
        },
    )

    body = api("get", f"/leads/{lead_id}", token).json()
    after = body.get("data", body) or {}

    check(
        "the draft is now a New lead",
        str(after.get("status", "")).upper() == "NEW",
        f"status was {after.get('status')!r}",
    )

    check(
        "and the New card counts it",
        card(token, "new") == new_before + 1,
        f"new went {new_before} -> {card(token, 'new')}",
    )

    print("\n5. Clearing up")

    api("delete", f"/leads/{lead_id}", token)

    left = [
        r
        for r in rows(api("get", "/leads/", token, params={"size": 200}))
        if TAG in str(r.get("organization_name", ""))
    ]

    print(f"   left behind: {len(left)}")

    print(f"\n{passed} passed, {failed} failed")

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
