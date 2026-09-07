"""Application data access backed by Supabase (PostgREST). UI modules should not contain SQL."""

from __future__ import annotations

import json
import secrets

from db import client, utc_now


def _rows(result) -> list[dict]:
    return result.data if result.data else []


def quizzes_for_teacher(owner_id: int):
    return _rows(
        client().table("quizzes").select("*")
        .eq("owner_id", owner_id)
        .order("created_at", desc=True)
        .execute()
    )


def quiz_for_teacher(quiz_id: int, owner_id: int):
    rows = _rows(
        client().table("quizzes").select("*")
        .eq("id", quiz_id)
        .eq("owner_id", owner_id)
        .limit(1)
        .execute()
    )
    return rows[0] if rows else None


def get_quiz(quiz_id: int):
    rows = _rows(client().table("quizzes").select("*").eq("id", quiz_id).limit(1).execute())
    return rows[0] if rows else None


def questions_for_quiz(quiz_id: int):
    return _rows(
        client().table("questions").select("*")
        .eq("quiz_id", quiz_id)
        .order("position")
        .execute()
    )


def move_question(quiz_id: int, question_id: int, direction: int) -> bool:
    questions = list(questions_for_quiz(quiz_id))
    current_index = next((index for index, question in enumerate(questions) if question["id"] == question_id), None)
    target_index = current_index + direction if current_index is not None else None
    if target_index is None or not 0 <= target_index < len(questions):
        return False
    supabase = client()
    supabase.table("questions").update({"position": -1}).eq("id", question_id).execute()
    supabase.table("questions").update({"position": current_index}).eq("id", questions[target_index]["id"]).execute()
    supabase.table("questions").update({"position": target_index}).eq("id", question_id).execute()
    return True


def students(teacher_id: int | None = None):
    supabase = client()
    if teacher_id:
        student_ids = [
            row["student_id"]
            for row in _rows(supabase.table("teacher_students").select("student_id").eq("teacher_id", teacher_id).execute())
        ]
        if not student_ids:
            return []
        return _rows(
            supabase.table("users").select("*")
            .in_("id", student_ids)
            .order("name")
            .order("email")
            .execute()
        )
    return _rows(
        supabase.table("users").select("*")
        .eq("role", "student")
        .order("name")
        .order("email")
        .execute()
    )


def add_student_to_roster(teacher_id: int, name: str, email: str, team_id: int | None = None) -> dict:
    supabase = client()
    email = email.lower()
    rows = _rows(supabase.table("users").select("*").eq("email", email).limit(1).execute())
    if rows:
        row = rows[0]
    else:
        row = _rows(
            supabase.table("users").insert(
                {"email": email, "name": name.strip(), "role": "student"}
            ).select("*").execute()
        )[0]
    if row["role"] != "student":
        raise ValueError("That email belongs to a teacher account.")
    supabase.table("teacher_students").upsert(
        {"teacher_id": teacher_id, "student_id": row["id"], "added_at": utc_now()},
        on_conflict="teacher_id,student_id",
    ).execute()
    if team_id is not None:
        supabase.table("team_students").upsert(
            {"team_id": team_id, "student_id": row["id"], "added_at": utc_now()},
            on_conflict="team_id,student_id",
        ).execute()
    return row


def teachers_for_student(student_id: int):
    teacher_ids = [
        row["teacher_id"]
        for row in _rows(
            client().table("teacher_students").select("teacher_id").eq("student_id", student_id).execute()
        )
    ]
    if not teacher_ids:
        return []
    return _rows(
        client().table("users").select("*")
        .eq("role", "teacher")
        .in_("id", teacher_ids)
        .order("name")
        .execute()
    )


