"""Application data access. UI modules should not contain SQL."""

from __future__ import annotations

import json

from db import connect, utc_now


def quizzes_for_teacher(owner_id: int):
    with connect() as db:
        return db.execute("SELECT * FROM quizzes WHERE owner_id = ? ORDER BY created_at DESC", (owner_id,)).fetchall()


def quiz_for_teacher(quiz_id: int, owner_id: int):
    with connect() as db:
        return db.execute("SELECT * FROM quizzes WHERE id = ? AND owner_id = ?", (quiz_id, owner_id)).fetchone()


def questions_for_quiz(quiz_id: int):
    with connect() as db:
        return db.execute("SELECT * FROM questions WHERE quiz_id = ? ORDER BY position", (quiz_id,)).fetchall()


def move_question(quiz_id: int, question_id: int, direction: int) -> bool:
    questions = list(questions_for_quiz(quiz_id))
    current_index = next((index for index, question in enumerate(questions) if question["id"] == question_id), None)
    target_index = current_index + direction if current_index is not None else None
    if target_index is None or not 0 <= target_index < len(questions):
        return False
    with connect() as db:
        db.execute("UPDATE questions SET position = ? WHERE id = ?", (-1, question_id))
        db.execute("UPDATE questions SET position = ? WHERE id = ?", (current_index, questions[target_index]["id"]))
        db.execute("UPDATE questions SET position = ? WHERE id = ?", (target_index, question_id))
    return True


def students(teacher_id: int | None = None):
    with connect() as db:
        if teacher_id:
            rows = db.execute("""SELECT u.* FROM users u JOIN teacher_students ts
                ON ts.student_id = u.id WHERE ts.teacher_id = ?
                ORDER BY u.name, u.email""", (teacher_id,)).fetchall()
        else:
            rows = db.execute("SELECT * FROM users WHERE role = 'student' ORDER BY name, email").fetchall()
        return [dict(row) for row in rows]


def add_student_to_roster(teacher_id: int, name: str, email: str, team_id: int | None = None) -> dict:
    with connect() as db:
        row = db.execute("SELECT * FROM users WHERE email = ?", (email.lower(),)).fetchone()
        if row is None:
            cursor = db.execute("INSERT INTO users(email, name, role) VALUES (?, ?, 'student')", (email.lower(), name.strip()))
            student_id = cursor.lastrowid
            row = db.execute("SELECT * FROM users WHERE id = ?", (student_id,)).fetchone()
        elif row["role"] != "student":
            raise ValueError("That email belongs to a teacher account.")
        db.execute("INSERT OR IGNORE INTO teacher_students(teacher_id, student_id, added_at) VALUES (?, ?, ?)", (teacher_id, row["id"], utc_now()))
        if team_id is not None:
            db.execute("INSERT OR IGNORE INTO team_students(team_id, student_id, added_at) VALUES (?, ?, ?)", (team_id, row["id"], utc_now()))
        return dict(row)


def teams_for_teacher(teacher_id: int):
    with connect() as db:
        return [dict(row) for row in db.execute(
            "SELECT * FROM teams WHERE teacher_id = ? ORDER BY name", (teacher_id,)
        ).fetchall()]


def create_team(teacher_id: int, name: str) -> int:
    with connect() as db:
        cursor = db.execute("INSERT INTO teams(teacher_id, name, created_at) VALUES (?, ?, ?)", (teacher_id, name.strip(), utc_now()))
        return cursor.lastrowid


def team_student_ids(team_id: int) -> set[int]:
    with connect() as db:
        return {row["student_id"] for row in db.execute("SELECT student_id FROM team_students WHERE team_id = ?", (team_id,))}


def student_ids_for_teams(teacher_id: int, team_ids: list[int]) -> set[int]:
    if not team_ids:
        return set()
    with connect() as db:
        placeholders = ",".join("?" for _ in team_ids)
        rows = db.execute(f"""SELECT DISTINCT ts.student_id FROM team_students ts
            JOIN teams t ON t.id=ts.team_id WHERE t.teacher_id=? AND t.id IN ({placeholders})""", [teacher_id, *team_ids]).fetchall()
        return {row["student_id"] for row in rows}


def set_team_members(team_id: int, student_ids: list[int]) -> None:
    with connect() as db:
        db.execute("DELETE FROM team_students WHERE team_id = ?", (team_id,))
        db.executemany("INSERT INTO team_students(team_id, student_id, added_at) VALUES (?, ?, ?)", [(team_id, student_id, utc_now()) for student_id in student_ids])


def teams_for_student(teacher_id: int, student_id: int):
    with connect() as db:
        return db.execute("""SELECT t.* FROM teams t JOIN team_students ts ON ts.team_id=t.id
            WHERE t.teacher_id=? AND ts.student_id=? ORDER BY t.name""", (teacher_id, student_id)).fetchall()


