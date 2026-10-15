"""Searching a list searches the list, not the page you are looking at.

Four screens show a list somebody searches and turns pages through:
Leads, Opportunity, Proposal and Sales Order. Each of them used to read
every record into the browser and filter the rows it was holding, which
works until there are more records than fit on a page - and then looking
for a customer on page 3 finds nothing and the screen says there are
none.

So each list is now a page at a time, and the search runs over
everything the person is allowed to see before the page is cut. That is
what this checks, the same way for all four: make enough records to need
several pages, search for one that is deliberately not on the first
page, and insist it comes back.

It also insists the count is the count of matches. "Showing 1-10 of 73"
with 2 matches on screen is a pager that lies.

Run seed_sales_team.py first.

    docker exec -w /app backend_app python test_list_paging.py
"""

import sys
import uuid

sys.path.insert(0, "/app")

from seed_sales_team import (  # noqa: E402
    ADMIN_EMAIL,
    ADMIN_PASSWORD,
    api,
    login,
    rows,
)

TAG = uuid.uuid4().hex[:6].upper()

#: Enough to need pages without making the suite slow.
MADE = 25
PAGE = 5

passed, failed = [], []


def check(name, ok, detail=""):
    (passed if ok else failed).append(name)
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"   [{detail}]" if not ok else ""))


def body(response):
    try:
        return response.json()
    except Exception:  # pragma: no cover
        return {"detail": response.text[:200]}


made = {"leads": [], "opportunities": [], "quotations": [], "orders": []}