def search_teachers(student_id: int, query: str):
    """Return teachers matching a name/email search, excluding those already joined."""
    query = query.strip()
    if not query:
        return []
    joined = {
        row["teacher_id"]
        for row in _rows(
            client().table("teacher_students").select("teacher_id").eq("student_id", student_id).execute()
        )
    }
    joined.add(student_id)
    supabase = client()
    by_name = _rows(
        supabase.table("users").select("*")
        .eq("role", "teacher")
        .ilike("name", f"%{query}%")
        .order("name")
        .execute()
    )
    by_email = _rows(
        supabase.table("users").select("*")
        .eq("role", "teacher")
        .ilike("email", f"%{query}%")
        .order("name")
        .execute()
    )
    seen: dict = {}
    for row in [*by_name, *by_email]:
        teacher_id = row["id"]
        if teacher_id in joined:
            continue
        seen.setdefault(teacher_id, row)
    return sorted(seen.values(), key=lambda row: row["name"])


def join_teacher(student_id: int, teacher_id: int) -> bool:
    """Join a teacher's roster. Returns False if already joined or invalid."""
    supabase = client()
    teacher = _rows(
        supabase.table("users").select("id").eq("id", teacher_id).eq("role", "teacher").limit(1).execute()
    )
    if not teacher:
        return False
    supabase.table("teacher_students").upsert(
        {"teacher_id": teacher_id, "student_id": student_id, "added_at": utc_now()},
        on_conflict="teacher_id,student_id",
    ).execute()
    return True


def leave_teacher(student_id: int, teacher_id: int) -> None:
    client().table("teacher_students").delete().eq("teacher_id", teacher_id).eq("student_id", student_id).execute()


def teams_for_teacher(teacher_id: int):
    return _rows(
        client().table("teams").select("*")
        .eq("teacher_id", teacher_id)
        .order("name")
        .execute()
    )


def create_team(teacher_id: int, name: str) -> int:
    return _rows(
        client().table("teams").insert(
            {"teacher_id": teacher_id, "name": name.strip(), "created_at": utc_now()}
        ).select("id").execute()
    )[0]["id"]


def team_student_ids(team_id: int) -> set[int]:
    rows = _rows(
        client().table("team_students").select("student_id").eq("team_id", team_id).execute()
    )
    return {row["student_id"] for row in rows}


def student_ids_for_teams(teacher_id: int, team_ids: list[int]) -> set[int]:
    if not team_ids:
        return set()
    team_rows = _rows(
        client().table("teams").select("id").eq("teacher_id", teacher_id).in_("id", team_ids).execute()
    )
    valid_team_ids = [row["id"] for row in team_rows]
    if not valid_team_ids:
        return set()
    rows = _rows(
        client().table("team_students").select("student_id").in_("team_id", valid_team_ids).execute()
    )
    return {row["student_id"] for row in rows}


def set_team_members(team_id: int, student_ids: list[int]) -> None:
    supabase = client()
    supabase.table("team_students").delete().eq("team_id", team_id).execute()
    if student_ids:
        supabase.table("team_students").insert(
            [{"team_id": team_id, "student_id": student_id, "added_at": utc_now()} for student_id in student_ids]
        ).execute()


def teams_for_student(teacher_id: int, student_id: int):
    team_rows = _rows(
        client().table("team_students").select("team_id").eq("student_id", student_id).execute()
    )
    team_ids = [row["team_id"] for row in team_rows]
    if not team_ids:
        return []
    return _rows(
        client().table("teams").select("*")
        .eq("teacher_id", teacher_id)
        .in_("id", team_ids)
        .order("name")
        .execute()
    )


