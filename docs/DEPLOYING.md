# Taking these changes to the live site

## If a deploy failed with "must be owner of table users"

The application's database user does not own the tables, so it cannot
add a column to them. Postgres requires *ownership* to ALTER a table -
`GRANT ALL` does not confer it, which is why this appears on a database
the application otherwise reads and writes happily.

It matters more than a failed pipeline looks. The container is recreated
and started **before** the schema step runs, so the new code goes live
against a database missing the column it expects. Every query touching
that table fails and signing in returns a 500. The site is down until
the column exists.

### Get it back up now

On the database server, as the postgres superuser or the tables' owner:

```sql
ALTER TABLE users ADD COLUMN IF NOT EXISTS location VARCHAR(100);
```

### Then stop it happening again — pick one

**Either** add two lines to `backend/.env` on the server:

```
POSTGRES_ADMIN_USER=<a user that owns the tables, e.g. postgres>
POSTGRES_ADMIN_PASSWORD=<its password>
```

The next deploy then uses them **for schema changes only** and, as its
first act, hands every table over to the application's own user. After
that the application owns them and can add its own columns, so you can
delete those two lines again - they are needed once.

**Or** run the handover yourself, once, and never set them at all:

```sql
DO $$DECLARE r record; BEGIN
  FOR r IN SELECT tablename FROM pg_tables WHERE schemaname = 'public'
  LOOP EXECUTE format('ALTER TABLE public.%I OWNER TO <app_db_user>', r.tablename);
  END LOOP; END$$;
```

Replace `<app_db_user>` with the backend's `POSTGRES_USER`.

Either way, re-run the failed deploy from the Actions tab afterwards.

### What changed in the pipeline

`sync_schema.py` now:

- uses the admin connection for DDL when those variables are set, and
  the ordinary one when they are not;
- hands the tables to the application's user on the first admin run, so
  the credentials stop being needed;
- runs each statement in its own transaction, where they used to share
  one - a single refusal rolled back every column added before it;
- reports a refusal as the tables and the exact SQL, not a traceback;
- still exits non-zero while anything is missing, because a missing
  column is not a degraded corner of the system, it is that table
  unusable.

---

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

## What a push to main now does by itself

The pipeline runs, in order:

| Script | What it settles |
|---|---|
| `sync_schema.py` | tables and columns - `sales_warranty_term`, `users.location` |
| `check_and_seed_db.py` | the sales roles and the super admin |
| `apply_crm_workflow_schema.py` | the workflow tables |
| `apply_release_setup.py` | **menus, warranty terms, the Founder role and its place above the CEO, the discount bands** |

So the Reporting Chart and Warranty Terms appear in Masters, the Founder
exists and outranks the CEO, and the bands move to 10 / 20 / Founder -
all from the push, with nothing to remember.

`apply_release_setup.py` is idempotent and reports what it did. It will
not overwrite bands somebody has tuned themselves: it only moves a set
that still matches a default we shipped.

---

## The three things a push cannot do

**1. The people.** Accounts, their locations and who reports to whom are
data, not code. Run the import against the live site from a machine that
can reach it - see step 6. Until that is done the Reporting Chart draws
whoever is already there, which on a fresh production database is the
seeded demo team.

**2. The warranty rate migration.** It is destructive and carries data
across, so it stays a decision made with a dry run in front of you - see
step 4.

**3. The CRM Address.** One field in Masters - see step 7. Without it
every link in an approval email points at whatever `FRONTEND_URL` the
server was started with.

---

Everything below has been run against the development database. The
numbered steps are the ones the pipeline does not do.

## 1. Schema — only if applying by hand

The pipeline does this. Here for a database the pipeline has not
touched.

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

## 2. The Founder role — somebody has to hold it

`apply_release_setup.py` creates the role on deploy and places it above
the CEO. What it cannot do is decide who the founder is.

**Give somebody the role**, or orders needing founder approval queue
with nobody to action them. On the development database that is Darpan
Sethi; on the live one, create or pick whoever it should be and assign
the Founder role from Users.

---

## 3. The discount bands — handled on deploy

`apply_release_setup.py` moves a database still carrying the old 15% set
onto 10 / 20 / Founder. Only if you want to set them by hand:

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
