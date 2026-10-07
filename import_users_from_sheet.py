"""Create the sales team's logins from the staffing sheet.

The sheet is the one HR keeps - name, designation, email, phone, employee
ID, and who each person reports to - and this turns a row of it into an
account with the right role and the right manager above it.

It goes through the API rather than the tables, so an import is held to
the same permissions, the same field checks and the same password hashing
as somebody typing the form. A row the API refuses costs only that row.

Two things the sheet cannot settle on its own:

  * Designations are written the way the business says them. "Zonal
    Manager" is the role the CRM calls "Zonal Head"; DESIGNATIONS below
    is that translation, and a designation missing from it stops the row
    rather than guessing.

  * "Reports To" is a typed name, so it carries typos - "Siddhartha Nega"
    for Negi, "Pradeep Keshari" for Kesari. An exact match is taken; a
    near-miss is reported and linked; a name that is nobody in the sheet
    and nobody on file is left unset, because inventing a manager puts a
    person's approvals in front of the wrong desk.

Passwords are generated here, one per person, and written to a file you
name. They are never printed, so the run can be read over a shoulder.

    python import_users_from_sheet.py users.xls --out credentials.csv
    python import_users_from_sheet.py users.xls --dry-run
"""

import argparse
import csv
import os
import re
import secrets
import string
import sys
from difflib import SequenceMatcher
from urllib.parse import urlparse

import requests

DEFAULT_BASE = "http://localhost:8000/api/v1"

#: The seeded local admin. It is a known pair committed to this repository,
#: so it is only ever offered to a server running on this machine.
LOCAL_ADMIN = ("superadmin@mailinator.com", "password123")

LOCAL_HOSTS = ("localhost", "127.0.0.1", "[::1]", "::1")

#: What the business calls a job, against what the CRM calls the role.
DESIGNATIONS = {
    "area manager": "Area Manager",
    "area manager business": "Area Manager",
    "zonal manager": "Zonal Head",
    "zonal head": "Zonal Head",
    "regional manager": "Zonal Head",
    "avp": "AVP",
    "ceo": "CEO",
    "group ceo": "CEO",
    "founder": "Founder",
    "accounts": "Accounts",
    "inventory": "Inventory",
}

#: Long enough to be worth having, short enough to read down a phone.
PASSWORD_LENGTH = 14

#: How alike two names must be before one is read as the other. The typos
#: this sheet actually carries score .93 and .97 ("Nega" for Negi,
#: "Keshari" for Kesari); the closest pair of genuinely different people
#: scores .69 ("Arvind Singh" against "Mahendra Singh"). The floor sits in
#: that gap, wide of both, so a near-miss is a typo and not a colleague.
WHOLE_NAME_FLOOR = 0.85
FIRST_NAME_FLOOR = 0.80


def similar(a, b):
    return SequenceMatcher(None, a, b).ratio()


def is_local(base):
    return urlparse(base).hostname in LOCAL_HOSTS


def credentials(base):
    """Who to sign in as, and from where.

    Against a server on this machine the seeded admin will do. Against
    anything else - staging, production - the pair has to come from the
    environment, because the seeded one is published in this repository
    and must never be the thing holding a live system shut.
    """

    email = os.environ.get("CRM_ADMIN_EMAIL")
    password = os.environ.get("CRM_ADMIN_PASSWORD")

    if email and password:
        return email, password

    if is_local(base):
        return LOCAL_ADMIN

    sys.exit(
        f"{base} is not a server on this machine, so the seeded admin login\n"
        "is not offered for it. Set the credentials in the environment:\n\n"
        "    export CRM_ADMIN_EMAIL='you@yourcompany.com'\n"
        "    read -rs CRM_ADMIN_PASSWORD && export CRM_ADMIN_PASSWORD\n"
    )


def login(base, email, password):
    response = requests.post(
        f"{base}/auth/login", json={"email": email, "password": password}
    )

    if response.status_code == 401:
        sys.exit(f"{base} refused that login.")

    response.raise_for_status()
    body = response.json()
    return body.get("data", body)["access_token"]


def api(method, path, token, base, **kw):
    return requests.request(
        method, f"{base}{path}", headers={"Authorization": f"Bearer {token}"}, **kw
    )


def rows(response):
    body = response.json()
    return body if isinstance(body, list) else body.get("data", [])