def student_detail_analytics(teacher_id: int, student_id: int):
    supabase = client()
    membership = _rows(
        supabase.table("teacher_students").select("student_id")
        .eq("teacher_id", teacher_id)
        .eq("student_id", student_id)
        .limit(1)
        .execute()
    )
    if not membership:
        return None
    student = _rows(supabase.table("users").select("*").eq("id", student_id).limit(1).execute())
    if not student:
        return None
    student = student[0]

    teams = teams_for_student(teacher_id, student_id)

    quizzes = quizzes_for_teacher(teacher_id)
    quiz_lookup = {q["id"]: q for q in quizzes}
    my_qs = _rows(
        supabase.table("quiz_students").select("quiz_id").eq("student_id", student_id).execute()
    )
    joined_ids = [q["quiz_id"] for q in my_qs if q["quiz_id"] in quiz_lookup]

    attempts = []
    if joined_ids:
        attempts = _rows(
            supabase.table("attempts").select("*")
            .eq("student_id", student_id)
            .in_("quiz_id", joined_ids)
            .execute()
        )
        attempts_by_quiz: dict = {}
        for attempt in attempts:
            attempts_by_quiz.setdefault(attempt["quiz_id"], []).append(attempt)
    else:
        attempts_by_quiz = {}

    results = []
    ordered_quizzes = sorted(quizzes, key=lambda q: q["created_at"] or "", reverse=True)
    for quiz in ordered_quizzes:
        if quiz["id"] not in quiz_lookup or quiz["id"] not in set(joined_ids):
            continue
        quiz_attempts = sorted(attempts_by_quiz.get(quiz["id"], []), key=lambda a: a["started_at"] or "", reverse=True)
        if quiz_attempts:
            for attempt in quiz_attempts:
                results.append({
                    "title": quiz["title"], "quiz_id": quiz["id"],
                    "score_percent": attempt.get("score_percent"),
                    "passed": attempt.get("passed"),
                    "submitted_at": attempt.get("submitted_at"),
                    "started_at": attempt.get("started_at"),
                })
        else:
            results.append({
                "title": quiz["title"], "quiz_id": quiz["id"],
                "score_percent": None, "passed": None,
                "submitted_at": None, "started_at": None,
            })

    return {"student": student, "teams": teams, "results": results}


def assigned_student_ids(quiz_id: int) -> set[int]:
    rows = _rows(
        client().table("quiz_students").select("student_id").eq("quiz_id", quiz_id).execute()
    )
    return {row["student_id"] for row in rows}


def set_quiz_assignments(quiz_id: int, student_ids: list[int]) -> None:
    supabase = client()
    supabase.table("quiz_students").delete().eq("quiz_id", quiz_id).execute()
    if student_ids:
        supabase.table("quiz_students").insert(
            [{"quiz_id": quiz_id, "student_id": student_id, "assigned_at": utc_now()} for student_id in student_ids]
        ).execute()


def create_quiz(owner_id: int, title: str, duration: int, passing: int, allow_retake: bool, show_average: bool, opening_time: str, closing_time: str, student_ids: list[int], opening_enabled: bool = True, closing_enabled: bool = True, randomize_questions: bool = True, randomize_answers: bool = True) -> int:
    supabase = client()
    row = _rows(
        supabase.table("quizzes").insert(
            {
                "owner_id": owner_id, "title": title, "duration_minutes": duration,
                "passing_score": passing, "quiz_length": None,
                "allow_retake": int(allow_retake), "show_average": int(show_average),
                "opening_time": opening_time, "closing_time": closing_time,
                "opening_enabled": int(opening_enabled), "closing_enabled": int(closing_enabled),
                "randomize_questions": int(randomize_questions), "randomize_answers": int(randomize_answers),
                "status": "draft", "created_at": utc_now(),
            }
        ).select("id").execute()
    )[0]
    quiz_id = row["id"]
    if student_ids:
        supabase.table("quiz_students").insert(
            [{"quiz_id": quiz_id, "student_id": student_id, "assigned_at": utc_now()} for student_id in student_ids]
        ).execute()
    return quiz_id


def save_question_bank(quiz_id: int, questions: list[dict]) -> None:
    supabase = client()
    supabase.table("questions").delete().eq("quiz_id", quiz_id).execute()
    if questions:
        records = [
            {
                "quiz_id": quiz_id, "question_text": q["question_text"],
                "options_json": json.dumps(q["options"]),
                "correct_label": json.dumps(q["correct_label"]) if isinstance(q["correct_label"], list) else q["correct_label"],
                "question_type": q.get("question_type", "Multiple choice"),
                "position": i,
            }
            for i, q in enumerate(questions)
        ]
        supabase.table("questions").insert(records).execute()
        supabase.table("quizzes").update({"status": "active"}).eq("id", quiz_id).execute()


