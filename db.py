"""Supabase persistence layer for the MCQ V3 Streamlit app."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from functools import lru_cache

import streamlit as st

# Prefer env vars (useful for scripts/migrations); fall back to st.secrets.
_SUPABASE_URL = os.getenv("SUPABASE_URL", "")
_SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "") or os.getenv("SUPABASE_KEY", "")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _resolve(secret_key: str, env: str) -> str:
    value = os.getenv(env, "")
    if value:
        return value
    try:
        return st.secrets["supabase"][secret_key]
    except Exception:
        return ""


@lru_cache(maxsize=1)
def client():
    """Return a shared Supabase client backed by the service-role key.

    The service-role key bypasses RLS; this app is a trusted server process,
    so that is intentional. Credentials are read from env vars or the
    [supabase] section of .streamlit/secrets.toml (gitignored).
    """
    from supabase import create_client  # lazy import keeps startup fast

    url = _resolve("url", "SUPABASE_URL")
    key = _resolve("service_role_key", "SUPABASE_SERVICE_ROLE_KEY")
    if not url or not key:
        raise RuntimeError(
            "Supabase credentials are missing. Set SUPABASE_URL and "
            "SUPABASE_SERVICE_ROLE_KEY env vars, or the [supabase] section "
            "of .streamlit/secrets.toml."
        )
    return create_client(url, key)


def init_db() -> None:
    """Verify connectivity. Schema DDL lives in supabase/schema.sql."""
    try:
        client().table("users").select("id").limit(1).execute()
    except Exception as exc:
        raise RuntimeError(f"Supabase is unreachable: {exc}") from exc


def get_or_create_user(email: str, name: str, role: str) -> dict:
    email = email.lower()
    row = client().table("users").select("*").eq("email", email).limit(1).execute().data
    if row:
        return row[0]
    inserted = client().table("users").insert(
        {"email": email, "name": name, "role": role}
    ).select("*").execute().data
    return inserted[0]


def seed_demo_data() -> None:
    """Seed demo users/teams/quiz only when absent (matches local demo flow)."""
    supabase = client()
    existing = supabase.table("users").select("email").execute().data
    emails = {row["email"] for row in existing}
    teacher = get_or_create_user("teacher@mcq.local", "Avery Morgan", "teacher")
    student = get_or_create_user("student@mcq.local", "Jordan Lee", "student")

    if "admin@mcq.local" not in emails:
        existing_admins = supabase.table("admins").select("email").execute().data
        if not existing_admins:
            supabase.table("admins").insert(
                {"email": "admin@mcq.local", "password": "admin", "name": "Administrator", "created_at": utc_now()}
            ).execute()

    if teacher["email"] not in emails:
        supabase.table("users").update({"password": "teacher"}).eq("email", teacher["email"]).execute()
    if student["email"] not in emails:
        supabase.table("users").update({"password": "student"}).eq("email", student["email"]).execute()

    for team_name in ("Tutors", "Software", "Finance", "Assistants"):
        existing_teams = supabase.table("teams").select("id").eq("teacher_id", teacher["id"]).eq("name", team_name).execute().data
        if not existing_teams:
            supabase.table("teams").insert(
                {"teacher_id": teacher["id"], "name": team_name, "created_at": utc_now()}
            ).execute()

    quiz_row = supabase.table("quizzes").select("id").eq("owner_id", teacher["id"]).limit(1).execute().data
    if quiz_row:
        quiz_id = quiz_row[0]["id"]
    else:
        quiz_id = supabase.table("quizzes").insert(
            {
                "owner_id": teacher["id"], "title": "Foundations of Computing",
                "duration_minutes": 20, "passing_score": 70, "quiz_length": 5,
                "allow_retake": 1, "show_average": 1,
                "opening_time": utc_now(), "closing_time": "2099-12-31T23:59:59+00:00",
                "created_at": utc_now(),
            }
        ).select("id").execute().data[0]["id"]

    questions = [
        ("What does CPU stand for?", [("A", "Central Processing Unit"), ("B", "Computer Personal Utility"), ("C", "Core Program User"), ("D", "Central Program Upload")], "A"),
        ("Which structure stores key-value pairs?", [("A", "Array"), ("B", "Dictionary"), ("C", "Queue"), ("D", "Tuple")], "B"),
        ("What is HTTP primarily used for?", [("A", "Transferring web resources"), ("B", "Compressing images"), ("C", "Encrypting disks"), ("D", "Rendering fonts")], "A"),
        ("Which is a version-control system?", [("A", "Git"), ("B", "Figma"), ("C", "SQLite"), ("D", "Docker")], "A"),
        ("What does SQL query?", [("A", "Sound files"), ("B", "Structured data"), ("C", "Screen layouts"), ("D", "System logs")], "B"),
        ("Which value represents true or false?", [("A", "Boolean"), ("B", "Float"), ("C", "String"), ("D", "Byte")], "A"),
    ]
    existing_questions = supabase.table("questions").select("id").eq("quiz_id", quiz_id).limit(1).execute().data
    if not existing_questions:
        supabase.table("questions").insert(
            [
                {"quiz_id": quiz_id, "question_text": text, "options_json": json.dumps(options),
                 "correct_label": correct, "position": index}
                for index, (text, options, correct) in enumerate(questions)
            ]
        ).execute()

    supabase.table("quiz_students").upsert(
        [{"quiz_id": quiz_id, "student_id": student["id"], "assigned_at": utc_now()}],
        on_conflict="quiz_id,student_id",
    ).execute()
    supabase.table("teacher_students").upsert(
        [{"teacher_id": teacher["id"], "student_id": student["id"], "added_at": utc_now()}],
        on_conflict="teacher_id,student_id",
    ).execute()