def phone_digits(value):
    """The ten digits of a mobile number, however the sheet holds it.

    A number typed into a spreadsheet cell comes back as a float, so
    "9602416077" reads as "9602416077.0" and strips to eleven digits with
    a zero on the end. The trailing ".0" goes before anything else does.
    """

    text = str(value or "").strip()

    if re.fullmatch(r"\d+\.0", text):
        text = text[:-2]

    return re.sub(r"\D", "", text)


def make_password():
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(PASSWORD_LENGTH))


#: The same place written two ways in one sheet. Left as a small, visible
#: list rather than a clever rule: "Delhi/NCR" and "Delhi-NCR" are the
#: same desk, and nothing else here is close enough to risk merging.
LOCATION_ALIASES = {
    "delhi-ncr": "Delhi/NCR",
    "delhi ncr": "Delhi/NCR",
    "delhi/ncr": "Delhi/NCR",
}


def tidy_location(value) -> str:
    text = str(value or "").strip()

    return LOCATION_ALIASES.get(text.lower(), text)


def split_name(full):
    """"Vikas pundir" -> ("Vikas", "Pundir"), one word -> no surname."""

    parts = [p for p in str(full or "").split() if p]

    if not parts:
        return "", ""

    first, *rest = parts
    return first.title(), " ".join(rest).title()


class _Sheet:
    """The two spreadsheet formats behind one small interface.

    .xls is read by xlrd and .xlsx by openpyxl; neither reads the other,
    and HR sends whichever their machine saved.
    """

    def __init__(self, rows):
        self.rows = rows
        self.nrows = len(rows)
        self.ncols = max((len(r) for r in rows), default=0)

    def value(self, row, col):
        line = self.rows[row]
        return line[col] if col < len(line) else ""


def read_sheet(path):
    if str(path).lower().endswith(".xlsx"):
        import openpyxl

        ws = openpyxl.load_workbook(path, data_only=True).worksheets[0]
        rows = [list(r) for r in ws.iter_rows(values_only=True)]
    else:
        import xlrd

        book = xlrd.open_workbook(path).sheet_by_index(0)
        rows = [
            [book.cell(r, c).value for c in range(book.ncols)]
            for r in range(book.nrows)
        ]

    # A sheet that opens with blank or decorative rows starts at the first
    # row that actually names columns.
    start = 0
    for i, row in enumerate(rows[:10]):
        text = " ".join(str(v or "").lower() for v in row)
        if "name" in text and ("email" in text or "designation" in text):
            start = i
            break

    sheet = _Sheet(rows[start:])
    headers = [
        str(sheet.value(0, c) or "").strip().lower() for c in range(sheet.ncols)
    ]

    def column(*names):
        for name in names:
            if name in headers:
                return headers.index(name)
        return None

    index = {
        "name": column("name", "full name"),
        "designation": column("designation", "role"),
        "email": column("email address", "email id", "email"),
        "phone": column("phone number", "mobile", "phone", "contact no.", "contact no"),
        "employee": column("employee id", "employee code", "emp id"),
        # L1 is the person's first line manager. L2 and L3 are the rest of
        # the chain, which the reporting line gives us for free once L1 is
        # set, so only L1 is read.
        "manager": column("reports to", "manager", "l1"),
    }

    #: Optional: not every sheet records where somebody is based.
    index["location"] = column("location", "base", "city")

    missing = [
        key for key, value in index.items() if value is None and key != "location"
    ]
    if missing:
        sys.exit(f"The sheet has no column for: {', '.join(missing)}")

    people = []
    for r in range(1, sheet.nrows):
        def cell(key):
            at = index.get(key)

            if at is None:
                return ""

            return str(sheet.value(r, at) or "").strip()

        if not cell("name"):
            continue

        people.append(
            {
                "row": r + 1,
                "name": cell("name"),
                "designation": cell("designation"),
                "email": cell("email").lower(),
                "phone": phone_digits(sheet.value(r, index["phone"])),
                "employee_id": cell("employee"),
                "manager": cell("manager").lstrip("-").strip(),
                "location": tidy_location(cell("location")),
            }
        )

    return people