def update_quiz_settings(quiz_id: int, duration: int, passing: int, allow_retake: bool, show_average: bool, opening_time: str, closing_time: str, opening_enabled: bool, closing_enabled: bool, randomize_questions: bool = True, randomize_answers: bool = True) -> None:
    client().table("quizzes").update(
        {
            "duration_minutes": duration, "passing_score": passing,
            "allow_retake": int(allow_retake), "show_average": int(show_average),
            "opening_time": opening_time, "closing_time": closing_time,
            "opening_enabled": int(opening_enabled), "closing_enabled": int(closing_enabled),
            "randomize_questions": int(randomize_questions), "randomize_answers": int(randomize_answers),
        }
    ).eq("id", quiz_id).execute()


def delete_quiz(quiz_id: int, owner_id: int) -> bool:
    supabase = client()
    owned = _rows(
        supabase.table("quizzes").select("id").eq("id", quiz_id).eq("owner_id", owner_id).limit(1).execute()
    )
    if not owned:
        return False
    supabase.table("quizzes").delete().eq("id", quiz_id).execute()
    return True


def attempts_for_quiz(quiz_id: int):
    rows = _rows(
        client().table("attempts").select("*, users(name,email)").eq("quiz_id", quiz_id).execute()
    )
    completed = [row for row in rows if row.get("submitted_at")]
    completed.sort(key=lambda row: row["submitted_at"] or "", reverse=True)
    students = []
    for row in completed:
        user = row.get("users") or {}
        students.append({
            "student": user.get("name", ""),
            "email": user.get("email", ""),
            "score_percent": row.get("score_percent"),
            "passed": row.get("passed"),
            "submitted_at": row.get("submitted_at"),
            "auto_submitted": row.get("auto_submitted"),
        })
    return students


def teacher_analytics(teacher_id: int) -> dict:
    quizzes = quizzes_for_teacher(teacher_id)
    quiz_ids = [q["id"] for q in quizzes]
    supabase = client()
    attempts = []
    assigned_rows = []
    if quiz_ids:
        attempts = _rows(supabase.table("attempts").select("*").in_("quiz_id", quiz_ids).execute())
        assigned_rows = _rows(supabase.table("quiz_students").select("student_id").in_("quiz_id", quiz_ids).execute())
    attempts = [a for a in attempts if a["student_id"] != teacher_id]
    completed = [a for a in attempts if a.get("submitted_at")]
    scores = [a.get("score_percent") for a in completed if a.get("score_percent") is not None]
    passed = [a.get("passed") for a in completed if a.get("passed") is not None]
    return {
        "quizzes": len(quizzes),
        "active_quizzes": sum(1 for q in quizzes if q["status"] == "active"),
        "attempts": len(attempts),
        "completed": len(completed),
        "average_score": (sum(scores) / len(scores)) if scores else None,
        "pass_rate": (sum(passed) / len(passed)) if passed else None,
        "assigned_students": len({row["student_id"] for row in assigned_rows}),
    }


def student_analytics(teacher_id: int):
    roster = students(teacher_id)
    quiz_ids = [q["id"] for q in quizzes_for_teacher(teacher_id)]
    supabase = client()
    assigned_rows = []
    attempt_rows = []
    if quiz_ids:
        assigned_rows = _rows(supabase.table("quiz_students").select("student_id,quiz_id").in_("quiz_id", quiz_ids).execute())
        student_ids = [row["id"] for row in roster]
        if student_ids:
            attempt_rows = _rows(
                supabase.table("attempts").select("*").in_("student_id", student_ids).in_("quiz_id", quiz_ids).execute()
            )
    assigned_lookup: dict = {}
    for row in assigned_rows:
        assigned_lookup.setdefault(row["student_id"], set()).add(row["quiz_id"])
    by_student: dict = {}
    for attempt in attempt_rows:
        by_student.setdefault(attempt["student_id"], []).append(attempt)

    rows = []
    for student in roster:
        mine = by_student.get(student["id"], [])
        completed = [a for a in mine if a.get("submitted_at")]
        scores = [a.get("score_percent") for a in completed if a.get("score_percent") is not None]
        passed = [a.get("passed") for a in completed if a.get("passed") is not None]
        submitted_times = [a.get("submitted_at") for a in completed if a.get("submitted_at")]
        rows.append({
            "id": student["id"],
            "name": student["name"],
            "email": student["email"],
            "assigned_quizzes": len(assigned_lookup.get(student["id"], set())),
            "attempts": len(mine),
            "completed": len(completed),
            "average_score": (sum(scores) / len(scores)) if scores else None,
            "pass_rate": (sum(passed) / len(passed)) if passed else None,
            "last_activity": max(submitted_times) if submitted_times else None,
        })
    return rows


