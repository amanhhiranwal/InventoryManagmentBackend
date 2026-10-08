"""Bring the database up to the shape the models expect.

Adds tables that are missing and columns that are missing from tables
that exist. It never drops or narrows anything, so it is safe to run on
every deploy.

Two things it is careful about, both learned the hard way:

  * Each statement runs in its own transaction. One shared transaction
    meant a single failure rolled back every column added before it, so
    a permission problem on one table left the others unmigrated too.

  * A permission error is reported as an instruction rather than a stack
    trace. Postgres requires ownership of a table to ALTER it, and
    GRANT ALL is not enough, which is not obvious from
    "InsufficientPrivilege" at the bottom of forty lines of traceback.

It exits non-zero when anything is still missing. That fails the deploy
on purpose: the application is already running against this database,
and a model that maps a column the table does not have fails every
query touching it - which is the whole table unusable, not a degraded
corner of it.
"""

import os
import sys

# Ensure backend root is on sys.path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import URL
from sqlalchemy.exc import ProgrammingError

from app.core.config import settings
from app.database.base import Base
from app.database.postgres import engine
import app.models  # Registers all SQLAlchemy models


def ddl_engine():
    """The connection schema changes are made over.

    The application's own user only needs to read and write. Adding a
    column needs ownership of the table, which is a much larger thing to
    hand the running process - so if admin credentials are configured
    they are used here and nowhere else.

    Returns (engine, is_admin). Without admin credentials this is the
    application's own connection, which is right wherever that user
    already owns the tables.
    """

    user = (settings.POSTGRES_ADMIN_USER or "").strip()
    password = settings.POSTGRES_ADMIN_PASSWORD or ""

    if not user:
        return engine, False

    admin = create_engine(
        URL.create(
            drivername="postgresql+psycopg2",
            username=user,
            password=password,
            host=settings.POSTGRES_HOST,
            port=settings.POSTGRES_PORT,
            database=settings.POSTGRES_DB,
        ),
        pool_pre_ping=True,
    )

    return admin, True


def hand_tables_to_app_user(ddl, app_user: str) -> int:
    """Give the application's user ownership of every table.

    Run once, while connected as somebody who can. After this the
    ordinary connection can add its own columns and the admin
    credentials stop being needed - so a deploy does not depend on them
    staying configured.
    """

    moved = 0

    with ddl.begin() as conn:
        rows = conn.execute(
            text(
                "SELECT tablename FROM pg_tables "
                "WHERE schemaname = 'public' AND tableowner <> :owner"
            ),
            {"owner": app_user},
        ).fetchall()

        for (table_name,) in rows:
            conn.execute(
                text(f'ALTER TABLE public."{table_name}" OWNER TO "{app_user}"')
            )
            moved += 1

    return moved


def _is_permission_error(error: Exception) -> bool:
    text_of = str(error).lower()

    return "insufficientprivilege" in text_of or "must be owner" in text_of


def _run(sql: str, what: str, blocked: list[tuple[str, str]], ddl=None) -> bool:
    """One statement, in its own transaction. True when it went through."""

    try:
        with (ddl or engine).begin() as conn:
            conn.execute(text(sql))

        return True
    except ProgrammingError as error:
        if _is_permission_error(error):
            blocked.append((what, sql))
            print(f"  ! {what} - not permitted")
            return False

        raise


def sync_schema() -> int:
    ddl, is_admin = ddl_engine()
    app_user = settings.POSTGRES_USER

    if is_admin:
        print(f"--- 0. Schema changes as '{settings.POSTGRES_ADMIN_USER}' ---")

        try:
            moved = hand_tables_to_app_user(ddl, app_user)

            if moved:
                print(f"  Handed {moved} table(s) to '{app_user}'.")
            else:
                print(f"  '{app_user}' already owns every table.")
        except Exception as error:  # noqa: BLE001 - reported, not fatal yet
            print(f"  Could not hand tables over: {str(error).splitlines()[0][:80]}")

    print("--- 1. Ensuring All Database Tables Exist (create_all) ---")

    blocked: list[tuple[str, str]] = []

    try:
        Base.metadata.create_all(bind=ddl)
    except ProgrammingError as error:
        if not _is_permission_error(error):
            raise

        blocked.append(("creating missing tables", "CREATE TABLE ..."))
        print("  ! creating missing tables - not permitted")

    print("--- 2. Checking & Adding Missing Columns & Indexes ---")

    inspector = inspect(engine)
    added = 0

    for table_name, table in Base.metadata.tables.items():
        if not inspector.has_table(table_name):
            continue

        existing = {col["name"] for col in inspector.get_columns(table_name)}

        for col in table.columns:
            if col.name in existing:
                continue

            col_type = col.type.compile(engine.dialect)
            print(f"  + Adding column: {table_name}.{col.name} ({col_type})")

            ok = _run(
                f'ALTER TABLE "{table_name}" ADD COLUMN IF NOT EXISTS '
                f'"{col.name}" {col_type} NULL;',
                f"{table_name}.{col.name}",
                blocked,
                ddl,
            )

            if not ok:
                continue

            added += 1

            if col.index:
                index_name = f"ix_{table_name}_{col.name}"
                print(f"  + Creating index: {index_name}")
                _run(
                    f'CREATE INDEX IF NOT EXISTS "{index_name}" '
                    f'ON "{table_name}" ("{col.name}");',
                    index_name,
                    blocked,
                    ddl,
                )

    if added:
        print(f"  Successfully synchronized {added} missing column(s).")
    elif not blocked:
        print("  All tables and columns are up to date.")

    if not blocked:
        return 0

    owner = app_user or "the application user"
    tables = sorted({name.split(".")[0] for name, _ in blocked})

    print("\n" + "=" * 68)
    print("  THE SCHEMA IS NOT UP TO DATE - THE DEPLOY IS NOT SAFE")
    print("=" * 68)
    print(
        f"\n  {len(blocked)} change(s) were refused because '{owner}' does not\n"
        "  own the table. Postgres requires ownership to ALTER a table, and\n"
        "  GRANT ALL does not confer it.\n"
    )

    for what, _ in blocked:
        print(f"    - {what}")

    print(
        "\n  Two ways to fix it. Either set POSTGRES_ADMIN_USER and\n"
        "  POSTGRES_ADMIN_PASSWORD in the backend's environment - the next\n"
        "  deploy then hands the tables over itself and they are not needed\n"
        "  again - or run this once, as the superuser or the current owner:\n"
    )

    for table_name in tables:
        print(f'    ALTER TABLE "{table_name}" OWNER TO {owner};')

    print(
        "\n  Or hand over everything in the schema in one go:\n"
        "\n    DO $$DECLARE r record; BEGIN\n"
        "      FOR r IN SELECT tablename FROM pg_tables WHERE schemaname='public'\n"
        f"      LOOP EXECUTE format('ALTER TABLE public.%I OWNER TO {owner}', r.tablename);\n"
        "      END LOOP; END$$;\n"
    )
    print(
        "  Until then the application is running against a database that is\n"
        "  missing these columns, and every query touching them will fail.\n"
    )

    return 1


if __name__ == "__main__":
    sys.exit(sync_schema())
