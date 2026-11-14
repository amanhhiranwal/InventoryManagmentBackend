"""Users > Bulk Upload: create and update people from HR's Excel or CSV.

The same sheet HR keeps ("Master Data for Employee") works as it is, and
so does the template from this screen. Each row is one person, matched to
an account already on file by Employee ID, then by email:

  * not on file and Active        -> created
  * on file                       -> updated, cell by cell: a blank cell
                                     leaves that field as it is
  * Left / Terminated / Inactive  -> on file: switched off; not on file:
                                     skipped

What a row says, and what happens to it:

  Employee ID      the key; needed to create someone
  Employee Name    or First Name and Last Name
  Email            their login. With "test emails" on, new people get
                   first.last@mailinator.com instead, and nobody's email
                   on file is changed
  Designation      their job title, kept on the user. For someone new it
                   also picks the role (app/services/org_setup.ROLE_FOR)
  Role             optional: the role by name. On an existing person, the
                   only way the upload changes their role
  Department       mapped and tidied (org_setup.DEPARTMENT_FOR). The sales
                   chart - AVP, Zonal Head, Area Manager - is always Sales
  Reporting Manager  a name, an Employee ID or an email; "NA" is nobody
  Also Reports To  more managers, separated by ; or ,
  Company, Phone, Location, Status

The upload is read twice by the screen: once to show what it would do
(``apply`` false), then to do it. Rows are applied one by one, so one
person the database refuses costs only that row, and every refusal comes
back with its sheet row and reason. Managers are set after everybody has
been created, so a manager lower down the sheet is found.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.company import Company
from app.models.procurement import Department
from app.models.role import Role
from app.models.user import User
from app.services.hierarchy_service import HierarchyService
from app.services.inventory_import_service import _rows_from_csv, _rows_from_xlsx
from app.services.org_setup import department_for_person, role_for, tidy

MAX_ROWS = 2000

#: Each field, and the headings it is found under (lower case, spaces only).
HEADINGS = {
    "employee_id": ("e.code", "e code", "ecode", "employee id", "employee code", "emp code", "emp id", "code"),
    "name": ("employee name", "name", "full name"),
    "first_name": ("first name",),
    "last_name": ("last name", "surname"),
    "email": ("email id", "email", "email address", "mail id", "e-mail"),
    "phone": ("contactno", "contact no", "contact number", "phone", "phone number", "mobile", "mobile no"),
    "designation": ("designation", "job title", "title"),
    "role": ("role", "crm role", "access role"),
    "department": ("department", "dept"),
    "manager": ("reporting manager", "reports to", "manager", "reporting to"),
    "also": ("also reports to", "also reporting to", "co manager", "co-manager", "second manager"),
    "company": ("company", "company name"),
    "status": ("status", "employment status"),
    "location": ("location", "city", "base location"),
}

LEFT = {"left", "terminated", "inactive", "resigned", "absconded", "exited"}
NOBODY = {"", "na", "n/a", "none", "-", "nil", "top", "self"}

#: The columns of the template, in order.
TEMPLATE_COLUMNS = [
    "Employee ID", "Employee Name", "Email", "Phone", "Designation", "Role", "Department",
    "Reporting Manager", "Also Reports To", "Company", "Location", "Status",
]


def _heading(value: str) -> str:
    return " ".join(str(value or "").replace("_", " ").split()).strip().lower().rstrip(":")


def _field_columns(header: list[str]) -> dict[str, int]:
    found: dict[str, int] = {}
    for index, cell in enumerate(header):
        name = _heading(cell)
        for field_name, options in HEADINGS.items():
            if name in options and field_name not in found:
                found[field_name] = index
    return found


def read_rows(content: bytes, filename: str) -> list[dict]:
    """The sheet as one dict per person, with its sheet row number."""

    name = (filename or "").lower()
    if name.endswith(".csv"):
        grid = _rows_from_csv(content)
    elif name.endswith((".xlsx", ".xlsm")):
        grid = _rows_from_xlsx(content)
    else:
        raise HTTPException(status_code=400, detail="Upload an Excel (.xlsx) or CSV (.csv) file.")

    # The heading row is the first one naming an employee and a code or email.
    for at, row in enumerate(grid[:15]):
        columns = _field_columns([str(c) for c in row])
        if ("name" in columns or "first_name" in columns) and ("employee_id" in columns or "email" in columns):
            break
    else:
        raise HTTPException(
            status_code=400,
            detail="No heading row found. The sheet needs at least Employee Name and Employee ID (or Email) columns.",
        )

    people = []
    for offset, row in enumerate(grid[at + 1:], start=at + 2):
        cells = {f: tidy(row[i]) if i < len(row) else "" for f, i in columns.items()}
        if not any(cells.values()):
            continue
        cells["_row"] = offset
        cells["_present"] = set(columns)
        people.append(cells)

    if not people:
        raise HTTPException(status_code=400, detail="The sheet has a heading row but no people under it.")
    if len(people) > MAX_ROWS:
        raise HTTPException(status_code=400, detail=f"Upload at most {MAX_ROWS} people at a time.")
    return people


# ----------------------------------------------------------------------
# Reading one row
# ----------------------------------------------------------------------
def _names(cells: dict) -> tuple[str, str]:
    if cells.get("first_name") or cells.get("last_name"):
        return cells.get("first_name", ""), cells.get("last_name", "")
    parts = cells.get("name", "").split()
    return (parts[0], " ".join(parts[1:])) if parts else ("", "")


def _first_email(value: str) -> str:
    match = re.search(r"[\w.+-]+@[\w-]+(\.[\w-]+)+", value or "")
    return match.group(0).lower() if match else ""


def _phone(value: str) -> str:
    digits = re.sub(r"\D", "", value or "")
    return digits[-10:] if len(digits) >= 10 else digits


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", ".", (text or "").lower()).strip(".")


def _people_in(value: str) -> list[str]:
    return [p.strip() for p in re.split(r"[;,/]", value or "") if p.strip() and p.strip().lower() not in NOBODY]


@dataclass
class Person:
    row: int
    cells: dict
    action: str = "skip"            # create | update | deactivate | skip | error
    user: User | None = None
    first_name: str = ""
    last_name: str = ""
    email: str = ""
    role: Role | None = None
    department: Department | None = None
    company: Company | None = None
    changes: list[str] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)
    manager_text: str | None = None
    also_texts: list[str] = field(default_factory=list)

    @property
    def name(self) -> str:
        return " ".join(p for p in (self.first_name, self.last_name) if p)


class Directory:
    """Everybody a manager cell could mean: people on file, and the people
    the sheet is about to create."""

    def __init__(self, db: Session, people: list[Person]):
        self.users = db.query(User).all()
        self.people = people

    def find(self, text: str) -> tuple[object | None, str | None]:
        """(a User or a Person, a note when it was a near match), or (None, why).

        By Employee ID or email first. Then by name: exactly, then a
        shortened one ("Leo Nelson" for Leo Nelson Paul, "Pradeep" when
        only one Pradeep is anywhere), then a near spelling. A name that
        fits more than one person is never guessed at."""

        wanted = tidy(text)
        lowered = wanted.lower()
        email = _first_email(wanted)

        for p in self.people:
            if p.action in ("create", "update") and (
                lowered == (p.cells.get("employee_id") or "").lower() or (email and email == p.email)
            ):
                return (p.user or p), None
        for u in self.users:
            if lowered == (u.employee_id or "").lower() or (email and email == (u.email or "").lower()):
                return u, None

        # Everybody once, under the name they go by.
        candidates: dict[int, tuple[str, object]] = {}
        for p in self.people:
            if p.action in ("create", "update"):
                who = p.user or p
                candidates[id(who)] = (p.name, who)
        for u in self.users:
            if u.is_active and id(u) not in candidates:
                candidates[id(u)] = (f"{u.first_name} {u.last_name}".strip(), u)

        words = _name_words(wanted)
        if not words:
            return None, f"'{wanted}' is not a name"

        def pick(matches: list[tuple[str, object]], note: str | None):
            if len(matches) == 1:
                return matches[0][1], (note.format(name=matches[0][0]) if note else None)
            names = ", ".join(sorted(n for n, _ in matches)[:4])
            return None, f"'{wanted}' could be {names}; use their Employee ID"

        exact = [(n, o) for n, o in candidates.values() if _name_words(n) == words]
        if exact:
            return pick(exact, None)

        shortened = [(n, o) for n, o in candidates.values() if _shortens(words, _name_words(n))]
        if shortened:
            return pick(shortened, f"read '{wanted}' as {{name}}")

        scored = sorted(
            ((SequenceMatcher(None, " ".join(words), " ".join(_name_words(n))).ratio(), n, o)
             for n, o in candidates.values()),
            key=lambda s: -s[0],
        )
        if scored and scored[0][0] >= 0.85 and (len(scored) == 1 or scored[1][0] < scored[0][0] - 0.05):
            return scored[0][2], f"read '{wanted}' as {scored[0][1]}"
        return None, f"nobody called {wanted} on file or in the sheet"


#: Courtesies people write after a manager's name in the sheet.
HONORIFICS = {"sir", "mam", "maam", "madam", "ji", "mr", "mrs", "ms", "dr", "shri", "smt"}


def _name_words(text: str) -> list[str]:
    return [w for w in re.findall(r"[a-z]+", (text or "").lower()) if w not in HONORIFICS]


def _same_person(on_file: list[str], in_sheet: list[str]) -> bool:
    """Whether a name on file and the sheet's name for the same Employee ID
    are one person written two ways: a middle name added ("Gaurav Thakur",
    "Gaurav Kumar Thakur") or a spelling ("Vikas Pundir", "Vikash
    Pundhir"). "Sahil Ramani" and "Darpan Sethi" are not."""

    if on_file == in_sheet or _shortens(on_file, in_sheet) or _shortens(in_sheet, on_file):
        return True
    return SequenceMatcher(None, " ".join(on_file), " ".join(in_sheet)).ratio() >= 0.8


def _shortens(words: list[str], full: list[str]) -> bool:
    """Each written word starts one of the full name's words, in order:
    "leo nelson" -> leo nelson paul, "kalam khan" -> mohd kalamuddin khan."""

    at = 0
    for word in words:
        while at < len(full) and not full[at].startswith(word):
            at += 1
        if at == len(full):
            return False
        at += 1
    return True


# ----------------------------------------------------------------------
# Planning
# ----------------------------------------------------------------------
def plan(rows: list[dict], db: Session, *, test_emails: bool) -> list[Person]:
    roles = {r.role_name.lower(): r for r in db.query(Role).all()}
    departments = {d.name.lower(): d for d in db.query(Department).all()}
    companies = db.query(Company).filter(Company.is_active.is_(True)).all()
    by_code = {(u.employee_id or "").lower(): u for u in db.query(User).all() if u.employee_id}
    by_email = {(u.email or "").lower(): u for u in db.query(User).all()}
    taken_emails = set(by_email)
    active_users = [u for u in by_email.values() if u.is_active and not u.is_super_admin]
    matched: dict = {}
    seen_codes: dict[str, int] = {}
    people: list[Person] = []

    def company_for(text: str) -> Company | None:
        wanted = text.lower()
        for c in companies:
            name = c.company_name.lower()
            initials = "".join(w[0] for w in re.findall(r"[a-z]+", name))
            if wanted and (wanted in name or wanted == (c.company_code or "").lower() or wanted == initials):
                return c
        return None

    for cells in rows:
        p = Person(row=cells["_row"], cells=cells)
        people.append(p)
        p.first_name, p.last_name = _names(cells)
        code = cells.get("employee_id", "")
        status = cells.get("status", "").lower()
        leaving = status in LEFT
        sheet_email = _first_email(cells.get("email", ""))

        if not p.first_name:
            p.action, p.issues = "error", ["no name"]
            continue
        if code and not re.search(r"\d", code):
            p.issues.append(f"'{code}' is not an Employee ID - it needs a number")
            code = ""
            cells["employee_id"] = ""
        if code:
            if code.lower() in seen_codes:
                p.action, p.issues = "error", [f"Employee ID {code} is also on row {seen_codes[code.lower()]}"]
                continue
            seen_codes[code.lower()] = p.row

        # Who this is on file: their Employee ID, then their email, then
        # their exact name. An ID held by someone with a different name is
        # never trusted - it would rename the wrong person's account.
        sheet_words = _name_words(f"{p.first_name} {p.last_name}")
        p.user = by_code.get(code.lower()) if code else None
        if p.user is not None and not _same_person(_name_words(f"{p.user.first_name} {p.user.last_name}"), sheet_words):
            p.issues.append(f"Employee ID {code} is {p.user.first_name} {p.user.last_name} on file - not them")
            p.user = None
        if p.user is None and sheet_email:
            p.user = by_email.get(sheet_email)
        if p.user is None:
            namesakes = [u for u in active_users if _name_words(f"{u.first_name} {u.last_name}") == sheet_words]
            if len(namesakes) == 1:
                p.user = namesakes[0]
                p.issues.append(f"matched by name; their Employee ID on file stays {p.user.employee_id}")
            elif len(namesakes) > 1:
                p.action, p.issues = "error", p.issues + [
                    f"{len(namesakes)} people on file are called {p.name}; give the Employee ID they have on file"]
                continue
        if p.user is not None:
            if p.user.id in matched:
                p.action, p.issues = "error", p.issues + [f"same person as row {matched[p.user.id]}"]
                continue
            matched[p.user.id] = p.row

        if p.user is not None and p.user.is_super_admin:
            p.action, p.issues = "skip", ["a super admin is never changed by an upload"]
            continue

        if leaving:
            if p.user is not None and p.user.is_active:
                p.action, p.changes = "deactivate", ["switched off: " + cells.get("status", "")]
            else:
                p.action = "skip"
                p.issues = ["left - not on file" if p.user is None else "left - already switched off"]
            continue

        # Who they are.
        role_text = cells.get("role", "")
        if role_text:
            p.role = roles.get(role_text.lower())
            if p.role is None:
                p.issues.append(f"no role called {role_text}")
        elif p.user is None:
            p.role = roles.get(role_for(cells.get("designation", "")).lower())

        role_name = p.role.role_name if p.role else (p.user.roles[0].role_name if p.user and p.user.roles else "")
        dept_name = department_for_person(cells.get("department", ""), role_name)
        if dept_name:
            p.department = departments.get(dept_name.lower())
        elif cells.get("department"):
            p.issues.append(f"no department matches '{cells['department']}' - add it under Masters > Departments")

        if cells.get("company"):
            p.company = company_for(cells["company"])
            if p.company is None:
                p.issues.append(f"company '{cells['company']}' is not in Masters > Companies - left without one")

        p.manager_text = cells.get("manager") if "manager" in cells["_present"] else None
        p.also_texts = _people_in(cells.get("also", ""))

        if p.user is None:
            if not code:
                p.action, p.issues = "error", p.issues + ["an Employee ID is needed to add someone"]
                continue
            if p.role is None:
                p.action, p.issues = "error", p.issues + ["no role to give them"]
                continue
            if test_emails or not sheet_email:
                base = _slug(p.name) or _slug(code)
                email = f"{base}@mailinator.com"
                if email in taken_emails:
                    email = f"{base}.{_slug(code)}@mailinator.com"
                if not test_emails:
                    p.issues.append("no email in the sheet - given a mailinator one")
                p.email = email
            else:
                p.email = sheet_email
            if p.email in taken_emails:
                p.action, p.issues = "error", p.issues + [f"{p.email} is already someone's login"]
                continue
            taken_emails.add(p.email)
            p.action = "create"
            continue

        # Already on file: what would change.
        u = p.user
        p.email = u.email
        p.action = "update"
        if (p.first_name, p.last_name) != (u.first_name, u.last_name):
            p.changes.append("name")
        if cells.get("designation") and cells["designation"] != (u.designation or ""):
            p.changes.append("designation")
        if p.department and p.department.id != u.department_id:
            p.changes.append("department")
        if p.role and {r.id for r in u.roles} != {p.role.id}:
            p.changes.append("role")
        if cells.get("phone") and _phone(cells["phone"]) != (u.phone_number or ""):
            p.changes.append("phone")
        if cells.get("location") and cells["location"] != (u.location or ""):
            p.changes.append("location")
        if p.company and p.company.id not in {c.id for c in u.companies}:
            p.changes.append("company")
        if not test_emails and sheet_email and sheet_email != (u.email or "").lower():
            if sheet_email in taken_emails:
                p.issues.append(f"email {sheet_email} is already someone's login - kept {u.email}")
            else:
                p.changes.append("email")
                p.email = sheet_email
        if not u.is_active:
            p.changes.append("switched back on")

    _plan_managers(people, db)

    for p in people:
        if p.action == "update" and not p.changes:
            p.action = "skip"
            p.issues = p.issues or ["no change"]
    return people


def _role_ids(who) -> set[str]:
    if isinstance(who, User):
        return {str(r.id) for r in who.roles}
    return {str(who.role.id)} if who.role else set()


def _plan_managers(people: list[Person], db: Session) -> None:
    directory = Directory(db, people)
    charted = set(HierarchyService.role_levels(db))
    co_managers = HierarchyService.co_managers(db)
    for p in people:
        p.cells["_manager"] = None
        p.cells["_also"] = []
        if p.action not in ("create", "update"):
            continue

        mine = _role_ids(p.user) if p.role is None and p.user else _role_ids(p)

        def acceptable(found) -> str | None:
            if found is (p.user or p):
                return "cannot report to themselves"
            if isinstance(found, User) and found.is_super_admin:
                return None
            theirs = _role_ids(found)
            below = HierarchyService.junior_role_ids(theirs, db)
            if mine & charted and theirs & charted and not mine & below:
                return "their role is not above this person's in Workflows"
            return None

        if p.manager_text is not None:
            if p.manager_text.lower() in NOBODY:
                p.cells["_manager"] = "top"
                if p.user is not None and p.user.reports_to_id:
                    p.changes.append("manager: none (top)")
            else:
                found, note = directory.find(p.manager_text)
                why = note if found is None else acceptable(found)
                if found is None or why:
                    p.issues.append(f"manager not set - {why}")
                else:
                    p.cells["_manager"] = found
                    if note:
                        p.issues.append(note)
                    if p.user is None or not isinstance(found, User) or found.id != p.user.reports_to_id:
                        p.changes.append(f"manager: {_display(found)}")

        already = co_managers.get(str(p.user.id), set()) if p.user is not None else set()
        for text in p.also_texts:
            found, note = directory.find(text)
            why = note if found is None else acceptable(found)
            if found is None or why:
                p.issues.append(f"also-reports-to {text} not set - {why}")
            else:
                p.cells["_also"].append(found)
                if not (isinstance(found, User) and str(found.id) in already):
                    p.changes.append(f"also reports to: {_display(found)}")


def _display(who) -> str:
    if isinstance(who, User):
        return f"{who.first_name} {who.last_name}".strip()
    return who.name


# ----------------------------------------------------------------------
# Applying
# ----------------------------------------------------------------------
def apply(people: list[Person], db: Session, current_user: dict, *, send_welcome: bool,
          start_password: str | None) -> None:
    from app.schemas.user import CreateUserRequest
    from app.services.user_service import UserService

    for p in people:
        try:
            if p.action == "create":
                p.user = UserService.create(
                    CreateUserRequest(
                        first_name=p.first_name, last_name=p.last_name, email=p.email,
                        password=start_password or None,
                        phone_number=_phone(p.cells.get("phone", "")),
                        employee_id=p.cells["employee_id"], location=p.cells.get("location") or None,
                        role_ids=[str(p.role.id)], company_ids=[str(p.company.id)] if p.company else [],
                        department_id=p.department.id if p.department else None,
                        designation=p.cells.get("designation") or None,
                    ),
                    db, current_user, send_welcome=send_welcome,
                )
            elif p.action == "update":
                # The same checks as editing someone by hand: only people
                # below the uploader, and only roles below their own.
                UserService._assert_can_manage_user(current_user, p.user, db)
                if p.role is not None:
                    UserService._assert_can_assign_roles(current_user, [str(p.role.id)], db)
                _update(p, db)
            elif p.action == "deactivate":
                UserService._assert_can_manage_user(current_user, p.user, db)
                UserService.set_active(str(p.user.id), False, db, current_user)
        except HTTPException as error:
            db.rollback()
            p.issues.append(str(error.detail))
            p.action = "error"
        except Exception as error:  # noqa: BLE001 - one row's failure is reported, not fatal
            db.rollback()
            p.issues.append(f"could not be saved: {str(error).splitlines()[0][:160]}")
            p.action = "error"

    # Managers once everybody exists.
    for p in people:
        if p.action not in ("create", "update") or p.user is None:
            continue
        try:
            role_ids = [str(r.id) for r in p.user.roles]
            manager = p.cells.get("_manager")
            if manager == "top":
                p.user.reports_to_id = None
            elif manager is not None:
                target = manager if isinstance(manager, User) else manager.user
                if target is None:
                    p.issues.append(f"manager not set - {manager.name} (row {manager.row}) was not saved")
                else:
                    p.user.reports_to_id = HierarchyService.validate_reports_to(
                        str(p.user.id), str(target.id), role_ids, db)
            also = [m if isinstance(m, User) else m.user for m in p.cells.get("_also") or []]
            if also:
                HierarchyService.set_co_managers(p.user.id, HierarchyService.validate_co_managers(
                    str(p.user.id), [str(m.id) for m in also if m is not None], p.user.reports_to_id, role_ids, db), db)
            db.commit()
        except HTTPException as error:
            db.rollback()
            p.issues.append(f"manager not set - {error.detail}")


def _update(p: Person, db: Session) -> None:
    u, cells = p.user, p.cells
    if p.first_name:
        u.first_name, u.last_name = p.first_name, p.last_name
    if cells.get("designation"):
        u.designation = cells["designation"]
    if p.department:
        u.department_id = p.department.id
    if p.role:
        u.roles = [p.role]
    if cells.get("phone"):
        u.phone_number = _phone(cells["phone"]) or u.phone_number
    if cells.get("location"):
        u.location = cells["location"]
    if p.company and p.company not in u.companies:
        u.companies = [*u.companies, p.company]
    if "email" in p.changes:
        u.email = p.email
    if not u.is_active:
        u.is_active = True
    db.commit()


# ----------------------------------------------------------------------
# The whole thing
# ----------------------------------------------------------------------
def run(content: bytes, filename: str, db: Session, current_user: dict, *, commit: bool,
        test_emails: bool, send_welcome: bool, start_password: str | None) -> dict:
    from app.services.password_service import PasswordService

    if start_password:
        PasswordService.assert_strong(start_password)

    people = plan(read_rows(content, filename), db, test_emails=test_emails)

    if commit:
        apply(people, db, current_user, send_welcome=send_welcome, start_password=start_password)

    counts: dict[str, int] = {}
    for p in people:
        counts[p.action] = counts.get(p.action, 0) + 1

    return {
        "applied": commit,
        "counts": counts,
        "rows": [
            {
                "row": p.row,
                "employee_id": p.cells.get("employee_id", ""),
                "name": p.name,
                "email": p.email,
                "action": p.action,
                "role": p.role.role_name if p.role else (", ".join(r.role_name for r in p.user.roles) if p.user else ""),
                "department": p.department.name if p.department else "",
                "designation": p.cells.get("designation", ""),
                "changes": p.changes,
                "issues": p.issues,
            }
            for p in people
        ],
        "notes": _notes(people, test_emails, send_welcome, start_password),
    }


def _notes(people: list[Person], test_emails: bool, send_welcome: bool, start_password: str | None) -> list[str]:
    notes = []
    creating = sum(1 for p in people if p.action == "create")
    if creating and test_emails:
        notes.append("New people get @mailinator.com logins; nobody's email on file is changed.")
    if creating and not send_welcome and not start_password:
        notes.append("No welcome email and no starting password: new people cannot sign in until you send "
                     "each a welcome email or reset their password from Users.")
    if creating and start_password:
        notes.append("New people start on the password you typed and must change it at first sign-in.")
    return notes


def template_csv() -> bytes:
    import csv
    import io

    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(TEMPLATE_COLUMNS)
    writer.writerow(["EMP001", "Asha Rao", "asha.rao@example.com", "9876543210", "Area Manager", "",
                     "Sales", "Priya Nair", "", "Synergy Global", "Pune", "Active"])
    return out.getvalue().encode("utf-8-sig")