def student_detail_analytics(teacher_id: int, student_id: int):
    with connect() as db:
        student = db.execute("""SELECT u.* FROM users u JOIN teacher_students ts ON ts.student_id=u.id
            WHERE ts.teacher_id=? AND u.id=?""", (teacher_id, student_id)).fetchone()
        if not student:
            return None
        rows = db.execute("""SELECT q.title, q.id AS quiz_id, a.score_percent, a.passed,
            a.submitted_at, a.started_at FROM quizzes q JOIN quiz_students qs ON qs.quiz_id=q.id
            LEFT JOIN attempts a ON a.quiz_id=q.id AND a.student_id=qs.student_id
            WHERE q.owner_id=? AND qs.student_id=? ORDER BY q.created_at DESC, a.started_at DESC""", (teacher_id, student_id)).fetchall()
        return {"student": dict(student), "teams": [dict(row) for row in teams_for_student(teacher_id, student_id)], "results": [dict(row) for row in rows]}


def assigned_student_ids(quiz_id: int) -> set[int]:
    with connect() as db:
        return {row["student_id"] for row in db.execute("SELECT student_id FROM quiz_students WHERE quiz_id = ?", (quiz_id,))}


def set_quiz_assignments(quiz_id: int, student_ids: list[int]) -> None:
    with connect() as db:
        db.execute("DELETE FROM quiz_students WHERE quiz_id = ?", (quiz_id,))
        db.executemany("INSERT INTO quiz_students(quiz_id, student_id, assigned_at) VALUES (?, ?, ?)", [(quiz_id, student_id, utc_now()) for student_id in student_ids])


def create_quiz(owner_id: int, title: str, duration: int, passing: int, allow_retake: bool, show_average: bool, opening_time: str, closing_time: str, student_ids: list[int], opening_enabled: bool = True, closing_enabled: bool = True) -> int:
    with connect() as db:
        cursor = db.execute("""INSERT INTO quizzes(owner_id,title,duration_minutes,passing_score,
            quiz_length,allow_retake,show_average,opening_time,closing_time,opening_enabled,closing_enabled,created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""", (owner_id, title, duration, passing, None, int(allow_retake), int(show_average), opening_time, closing_time, int(opening_enabled), int(closing_enabled), utc_now()))
        db.execute("UPDATE quizzes SET status = 'draft' WHERE id = ?", (cursor.lastrowid,))
        db.executemany("INSERT INTO quiz_students(quiz_id, student_id, assigned_at) VALUES (?, ?, ?)", [(cursor.lastrowid, student_id, utc_now()) for student_id in student_ids])
        return cursor.lastrowid


def save_question_bank(quiz_id: int, questions: list[dict]) -> None:
    with connect() as db:
        db.execute("DELETE FROM questions WHERE quiz_id = ?", (quiz_id,))
        db.executemany("INSERT INTO questions(quiz_id, question_text, options_json, correct_label, question_type, position) VALUES (?, ?, ?, ?, ?, ?)", [(quiz_id, q["question_text"], json.dumps(q["options"]), json.dumps(q["correct_label"]) if isinstance(q["correct_label"], list) else q["correct_label"], q.get("question_type", "Multiple choice"), i) for i, q in enumerate(questions)])
        if questions:
            db.execute("UPDATE quizzes SET status = 'active' WHERE id = ?", (quiz_id,))


def update_quiz_settings(quiz_id: int, duration: int, passing: int, allow_retake: bool, show_average: bool, opening_time: str, closing_time: str, opening_enabled: bool, closing_enabled: bool) -> None:
    with connect() as db:
        db.execute("""UPDATE quizzes SET duration_minutes=?, passing_score=?,
            allow_retake=?, show_average=?, opening_time=?, closing_time=?, opening_enabled=?, closing_enabled=? WHERE id=?""",
                   (duration, passing, int(allow_retake), int(show_average), opening_time, closing_time, int(opening_enabled), int(closing_enabled), quiz_id))


def delete_quiz(quiz_id: int, owner_id: int) -> bool:
    with connect() as db:
        cursor = db.execute("DELETE FROM quizzes WHERE id = ? AND owner_id = ?", (quiz_id, owner_id))
        return cursor.rowcount == 1


def attempts_for_quiz(quiz_id: int):
    with connect() as db:
        return db.execute("""SELECT u.name AS student, u.email, a.score_percent,
            a.passed, a.submitted_at, a.auto_submitted FROM attempts a
            JOIN users u ON u.id = a.student_id
            WHERE a.quiz_id = ? AND a.submitted_at IS NOT NULL
            ORDER BY a.submitted_at DESC""", (quiz_id,)).fetchall()


