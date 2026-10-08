import os
import sys

# Ensure backend root is on sys.path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from sqlalchemy import inspect, text
from app.database.base import Base
from app.database.postgres import engine
import app.models  # Registers all SQLAlchemy models


def sync_schema() -> None:
    print("--- 1. Ensuring All Database Tables Exist (create_all) ---")
    Base.metadata.create_all(bind=engine)

    print("--- 2. Checking & Adding Missing Columns & Indexes ---")
    inspector = inspect(engine)
    added_count = 0

    with engine.begin() as conn:
        for table_name, table in Base.metadata.tables.items():
            if not inspector.has_table(table_name):
                continue

            existing_cols = {col["name"] for col in inspector.get_columns(table_name)}
            for col in table.columns:
                if col.name not in existing_cols:
                    col_type = col.type.compile(engine.dialect)
                    print(f"  + Adding column: {table_name}.{col.name} ({col_type})")
                    conn.execute(
                        text(f'ALTER TABLE "{table_name}" ADD COLUMN IF NOT EXISTS "{col.name}" {col_type} NULL;')
                    )
                    added_count += 1

                if col.index and col.name not in existing_cols:
                    index_name = f"ix_{table_name}_{col.name}"
                    print(f"  + Creating index: {index_name}")
                    conn.execute(
                        text(f'CREATE INDEX IF NOT EXISTS "{index_name}" ON "{table_name}" ("{col.name}");')
                    )

    if added_count == 0:
        print("  All tables and columns are up to date.")
    else:
        print(f"  Successfully synchronized {added_count} missing column(s).")


if __name__ == "__main__":
    sync_schema()
