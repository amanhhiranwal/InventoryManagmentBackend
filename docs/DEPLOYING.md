# Taking these changes to the live site

## Read this first

`check_and_seed_db.py` runs on every deploy, and until now its `else`
branch reset the super admin's password back to `password123` - a value
committed to this repository - and forced the account active again. So on
the live site that credential works, changing it did not survive the next
release, and switching the account off did not either.

That branch no longer touches the password or the active flag. **After
the next deploy, sign in as the super admin and change the password.**
Until then it is still the published one.

A fresh database takes its first password from `SUPERADMIN_PASSWORD` if
that is set, and warns on the console when it falls back to the default.

---

## What the pipeline already does

The CI runs `sync_schema.py`, `check_and_seed_db.py` and
`apply_crm_workflow_schema.py` on every deploy, so the schema - the new
`sales_warranty_term` table and `users.location` - arrives on its own.
Step 1 below is only for applying it by hand.

Everything else in this file is a one-off that the pipeline does not do.

---

Everything here has been run against the development database. Run it on
the live one in this order, from the backend container:

```bash
docker exec -w /app backend_app python <script>
```

Nothing below needs the site taken down, but do it when nobody is
mid-approval: step 3 changes who signs what.

---

## 1. Schema — the new columns and tables

`create_all` adds what is missing and never removes anything, so this is
safe to run against a database that already has some of it.

```bash
docker exec -w /app backend_app python -c "
from app.database.base import Base
from app.database.postgres import engine
import app.models  # registers every model
Base.metadata.create_all(bind=engine)
print('schema synced')
"
```

This adds:

| What | Where |
|---|---|
| `sales_warranty_term` | new table — the warranty lengths |
| `users.location` | new column |

`users.location` is nullable, so existing accounts are unaffected until
somebody fills it in.

---

## 2. The Founder role

```bash
docker exec -w /app backend_app python seed_founder_role.py
```

Creates the role, places it above the CEO on the Sales chart — which is
what seniority is read from — and grants it what the CEO holds. Safe to
run again; everything is checked before it is added.

**Then give somebody the role**, or orders needing founder approval queue
with nobody to action them. On the development database that is Darpan
Sethi; on the live one, create or pick whoever it should be and assign
the Founder role from Users.

---

## 3. The discount bands

The bands are read from a setting, not from the code, so the new figures
have to be written to the live database. The old set said the AVP carried
15%.

```bash
docker exec -w /app backend_app python -c "
import json
from app.database.postgres import SessionLocal
from app.models.app_setting import AppSetting

db = SessionLocal()
bands = [
    {'role': 'AVP',     'to_percent': 10.0},
    {'role': 'CEO',     'to_percent': 20.0},
    {'role': 'Founder', 'to_percent': None},
]
row = db.query(AppSetting).filter(AppSetting.key == 'discount_bands').first()
if row:
    print('was:', row.value)
    row.value = json.dumps(bands)
else:
    db.add(AppSetting(key='discount_bands', value=json.dumps(bands)))
db.commit()
print('now:', json.dumps(bands))
"
```

Or set it from Masters › Proposal Approval, which writes the same thing.

---

## 4. Warranty rates — the lossy one, so dry-run it

Under the old model one rate applied to the whole catalogue. It is now
held per product, because five years on a panel and five years on a
camera are different undertakings.

```bash
docker exec -w /app backend_app python migrations/drop_warranty_term_rates.py --dry-run
```

It prints what the columns hold and how many products would receive them.
Then:

```bash
docker exec -w /app backend_app python migrations/drop_warranty_term_rates.py
```

It carries each term's old rate onto every product that has no figure of
its own, and only then drops the columns — so the move is lossless. A
product that already carries its own rate keeps it, because that is the
newer answer. Running it twice says there is nothing to do.

---

## 5. The menus

```bash
docker exec -w /app backend_app python -c "
from app.database.postgres import SessionLocal
from app.services.menu_service import MenuService
db = SessionLocal()
MenuService.ensure_default_menus(db)
print('menus synced')
"
```

Adds Masters › Warranty Terms and Masters › Reporting Chart.

---

## 6. The staffing sheet

Only if the live database does not already have these people.

```bash
export CRM_ADMIN_EMAIL='your-admin@qonevo.in'
read -rs CRM_ADMIN_PASSWORD && export CRM_ADMIN_PASSWORD

python3 import_users_from_sheet.py "Employees Details.xlsx" \
  --base-url https://synergy-sync.com/api/v1 \
  --out ~/live_credentials.csv
```

Run it from a machine that can reach the site, not from inside the
container. It refuses the seeded local admin against any non-local host
and makes you type the hostname to confirm.

`--dry-run` first. It creates accounts, sets each person's location and
points them at their L1. An account that already exists is not skipped:
anything the sheet carries and the account lacks is filled in, and
nothing already set is overwritten.

The passwords it generates are real credentials. Hand them out, have
everyone change theirs, then delete the file.

---

## 7. Where the emails point

Masters › Company Profile › **CRM Address**. Set it to
`https://synergy-sync.com`.

Every link in an approval or notification email is built from this. Until
it is set the links come from the server's `FRONTEND_URL`, which on a
laptop is `localhost` — a link nobody receiving the email can open.

---

## 8. One-click approval from the email

Nothing to run. An approval email now carries **Approve** and **Reject**
beside the Open link, and clicking one decides it without signing in.

What that link can and cannot do:

- It is signed with the server's `JWT_SECRET_KEY`, so it cannot be
  composed by anybody who did not get the email.
- It carries one approval, one step of it, one person and one decision.
- It is spent the moment that step is decided, so a forwarded email
  approves nothing.
- It expires after seven days.
- It only ever goes to somebody who actually holds the role the step
  names - the AVP, the CEO, the founder. Where the reporting line has a
  gap and the request falls back to the super admins, they get no
  buttons and open the CRM as before.
- It does not sign anybody in. It decides that one request and nothing
  else.
- The decision is recorded as "Decided from the approval email."

If you would rather not have it, the buttons disappear by removing the
`decisions=` argument where the letter is built in
`app/services/approval_service.py`.

---

## Afterwards, worth checking

- Sign in as somebody on the sales floor and confirm Masters shows
  **Reporting Chart** and nothing else.
- Masters › Reporting Chart draws the tree, with the founder at the top.
- Raise a sales order at 25% and confirm it is refused at Confirmed, and
  that the refusal names AVP, CEO and Founder.
- Raise a dealer order with no discount and confirm it is held for the
  founder.
- A proposal at any discount should save as a draft and need no approval.