def student_progress_for_quiz(teacher_id: int, quiz_id: int):
    """Return every student who should be counted for this quiz, not only students with attempts."""
    supabase = client()
    quiz = _rows(
        supabase.table("quizzes").select("id").eq("id", quiz_id).eq("owner_id", teacher_id).limit(1).execute()
    )
    if not quiz:
        return []
    roster = students(teacher_id)
    assigned = _rows(
        supabase.table("quiz_students").select("student_id").eq("quiz_id", quiz_id).execute()
    )
    if assigned:
        assigned_ids = {row["student_id"] for row in assigned}
        students_rows = [student for student in roster if student["id"] in assigned_ids]
    else:
        students_rows = roster

    attempt_rows = []
    if students_rows and quiz_id:
        attempt_rows = _rows(
            supabase.table("attempts").select("*")
            .eq("quiz_id", quiz_id)
            .in_("student_id", [s["id"] for s in students_rows])
            .execute()
        )
    attempts_by_student: dict = {}
    for attempt in attempt_rows:
        attempts_by_student.setdefault(attempt["student_id"], []).append(attempt)

    progress = []
    for student in students_rows:
        sorted_attempts = sorted(attempts_by_student.get(student["id"], []), key=lambda a: a["started_at"] or "", reverse=True)
        attempt = sorted_attempts[0] if sorted_attempts else None
        status = "Not started" if attempt is None else ("Completed" if attempt.get("submitted_at") else "In progress")
        progress.append({
            "student": student["name"],
            "email": student["email"],
            "status": status,
            "score": attempt.get("score_percent") if attempt and attempt.get("score_percent") is not None else None,
            "result": ("Passed" if attempt.get("passed") else "Failed") if attempt and attempt.get("passed") is not None else "-",
            "last_activity": (attempt.get("submitted_at") or attempt.get("started_at")) if attempt else "-",
        })
    return progress


def available_quizzes(student_id: int, owner_id: int | None = None):
    now = utc_now()
    supabase = client()
    base = supabase.table("quizzes").select("*").eq("status", "active") \
        .or_(f"opening_enabled.eq.0,opening_time.lte.{now}") \
        .or_(f"closing_enabled.eq.0,closing_time.gte.{now}")
    if owner_id is not None:
        return _rows(base.eq("owner_id", owner_id).order("closing_time").execute())
    quizzes = _rows(base.order("closing_time").execute())
    quiz_student_rows = _rows(supabase.table("quiz_students").select("quiz_id,student_id").execute())
    mine = {row["quiz_id"] for row in quiz_student_rows if row["student_id"] == student_id}
    return [q for q in quizzes if q["id"] in mine]


def quiz_average_score(quiz_id: int):
    """Average score (percent) across completed attempts for a quiz, or None."""
    rows = _rows(
        client().table("attempts").select("score_percent")
        .eq("quiz_id", quiz_id)
        .not_.is_("submitted_at", None)
        .execute()
    )
    scores = [row.get("score_percent") for row in rows if row.get("score_percent") is not None]
    return (sum(scores) / len(scores)) if scores else None


def attempts_for_student(student_id: int):
    return _rows(
        client().table("attempts").select("*")
        .eq("student_id", student_id)
        .order("started_at", desc=True)
        .execute()
    )


def question_bank(quiz_id: int):
    return questions_for_quiz(quiz_id)


def open_attempt(quiz_id: int, student_id: int):
    rows = _rows(
        client().table("attempts").select("*")
        .eq("quiz_id", quiz_id)
        .eq("student_id", student_id)
        .is_("submitted_at", None)
        .limit(1)
        .execute()
    )
    return rows[0] if rows else None