try:
    admin = login(ADMIN_EMAIL, ADMIN_PASSWORD)

    def paged(path, extra=""):
        return body(api("get", f"{path}?page=1&size={PAGE}{extra}", admin))

    def examine(label, path, needle, make):
        """The same four questions about one list."""

        print(f"\n{label}")

        for n in range(MADE):
            make(n)

        first = paged(path)
        total = first.get("total")

        check(
            "the list comes back a page at a time",
            len(first.get("data") or []) == PAGE,
            f"{len(first.get('data') or [])} rows",
        )
        check(
            "and says how many there are altogether",
            isinstance(total, int) and total >= MADE,
            str(total),
        )

        # The record that is deliberately not on the first page. These
        # lists put the newest first, so it is the one made first.
        wanted = needle(0)

        on_first_page = [
            row for row in (first.get("data") or []) if wanted in str(row)
        ]

        check(
            "the record we will look for is not on the first page",
            not on_first_page,
            wanted,
        )

        found = paged(path, f"&search={wanted}")
        hits = found.get("data") or []

        check(
            "searching for it finds it anyway",
            any(wanted in str(row) for row in hits),
            f"{wanted} -> {len(hits)} rows",
        )
        check(
            "and the count is the count of matches",
            found.get("total") == len(hits) and found.get("total", 0) >= 1,
            f"total={found.get('total')} rows={len(hits)}",
        )

        # Paging has to agree with itself: no record on two pages, and
        # every record reachable.
        seen = []

        for number in range(1, ((total or 0) // PAGE) + 2):
            seen += [
                str(row.get("id"))
                for row in (paged(path, f"&page={number}") or {}).get("data") or []
            ]

        check(
            "every page is a different slice, with nothing repeated",
            len(seen) == len(set(seen)),
            f"{len(seen)} rows, {len(set(seen))} distinct",
        )

        # The figures across the top describe the whole list, not the
        # page - and they narrow with a search, because what is on screen
        # is what they should be describing.
        summary = first.get("summary") or {}
        headline = summary.get("total") or {}
        counted = headline.get("count", headline.get("value"))

        check(
            "the figures across the top count the whole list",
            counted is not None and int(counted) == total,
            f"summary total={counted} list total={total}",
        )

        searched = (found.get("summary") or {}).get("total") or {}
        narrowed = searched.get("count", searched.get("value"))

        check(
            "and they narrow to the search",
            narrowed is not None and int(narrowed) == found.get("total"),
            f"summary={narrowed} total={found.get('total')}",
        )

        empty = paged(path, "&search=zzz-nothing-matches-zzz")

        check(
            "a search that matches nothing says so",
            (empty.get("data") == [] or empty.get("data") is None)
            and empty.get("total") == 0,
            str(empty.get("total")),
        )

    # ------------------------------------------------------------- leads
    def make_lead(n):
        r = api("post", "/leads/", admin, json={
            "title": f"Paging Lead {TAG}-{n:02d}",
            "organization_name": f"Paging Org {TAG}-{n:02d}",
            "contact_name": f"Contact {n:02d}",
            "email": f"paging.{TAG}.{n:02d}@mailinator.com",
            "mobile_number": "9800000001",
            "description": "raised by test_list_paging",
        })

        if r.status_code < 400:
            made["leads"].append((r.json().get("data") or {}).get("id"))

    examine("1. Leads", "/leads/", lambda n: f"Paging Org {TAG}-{n:02d}", make_lead)

    # ----------------------------------------------------- opportunities
    def make_opportunity(n):
        r = api("post", "/opportunities/", admin, json={
            "title": f"Paging Opp {TAG}-{n:02d}",
            "organization_name": f"Paging OppOrg {TAG}-{n:02d}",
            "contact_name": f"Contact {n:02d}",
            "email": f"paging.opp.{TAG}.{n:02d}@mailinator.com",
            "deal_value": 1000 + n,
        })

        if r.status_code < 400:
            made["opportunities"].append((r.json().get("data") or {}).get("id"))

    examine(
        "2. Opportunity",
        "/opportunities/",
        lambda n: f"Paging OppOrg {TAG}-{n:02d}",
        make_opportunity,
    )

    # -------------------------------------------------------- proposals
    def make_quotation(n):
        r = api("post", "/quotations/", admin, json={
            "organization_name": f"Paging Quo {TAG}-{n:02d}",
            "contact_name": f"Contact {n:02d}",
            "email": f"paging.quo.{TAG}.{n:02d}@mailinator.com",
            "status": "DRAFT",
            "items": [],
        })

        if r.status_code < 400:
            made["quotations"].append((r.json().get("data") or {}).get("id"))

    examine(
        "3. Proposal",
        "/quotations/",
        lambda n: f"Paging Quo {TAG}-{n:02d}",
        make_quotation,
    )

    # ------------------------------------------------------ sales orders
    def make_order(n):
        r = api("post", "/orders", admin, json={
            "customer_name": f"Paging Customer {TAG}-{n:02d}",
            "company_name": "Synergy North Agro",
            "state": "Delhi",
            "items": [{
                "product": "Paging Item",
                "sku": f"PAGING-{TAG}-{n:02d}",
                "qty": 1,
                "rate": 100,
                "tax_rate": 18,
            }],
        })

        if r.status_code < 400:
            made["orders"].append((r.json().get("data") or {}).get("id"))

    examine(
        "4. Sales Order",
        "/orders",
        lambda n: f"Paging Customer {TAG}-{n:02d}",
        make_order,
    )

    print("\n5. The whole list is still there for the screens that want it")

    # The pickers - "pick the opportunity this proposal is for" - read a
    # whole list rather than a page, so asking for no page must still
    # answer with all of it.
    everything = body(api("get", "/leads/", admin))

    check(
        "no page asked for means every lead",
        len(everything.get("data") or []) >= MADE,
        str(len(everything.get("data") or [])),
    )

finally:
    print("\n6. Clearing up")

    try:
        admin = login(ADMIN_EMAIL, ADMIN_PASSWORD)

        for path, key in (
            ("/orders", "orders"),
            ("/quotations", "quotations"),
            ("/opportunities", "opportunities"),
            ("/leads", "leads"),
        ):
            gone = 0

            for record_id in made[key]:
                if record_id and api("delete", f"{path}/{record_id}", admin).status_code < 400:
                    gone += 1

            print(f"   removed {gone} {key}")
    except Exception as caught:  # pragma: no cover
        print("   cleanup problem:", caught)

print(f"\n{len(passed)} passed, {len(failed)} failed")

for name in failed:
    print("  -", name)

sys.exit(1 if failed else 0)
