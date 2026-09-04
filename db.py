"""Small SQLite persistence layer for the MCQ V3 Streamlit app."""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


# The live database stays off Dropbox; file sync and SQLite corrupt each other.
# backup_db.py snapshots it back to data/mcq.db for versioned backups.
DB_PATH = Path(os.getenv("MCQ_DB_PATH", Path.home() / "mcq-data" / "mcq.db"))


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def init_db() -> None:
    with connect() as db:
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY,
                email TEXT NOT NULL UNIQUE,
                name TEXT NOT NULL,
                role TEXT NOT NULL CHECK(role IN ('teacher', 'student')),
                password TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS admins (
                id INTEGER PRIMARY KEY,
                email TEXT NOT NULL UNIQUE,
                password TEXT NOT NULL,
                name TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS quizzes (
                id INTEGER PRIMARY KEY,
                owner_id INTEGER NOT NULL REFERENCES users(id),
                title TEXT NOT NULL,
                duration_minutes INTEGER NOT NULL,
                passing_score INTEGER NOT NULL,
                quiz_length INTEGER,
                allow_retake INTEGER NOT NULL DEFAULT 0,
                show_average INTEGER NOT NULL DEFAULT 0,
                opening_time TEXT NOT NULL,
                closing_time TEXT NOT NULL,
                opening_enabled INTEGER NOT NULL DEFAULT 1,
                closing_enabled INTEGER NOT NULL DEFAULT 1,
                randomize_questions INTEGER NOT NULL DEFAULT 1,
                randomize_answers INTEGER NOT NULL DEFAULT 1,
                status TEXT NOT NULL DEFAULT 'active',
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS quiz_students (
                quiz_id INTEGER NOT NULL REFERENCES quizzes(id) ON DELETE CASCADE,
                student_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                assigned_at TEXT NOT NULL,
                PRIMARY KEY (quiz_id, student_id)
            );
            CREATE TABLE IF NOT EXISTS teacher_students (
                teacher_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                student_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                added_at TEXT NOT NULL,
                PRIMARY KEY (teacher_id, student_id)
            );
            CREATE TABLE IF NOT EXISTS teams (
                id INTEGER PRIMARY KEY,
                teacher_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                name TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE (teacher_id, name)
            );
            CREATE TABLE IF NOT EXISTS team_students (
                team_id INTEGER NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
                student_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                added_at TEXT NOT NULL,
                PRIMARY KEY (team_id, student_id)
            );
            CREATE TABLE IF NOT EXISTS questions (
                id INTEGER PRIMARY KEY,
                quiz_id INTEGER NOT NULL REFERENCES quizzes(id) ON DELETE CASCADE,
                question_text TEXT NOT NULL,
                options_json TEXT NOT NULL,
                correct_label TEXT NOT NULL,
                question_type TEXT NOT NULL DEFAULT 'Multiple choice',
                position INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS attempts (
                id INTEGER PRIMARY KEY,
                quiz_id INTEGER NOT NULL REFERENCES quizzes(id) ON DELETE CASCADE,
                student_id INTEGER NOT NULL REFERENCES users(id),
                started_at TEXT NOT NULL,
                deadline_at TEXT NOT NULL,
                submitted_at TEXT,
                score_percent REAL,
                passed INTEGER,
                auto_submitted INTEGER NOT NULL DEFAULT 0,
                answers_json TEXT NOT NULL DEFAULT '{}'
            );
            CREATE UNIQUE INDEX IF NOT EXISTS one_open_attempt
                ON attempts(quiz_id, student_id) WHERE submitted_at IS NULL;
            """
        )
        quiz_columns = {row["name"] for row in db.execute("PRAGMA table_info(quizzes)")}
        if "opening_enabled" not in quiz_columns:
            db.execute("ALTER TABLE quizzes ADD COLUMN opening_enabled INTEGER NOT NULL DEFAULT 1")
        if "closing_enabled" not in quiz_columns:
            db.execute("ALTER TABLE quizzes ADD COLUMN closing_enabled INTEGER NOT NULL DEFAULT 1")
        user_columns = {row["name"] for row in db.execute("PRAGMA table_info(users)")}
        if "password" not in user_columns:
            db.execute("ALTER TABLE users ADD COLUMN password TEXT NOT NULL DEFAULT 'password'")
        if "randomize_questions" not in quiz_columns:
            db.execute("ALTER TABLE quizzes ADD COLUMN randomize_questions INTEGER NOT NULL DEFAULT 1")
        if "randomize_answers" not in quiz_columns:
            db.execute("ALTER TABLE quizzes ADD COLUMN randomize_answers INTEGER NOT NULL DEFAULT 1")
        columns = {row["name"] for row in db.execute("PRAGMA table_info(questions)")}
        if "question_type" not in columns:
            db.execute("ALTER TABLE questions ADD COLUMN question_type TEXT NOT NULL DEFAULT 'Multiple choice'")
        admin_columns = {row["name"] for row in db.execute("PRAGMA table_info(admins)")}
        if "email" not in admin_columns:
            db.executescript("""
                ALTER TABLE admins ADD COLUMN email TEXT;
                UPDATE admins SET email = lower(username) || '@mcq.local' WHERE email IS NULL OR email = '';
                CREATE TABLE admins_new (
                    id INTEGER PRIMARY KEY,
                    email TEXT NOT NULL UNIQUE,
                    password TEXT NOT NULL,
                    name TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                INSERT INTO admins_new(id, email, password, name, created_at)
                    SELECT id, email, password, name, created_at FROM admins;
                DROP TABLE admins;
                ALTER TABLE admins_new RENAME TO admins;
            """)


def get_or_create_user(email: str, name: str, role: str) -> sqlite3.Row:
    with connect() as db:
        row = db.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        if row:
            return row
        cursor = db.execute(
            "INSERT INTO users(email, name, role) VALUES (?, ?, ?)",
            (email, name, role),
        )
        return db.execute("SELECT * FROM users WHERE id = ?", (cursor.lastrowid,)).fetchone()


def seed_demo_data() -> None:
    init_db()
    teacher = get_or_create_user("teacher@mcq.local", "Avery Morgan", "teacher")
    student = get_or_create_user("student@mcq.local", "Jordan Lee", "student")
    with connect() as db:
        db.execute("INSERT OR IGNORE INTO admins(email, password, name, created_at) VALUES ('admin@mcq.local', 'admin', 'Administrator', ?)", (utc_now(),))
        for email, password in (("teacher@mcq.local", "teacher"), ("student@mcq.local", "student")):
            db.execute("UPDATE users SET password = ? WHERE email = ? AND (password = '' OR password = 'password')", (password, email))
        db.executemany(
            "INSERT OR IGNORE INTO teams(teacher_id, name, created_at) VALUES (?, ?, ?)",
            [(teacher["id"], name, utc_now()) for name in ("Tutors", "Software", "Finance", "Assistants")],
        )
        existing = db.execute("SELECT id FROM quizzes WHERE owner_id = ?", (teacher["id"],)).fetchone()
        if existing:
            db.execute(
                "INSERT OR IGNORE INTO quiz_students(quiz_id, student_id, assigned_at) VALUES (?, ?, ?)",
                (existing["id"], student["id"], utc_now()),
            )
            db.execute(
                "INSERT OR IGNORE INTO teacher_students(teacher_id, student_id, added_at) VALUES (?, ?, ?)",
                (teacher["id"], student["id"], utc_now()),
            )
            return
        now = utc_now()
        quiz = db.execute(
            """INSERT INTO quizzes(owner_id, title, duration_minutes, passing_score,
            quiz_length, allow_retake, show_average, opening_time, closing_time, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (teacher["id"], "Foundations of Computing", 20, 70, 5, 1, 1,
             now, "2099-12-31T23:59:59+00:00", now),
        )
        questions = [
            ("What does CPU stand for?", [("A", "Central Processing Unit"), ("B", "Computer Personal Utility"), ("C", "Core Program User"), ("D", "Central Program Upload")], "A"),
            ("Which structure stores key-value pairs?", [("A", "Array"), ("B", "Dictionary"), ("C", "Queue"), ("D", "Tuple")], "B"),
            ("What is HTTP primarily used for?", [("A", "Transferring web resources"), ("B", "Compressing images"), ("C", "Encrypting disks"), ("D", "Rendering fonts")], "A"),
            ("Which is a version-control system?", [("A", "Git"), ("B", "Figma"), ("C", "SQLite"), ("D", "Docker")], "A"),
            ("What does SQL query?", [("A", "Sound files"), ("B", "Structured data"), ("C", "Screen layouts"), ("D", "System logs")], "B"),
            ("Which value represents true or false?", [("A", "Boolean"), ("B", "Float"), ("C", "String"), ("D", "Byte")], "A"),
        ]
        db.executemany(
            "INSERT INTO questions(quiz_id, question_text, options_json, correct_label, position) VALUES (?, ?, ?, ?, ?)",
            [(quiz.lastrowid, text, json.dumps(options), correct, index) for index, (text, options, correct) in enumerate(questions)],
        )
        db.execute(
            "INSERT OR IGNORE INTO quiz_students(quiz_id, student_id, assigned_at) VALUES (?, ?, ?)",
            (quiz.lastrowid, student["id"], now),
        )
        db.execute(
            "INSERT OR IGNORE INTO teacher_students(teacher_id, student_id, added_at) VALUES (?, ?, ?)",
            (teacher["id"], student["id"], now),
        )