def create_attempt(quiz_id: int, student_id: int, started_at: str, deadline_at: str, answers_json: str) -> int:
    return _rows(
        client().table("attempts").insert(
            {
                "quiz_id": quiz_id, "student_id": student_id,
                "started_at": started_at, "deadline_at": deadline_at,
                "answers_json": answers_json,
            }
        ).select("id").execute()
    )[0]["id"]


def attempt_with_quiz(attempt_id: int, student_id: int):
    rows = _rows(
        client().table("attempts").select("*")
        .eq("id", attempt_id)
        .eq("student_id", student_id)
        .limit(1)
        .execute()
    )
    if not rows:
        return None
    attempt = rows[0]
    quiz = get_quiz(attempt["quiz_id"])
    return {**attempt, "title": quiz["title"] if quiz else "", "passing_score": quiz["passing_score"] if quiz else 0}


def save_attempt_answers(attempt_id: int, answers_json: str) -> None:
    client().table("attempts").update({"answers_json": answers_json}).eq("id", attempt_id).execute()


def complete_attempt(attempt_id: int, answers_json: str, score: float, passed: bool, automatic: bool) -> None:
    client().table("attempts").update(
        {
            "answers_json": answers_json, "submitted_at": utc_now(),
            "score_percent": score, "passed": int(passed), "auto_submitted": int(automatic),
        }
    ).eq("id", attempt_id).execute()


def quiz_has_attempts(quiz_id: int) -> bool:
    return bool(_rows(
        client().table("attempts").select("id").eq("quiz_id", quiz_id).limit(1).execute()
    ))


def authenticate_admin(email: str, password: str):
    rows = _rows(
        client().table("admins").select("*").ilike("email", email.strip()).limit(1).execute()
    )
    row = rows[0] if rows else None
    return row if row and row["password"] == password else None


def authenticate(identifier: str, password: str) -> dict | None:
    """Universal login: accepts an admin email, or a user's email or full name (first and last name)."""
    identifier = identifier.strip()
    if not identifier:
        return None
    supabase = client()
    admin_rows = _rows(supabase.table("admins").select("*").ilike("email", identifier).limit(1).execute())
    if admin_rows and admin_rows[0]["password"] == password:
        return admin_session(admin_rows[0])
    user_rows = _rows(supabase.table("users").select("*").ilike("email", identifier).limit(1).execute())
    if not user_rows:
        user_rows = _rows(supabase.table("users").select("*").ilike("name", identifier).limit(1).execute())
    if user_rows and user_rows[0]["password"] and user_rows[0]["password"] == password:
        session = dict(user_rows[0])
        session.pop("password", None)
        return session
    return None


def admin_session(admin_row) -> dict:
    return {"id": admin_row["id"], "name": admin_row["name"], "role": "admin", "email": admin_row["email"]}


def admin_by_email(email: str) -> dict | None:
    """Return an admin session if this email is an administrator, else None."""
    rows = _rows(
        client().table("admins").select("*").ilike("email", email.strip().lower()).limit(1).execute()
    )
    return admin_session(rows[0]) if rows else None


def assign_role(user_id: int, role: str) -> None:
    """Assign an unassigned account a role: teacher, student, or admin.

    Admin accounts live in the `admins` table (Google sign-in is how they log
    in), so promoting to admin converts the user row into an admins row.
    """
    if role not in ("admin", "teacher", "student"):
        raise ValueError("Role must be admin, teacher, or student.")
    supabase = client()
    user_rows = _rows(supabase.table("users").select("*").eq("id", user_id).limit(1).execute())
    if not user_rows:
        raise ValueError("That account no longer exists.")
    user = user_rows[0]
    if role == "admin":
        existing = _rows(supabase.table("admins").select("*").ilike("email", user["email"]).limit(1).execute())
        if existing:
            supabase.table("admins").update({"name": user["name"]}).eq("id", existing[0]["id"]).execute()
        else:
            # Random password keeps the universal-login form locked; Google is
            # the intended sign-in for promoted administrators.
            supabase.table("admins").insert(
                {"email": user["email"].lower(), "password": secrets.token_hex(16),
                 "name": user["name"], "created_at": utc_now()}
            ).execute()
        try:
            supabase.table("users").delete().eq("id", user_id).execute()
        except Exception:
            raise ValueError("That account has existing activity and can't be converted to an administrator.")
    else:
        supabase.table("users").update({"role": role}).eq("id", user_id).execute()