def match_name(wanted, candidates):
    """The candidate a typed name means, or None.

    Exact first. Failing that, a matching first name and a surname that
    starts the same way - enough to catch "Negi" typed "Nega", not enough
    to pair two different people. Used both to order the import and to
    point a report at a manager, so the two always agree.
    """

    want = str(wanted or "").strip().lower()

    if not want:
        return None

    if want in candidates:
        return want

    parts = want.split()

    # A name written short. "Leo Nelson" for Leo Nelson Paul, "Mahendra"
    # for Mahendra Singh Kachhawaha, "Pradeep" for Pradeep Kesari - the
    # sheet does this constantly, and it is not a typo but an
    # abbreviation. Every word present, in order, and only one candidate
    # it can mean: two people called Mahendra would make it ambiguous, and
    # an ambiguous manager is worse than none.
    prefixed = [
        name
        for name in candidates
        if name.split()[: len(parts)] == parts and len(name.split()) >= len(parts)
    ]

    if len(prefixed) == 1:
        return prefixed[0]

    if len(parts) < 2:
        return None

    best, score = None, 0.0

    for name in candidates:
        have = name.split()

        if len(have) < 2:
            continue

        # A shared surname is common here - Singh appears three times - so
        # the given name has to agree before the whole name is weighed.
        if similar(parts[0], have[0]) < FIRST_NAME_FLOOR:
            continue

        whole = similar(want, name)

        if whole > score:
            best, score = name, whole

    return best if score >= WHOLE_NAME_FLOOR else None