def teacher_analytics(teacher_id: int) -> dict:
    with connect() as db:
        row = db.execute("""SELECT COUNT(DISTINCT q.id) AS quizzes,
            COUNT(DISTINCT CASE WHEN q.status='active' THEN q.id END) AS active_quizzes,
            COUNT(DISTINCT a.id) AS attempts,
            COUNT(DISTINCT CASE WHEN a.submitted_at IS NOT NULL THEN a.id END) AS completed,
            AVG(CASE WHEN a.submitted_at IS NOT NULL THEN a.score_percent END) AS average_score,
            AVG(CASE WHEN a.submitted_at IS NOT NULL THEN a.passed END) AS pass_rate,
            COUNT(DISTINCT qs.student_id) AS assigned_students
            FROM quizzes q LEFT JOIN attempts a ON a.quiz_id=q.id
            LEFT JOIN quiz_students qs ON qs.quiz_id=q.id WHERE q.owner_id=?""", (teacher_id,)).fetchone()
        return dict(row)


def student_analytics(teacher_id: int):
    with connect() as db:
        return db.execute("""SELECT u.id, u.name, u.email,
            COUNT(DISTINCT qs.quiz_id) AS assigned_quizzes,
            COUNT(DISTINCT a.id) AS attempts,
            COUNT(DISTINCT CASE WHEN a.submitted_at IS NOT NULL THEN a.id END) AS completed,
            AVG(CASE WHEN a.submitted_at IS NOT NULL THEN a.score_percent END) AS average_score,
            AVG(CASE WHEN a.submitted_at IS NOT NULL THEN a.passed END) AS pass_rate,
            MAX(a.submitted_at) AS last_activity
            FROM users u JOIN teacher_students ts ON ts.student_id=u.id
            LEFT JOIN quiz_students qs ON qs.student_id=u.id
            LEFT JOIN quizzes q ON q.id=qs.quiz_id AND q.owner_id=ts.teacher_id
            LEFT JOIN attempts a ON a.student_id=u.id AND a.quiz_id=q.id
            WHERE ts.teacher_id=? GROUP BY u.id ORDER BY u.name, u.email""", (teacher_id,)).fetchall()


def student_progress_for_quiz(teacher_id: int, quiz_id: int):
    """Return every student who should be counted for this quiz, not only students with attempts."""
    with connect() as db:
        quiz = db.execute("SELECT id FROM quizzes WHERE id = ? AND owner_id = ?", (quiz_id, teacher_id)).fetchone()
        if not quiz:
            return []
        assigned = db.execute("SELECT student_id FROM quiz_students WHERE quiz_id = ?", (quiz_id,)).fetchall()
        if assigned:
            students_sql = """SELECT u.* FROM users u JOIN teacher_students ts ON ts.student_id=u.id
                WHERE ts.teacher_id=? AND u.id IN (SELECT student_id FROM quiz_students WHERE quiz_id=?)
                ORDER BY u.name, u.email"""
            students_rows = db.execute(students_sql, (teacher_id, quiz_id)).fetchall()
        else:
            students_rows = db.execute("""SELECT u.* FROM users u JOIN teacher_students ts
                ON ts.student_id=u.id WHERE ts.teacher_id=? ORDER BY u.name, u.email""", (teacher_id,)).fetchall()
        progress = []
        for student in students_rows:
            attempt = db.execute("""SELECT * FROM attempts WHERE quiz_id=? AND student_id=?
                ORDER BY started_at DESC LIMIT 1""", (quiz_id, student["id"])).fetchone()
            status = "Not started" if attempt is None else ("Completed" if attempt["submitted_at"] else "In progress")
            progress.append({"student": student["name"], "email": student["email"], "status": status,
                             "score": attempt["score_percent"] if attempt and attempt["score_percent"] is not None else None,
                             "result": ("Passed" if attempt["passed"] else "Failed") if attempt and attempt["passed"] is not None else "-",
                             "last_activity": (attempt["submitted_at"] or attempt["started_at"]) if attempt else "-"})
        return progress


def available_quizzes(student_id: int):
    with connect() as db:
        return db.execute("""SELECT DISTINCT q.* FROM quizzes q
            LEFT JOIN quiz_students qs ON qs.quiz_id = q.id
                        WHERE q.status = 'active'
                            AND (q.opening_enabled = 0 OR q.opening_time <= ?)
              AND (q.closing_enabled = 0 OR q.closing_time >= ?)
              AND (NOT EXISTS (SELECT 1 FROM quiz_students WHERE quiz_id = q.id)
                 OR qs.student_id = ?)
              ORDER BY q.closing_time""", (utc_now(), utc_now(), student_id)).fetchall()


def attempts_for_student(student_id: int):
    with connect() as db:
        return db.execute("SELECT * FROM attempts WHERE student_id = ? ORDER BY started_at DESC", (student_id,)).fetchall()


def question_bank(quiz_id: int):
    return questions_for_quiz(quiz_id)
