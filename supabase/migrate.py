"""One-time backfill: copy the live SQLite database into Supabase.

Run from the project root:
    python supabase/migrate.py [path/to/mcq.db]

Credentials come from SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY env vars,
or the [supabase] section of .streamlit/secrets.toml.
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def resolve_db_path(arg: str | None) -> Path:
    if arg:
        return Path(arg)
    if os.getenv("MCQ_DB_PATH"):
        return Path(os.getenv("MCQ_DB_PATH"))
    live = Path.home() / "mcq-data" / "mcq.db"
    if live.exists():
        return live
    return ROOT / "data" / "mcq.db"


def supabase_credentials() -> tuple[str, str]:
    url = os.getenv("SUPABASE_URL", "")
    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "") or os.getenv("SUPABASE_KEY", "")
    if url and key:
        return url, key
    secrets_path = ROOT / ".streamlit" / "secrets.toml"
    if secrets_path.exists():
        with open(secrets_path, "rb") as handle:
            section = tomllib.load(handle).get("supabase", {})
        url = url or section.get("url", "")
        key = key or section.get("service_role_key", "")
    if not url or not key:
        raise SystemExit("Missing Supabase credentials (env or .streamlit/secrets.toml [supabase]).")
    return url, key


def connect_sqlite(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    return connection


def rows(connection: sqlite3.Connection, table: str) -> list[dict]:
    return [dict(row) for row in connection.execute(f"SELECT * FROM {table}")]


def insert(supabase, table: str, records: list[dict], on_conflict: str | None = None) -> None:
    if not records:
        print(f"  {table}: 0 rows")
        return
    builder = supabase.table(table).upsert(records, on_conflict=on_conflict or "id")
    result = builder.execute()
    errors = getattr(result, "error", None)
    if errors:
        raise RuntimeError(f"Insert into {table} failed: {errors}")
    print(f"  {table}: {len(records)} rows")


def existing_ids(supabase, table: str) -> set[int]:
    data = supabase.table(table).select("id").execute().data
    return {row["id"] for row in data}


def main() -> None:
    db_path = resolve_db_path(sys.argv[1] if len(sys.argv) > 1 else None)
    sqlite_path = Path(db_path)
    if not sqlite_path.exists():
        raise SystemExit(f"SQLite database not found: {sqlite_path}. Pass the path as an argument.")

    from supabase import create_client

    url, key = supabase_credentials()
    supabase = create_client(url, key)

    print(f"SQLite source: {sqlite_path}\nSupabase:      {url}\n")
    print("Reading SQLite tables...")
    with connect_sqlite(sqlite_path) as source:
        users = rows(source, "users")
        admins = rows(source, "admins")
        quizzes = rows(source, "quizzes")
        quiz_students = rows(source, "quiz_students")
        teacher_students = rows(source, "teacher_students")
        teams = rows(source, "teams")
        team_students = rows(source, "team_students")
        questions = rows(source, "questions")
        attempts = rows(source, "attempts")

    def strip_null(record: dict) -> dict:
        return {k: v for k, v in record.items() if v is not None}

    insert(supabase, "users", [strip_null(row) for row in users], on_conflict="email")
    insert(supabase, "admins", [strip_null(row) for row in admins], on_conflict="email")
    insert(supabase, "teams", [strip_null(row) for row in teams])
    insert(supabase, "quizzes", [strip_null(row) for row in quizzes])
    insert(supabase, "questions", [strip_null(row) for row in questions])
    insert(supabase, "attempts", [strip_null(row) for row in attempts])
    insert(supabase, "quiz_students", [strip_null(row) for row in quiz_students], on_conflict="quiz_id,student_id")
    insert(supabase, "teacher_students", [strip_null(row) for row in teacher_students], on_conflict="teacher_id,student_id")
    insert(supabase, "team_students", [strip_null(row) for row in team_students], on_conflict="team_id,student_id")

    print("\nVerifying counts...")
    primary_keys = {
        "users": "id", "admins": "id", "quizzes": "id", "questions": "id",
        "attempts": "id", "quiz_students": "quiz_id", "teacher_students": "teacher_id",
        "teams": "id", "team_students": "team_id",
    }
    for table in primary_keys:
        remote = supabase.table(table).select(primary_keys[table], count="exact").execute()
        print(f"  {table}: {remote.count}")
    print("\nMigration complete.")


if __name__ == "__main__":
    main()