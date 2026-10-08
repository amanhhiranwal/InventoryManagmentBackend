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

from sqlalchemy import inspect, text
from sqlalchemy.exc import ProgrammingError

from app.database.base import Base
from app.database.postgres import engine
import app.models  # Registers all SQLAlchemy models


def _is_permission_error(error: Exception) -> bool:
    text_of = str(error).lower()

    return "insufficientprivilege" in text_of or "must be owner" in text_of


def _run(sql: str, what: str, blocked: list[tuple[str, str]]) -> bool:
    """One statement, in its own transaction. True when it went through."""

    try:
        with engine.begin() as conn:
            conn.execute(text(sql))

        return True
    except ProgrammingError as error:
        if _is_permission_error(error):
            blocked.append((what, sql))
            print(f"  ! {what} - not permitted")
            return False

        raise


def sync_schema() -> int:
    print("--- 1. Ensuring All Database Tables Exist (create_all) ---")

    blocked: list[tuple[str, str]] = []

    try:
        Base.metadata.create_all(bind=engine)
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
                )

    if added:
        print(f"  Successfully synchronized {added} missing column(s).")
    elif not blocked:
        print("  All tables and columns are up to date.")

    if not blocked:
        return 0

    owner = engine.url.username or "the application user"
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
        "\n  Fix it once, as the postgres superuser or the table's current\n"
        "  owner, then deploy again:\n"
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
