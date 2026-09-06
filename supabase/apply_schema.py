"""Apply supabase/schema.sql to the database.

Usage:
    python supabase/apply_schema.py "postgresql://postgres.REF:PASSWORD@aws-0-REGION.pooler.supabase.com:5432/postgres"

Or set the DATABASE_URL env var and omit the argument.
"""

from __future__ import annotations

import sys
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCHEMA_PATH = ROOT / "supabase" / "schema.sql"


def main() -> None:
    database_url = sys.argv[1] if len(sys.argv) > 1 else os.getenv("DATABASE_URL", "")
    if not database_url:
        raise SystemExit(
            "Pass the Pooler connection string as an argument or set DATABASE_URL.\n"
            "Find it in Supabase Dashboard -> Project Settings -> Database "
            "-> Connection string (Session pooler)."
        )
    import psycopg

    try:
        with psycopg.connect(database_url) as connection:
            connection.execute(SCHEMA_PATH.read_text())
        print("Schema applied.")
    except Exception as exc:
        raise SystemExit(f"Failed to apply schema: {exc}") from exc


if __name__ == "__main__":
    main()