def _fill_gaps(existing, person, by_name, args, token) -> str:
    """Add what the account is missing, and change nothing it already has.

    An account made on an earlier run may predate columns the sheet now
    carries - where somebody is based, who they report to. Skipping it
    outright means those never arrive, so the second run finishes what
    the first started. Anything already set is left exactly as it is.
    """

    wants: dict[str, object] = {}

    if person.get("location") and not existing.get("location"):
        wants["location"] = person["location"]

    manager_label = None

    if person["manager"] and not existing.get("reports_to_id"):
        key = match_name(person["manager"], by_name)
        manager = by_name.get(key) if key else None

        if manager and not str(manager.get("id", "")).startswith("dry-"):
            wants["reports_to_id"] = manager["id"]
            manager_label = manager["_label"]

    if not wants:
        return ""

    said = []
    if "location" in wants:
        said.append(f"location {wants['location']}")
    if "reports_to_id" in wants:
        said.append(f"reports to {manager_label}")

    if args.dry_run:
        return "would set " + " and ".join(said)

    payload = {
        "first_name": existing.get("first_name") or person["first"],
        "last_name": existing.get("last_name") or person["last"],
        "phone_number": existing.get("phone_number") or person["phone"],
        "employee_id": existing.get("employee_id") or person["employee_id"],
        # The listing returns role_ids and company_ids, not nested
        # objects. Reading the wrong key here sent an empty list, and a
        # PUT with an empty role list takes every role off the account.
        "role_ids": list(existing.get("role_ids") or []),
        "company_ids": list(existing.get("company_ids") or []),
        "location": wants.get("location", existing.get("location")),
        "reports_to_id": wants.get("reports_to_id", existing.get("reports_to_id")),
    }

    response = api(
        "PUT", f"/users/{existing['id']}", token, args.base_url, json=payload
    )

    if response.status_code >= 400:
        return f"could not set {' and '.join(said)}"

    existing.update(
        {k: v for k, v in payload.items() if k in ("location", "reports_to_id")}
    )

    return "set " + " and ".join(said)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sheet", help="the .xls staffing sheet")
    parser.add_argument("--out", help="where to write the generated passwords")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="say what would be created, create nothing",
    )
    parser.add_argument(
        "--base-url",
        default=DEFAULT_BASE,
        help=f"the API to create them on (default {DEFAULT_BASE})",
    )
    args = parser.parse_args()

    args.base_url = args.base_url.rstrip("/")

    if not is_local(args.base_url) and not args.dry_run:
        print(f"About to create logins on {args.base_url}, which is not this machine.")
        print("The passwords written out are real credentials for real people.\n")

        if input("Type the host to go ahead: ").strip() != urlparse(args.base_url).hostname:
            sys.exit("Stopped.")

        print()

    if not args.dry_run and not args.out:
        sys.exit("Pass --out so the generated passwords are written somewhere.")

    people = read_sheet(args.sheet)
    print(f"{len(people)} people in {args.sheet}\n")

    email, password = credentials(args.base_url)
    token = login(args.base_url, email, password)

    roles = {r["role_name"].lower(): r["id"] for r in rows(api("GET", "/roles/", token, args.base_url))}
    existing = rows(api("GET", "/users/?page=1&size=500", token, args.base_url))

    by_email = {u["email"].lower(): u for u in existing if u.get("email")}
    by_name = {}
    for u in existing:
        label = f"{u.get('first_name','')} {u.get('last_name','')}".strip()
        by_name[label.lower()] = {**u, "_label": label}

    created, skipped, failed = [], [], []

    # Settle each person's own name once, then read "Reports To" against
    # that list, so a manager written with a typo still finds its person.
    for person in people:
        first, last = split_name(person["name"])
        person["label"] = f"{first} {last}".strip()
        person["first"], person["last"] = first, last

    sheet_names = {p["label"].lower(): p for p in people}

    for person in people:
        person["manager_key"] = match_name(person["manager"], sheet_names)

    # Managers before reports, so the person above exists to be pointed at.
    depth = {p["label"].lower(): 0 for p in people}
    for _ in range(len(people)):
        for p in people:
            key = p["manager_key"]
            if key:
                depth[p["label"].lower()] = max(
                    depth[p["label"].lower()], depth[key] + 1
                )

    for person in sorted(people, key=lambda p: depth[p["label"].lower()]):
        notes = []
        first, last, label = person["first"], person["last"], person["label"]

        if person["email"] in by_email:
            # The account exists, but the sheet may carry things it does
            # not: where somebody is based, or who they report to. Those
            # are filled in rather than skipped over - an import that
            # refuses to finish a half-made account is of no use the
            # second time it is run. Nothing already set is overwritten.
            existing = by_email[person["email"]]
            filled = _fill_gaps(existing, person, by_name, args, token)

            skipped.append(
                (person, "already has a login" + (f"; {filled}" if filled else ""))
            )

            by_name[label.lower()] = {**existing, "_label": label}
            continue

        role_name = DESIGNATIONS.get(person["designation"].strip().lower())
        if not role_name:
            failed.append((person, f"designation “{person['designation']}” is not a role"))
            continue

        role_id = roles.get(role_name.lower())
        if not role_id:
            failed.append((person, f"the CRM has no “{role_name}” role"))
            continue

        if len(person["phone"]) != 10:
            failed.append(
                (person, f"phone “{person['phone']}” is {len(person['phone'])} digits")
            )
            continue

        manager = None

        if person["manager"]:
            key = match_name(person["manager"], by_name)
            manager = by_name.get(key) if key else None

            if manager and key != person["manager"].strip().lower():
                notes.append(f"read “{person['manager']}” as “{manager['_label']}”")

            if not manager:
                notes.append(
                    f"“{person['manager']}” is nobody in the sheet or on file"
                    " - left unset"
                )

        payload = {
            "first_name": first,
            "last_name": last,
            "email": person["email"],
            "password": make_password(),
            "phone_number": person["phone"],
            "employee_id": person["employee_id"],
            "role_ids": [role_id],
            "reports_to_id": manager["id"] if manager else None,
            "location": person.get("location") or None,
        }

        trail = f"  -> {manager['_label']}" if manager else "  -> (no manager)"
        for note in notes:
            trail += f"\n       note: {note}"

        if args.dry_run:
            print(f"would create {label:24s} {role_name:12s}{trail}")
            by_name[label.lower()] = {"id": f"dry-{label}", "_label": label}
            continue

        response = api("POST", "/users/", token, args.base_url, json=payload)

        if response.status_code >= 400:
            detail = response.json().get("detail", response.text)
            failed.append((person, str(detail)[:120]))
            continue

        made = response.json().get("data", {})
        by_name[label.lower()] = {**made, "_label": label}
        by_email[person["email"]] = made

        created.append((person, label, role_name, payload["password"]))
        print(f"created {label:24s} {role_name:12s}{trail}")

    print()
    for person, why in skipped:
        print(f"skipped  row {person['row']:>2}  {person['name']:24s} {why}")
    for person, why in failed:
        print(f"REFUSED  row {person['row']:>2}  {person['name']:24s} {why}")

    print(f"\n{len(created)} created, {len(skipped)} skipped, {len(failed)} refused")

    if created and args.out:
        with open(args.out, "w", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(["Name", "Email", "Role", "Temporary password"])
            for person, label, role_name, password in created:
                writer.writerow([label, person["email"], role_name, password])

        print(f"\nPasswords written to {args.out} - hand them out, then delete it.")
        print("Everyone should change theirs on first sign-in.")


if __name__ == "__main__":
    main()