def admins_list():
    return _rows(client().table("admins").select("*").order("email").execute())


def add_admin(email: str, password: str, name: str) -> int:
    if "@" not in email or not password:
        raise ValueError("Admin needs an email and password.")
    try:
        return _rows(
            client().table("admins").insert(
                {"email": email.strip().lower(), "password": password, "name": name.strip() or email.strip(), "created_at": utc_now()}
            ).select("id").execute()
        )[0]["id"]
    except Exception:
        raise ValueError("An admin with that email already exists.")


def remove_admin(admin_id: int, count_allowed: bool = False) -> None:
    supabase = client()
    remaining = len(_rows(supabase.table("admins").select("id").execute()))
    if remaining <= 1 and not count_allowed:
        raise ValueError("Cannot remove the last administrator.")
    supabase.table("admins").delete().eq("id", admin_id).execute()


def users_by_role(role: str):
    return _rows(
        client().table("users").select("*")
        .eq("role", role)
        .order("name")
        .order("email")
        .execute()
    )


def create_user(name: str, email: str, role: str, password: str) -> dict:
    if not name.strip() or "@" not in email:
        raise ValueError("Enter a name and a valid email address.")
    if not password:
        raise ValueError("Set a password for this account.")
    email = email.lower()
    existing = _rows(client().table("users").select("id").eq("email", email).limit(1).execute())
    if existing:
        raise ValueError("A user with that email already exists.")
    return _rows(
        client().table("users").insert(
            {"email": email, "name": name.strip(), "role": role, "password": password}
        ).select("*").execute()
    )[0]


def update_user(user_id: int, name: str = None, email: str = None, password: str = None) -> None:
    supabase = client()
    user_rows = _rows(supabase.table("users").select("*").eq("id", user_id).limit(1).execute())
    if not user_rows:
        raise ValueError("That user no longer exists.")
    user = user_rows[0]
    new_email = (email or user["email"]).lower()
    if email and "@" not in email:
        raise ValueError("Enter a valid email address.")
    if email and email.lower() != user["email"]:
        duplicates = _rows(supabase.table("users").select("id").eq("email", new_email).neq("id", user_id).limit(1).execute())
        if duplicates:
            raise ValueError("Another user already uses that email.")
    if name is not None and not name.strip():
        raise ValueError("Name cannot be empty.")
    supabase.table("users").update(
        {
            "name": name.strip() if name is not None else user["name"],
            "email": new_email,
            "password": password if password is not None else user["password"],
        }
    ).eq("id", user_id).execute()


def update_admin(admin_id: int, email: str = None, name: str = None, password: str = None) -> None:
    supabase = client()
    admin_rows = _rows(supabase.table("admins").select("*").eq("id", admin_id).limit(1).execute())
    if not admin_rows:
        raise ValueError("That admin no longer exists.")
    admin = admin_rows[0]
    new_email = (email or admin["email"]).strip().lower()
    if email and "@" not in email:
        raise ValueError("Enter a valid email address.")
    if email and new_email != admin["email"].lower():
        duplicates = _rows(supabase.table("admins").select("id").ilike("email", new_email).neq("id", admin_id).limit(1).execute())
        if duplicates:
            raise ValueError("Another admin already uses that email.")
    supabase.table("admins").update(
        {
            "email": new_email,
            "name": name.strip() if name else admin["name"],
            "password": password if password is not None else admin["password"],
        }
    ).eq("id", admin_id).execute()


def remove_user(user_id: int) -> None:
    supabase = client()
    existing = _rows(supabase.table("users").select("id").eq("id", user_id).limit(1).execute())
    if not existing:
        raise ValueError("That user no longer exists.")
    try:
        supabase.table("users").delete().eq("id", user_id).execute()
    except Exception:
        raise ValueError("This user has quizzes, teams, or attempt history and cannot be removed.")