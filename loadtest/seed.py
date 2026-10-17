"""Create, check and remove the ZZTEST data a load test runs against.

Everything this writes is prefixed ZZTEST and owned by one ZZTEST teacher, so
`cleanup` can find all of it again without touching anybody else's rows.

    python loadtest/seed.py accounts --students 150 --out accounts.json
    python loadtest/seed.py quiz --accounts accounts.json --title "ZZTEST Load A" --students 35 --out quiz.json
    python loadtest/seed.py verify --quiz quiz.json --results results.json
    python loadtest/seed.py drop-quiz --quiz quiz.json
    python loadtest/seed.py cleanup
"""

from __future__ import annotations

import argparse
import json
import random
import secrets
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import grading  # noqa: E402
import repository  # noqa: E402
from db import client, utc_now  # noqa: E402

PREFIX = "ZZTEST"
TEACHER_EMAIL = "zztest-load-teacher@mcq.local"


def student_email(number: int) -> str:
    return f"zztest-load-{number:03d}@mcq.local"


# --------------------------------------------------------------------------- accounts


def make_accounts(count: int, out: Path) -> None:
    supabase = client()
    password = secrets.token_urlsafe(12)
    teacher = repository.user_by_email(TEACHER_EMAIL)
    if teacher is None:
        teacher = repository.create_user(f"{PREFIX} Load Teacher", TEACHER_EMAIL, "teacher", password)
    else:
        supabase.table("users").update({"password": repository.hash_password(password)}).eq("id", teacher["id"]).execute()

    def one(number: int) -> dict:
        email = student_email(number)
        row = repository.user_by_email(email)
        if row is None:
            row = repository.create_user(f"{PREFIX} Load Student {number:03d}", email, "student", password)
        else:
            supabase.table("users").update({"password": repository.hash_password(password)}).eq("id", row["id"]).execute()
        return {"id": row["id"], "email": email}

    # One at a time: the shared HTTP/2 client raised WinError 10035 with 8 threads on it.
    with ThreadPoolExecutor(1) as pool:
        students = list(pool.map(one, range(1, count + 1)))
    supabase.table("teacher_students").upsert(
        [{"teacher_id": teacher["id"], "student_id": s["id"], "added_at": utc_now()} for s in students],
        on_conflict="teacher_id,student_id",
    ).execute()
    out.write_text(json.dumps({"password": password, "teacher_id": teacher["id"], "students": students}, indent=1))
    print(f"{len(students)} students + teacher {teacher['id']} ready; credentials in {out}")


# --------------------------------------------------------------------------- the paper

# A made-up balance sheet the size of the real ones (~8 KB): the Description/Context
# block is drawn above every question on every five-second refresh, so its size
# is part of the load and should not be understated.
def case_material() -> str:
    rng = random.Random(7)
    lines = ["**ZZTEST Holdings Ltd -- Statement of financial position (in $000)**", "",
             "| Line item | 2024 | 2025 | Note |", "| --- | ---: | ---: | --- |"]
    items = ["Cash and equivalents", "Trade receivables", "Inventories", "Prepaid expenses",
             "Short-term investments", "Property, plant and equipment", "Right-of-use assets",
             "Intangible assets", "Goodwill", "Deferred tax assets", "Trade payables",
             "Accrued liabilities", "Short-term borrowings", "Current portion of long-term debt",
             "Lease liabilities", "Long-term debt", "Deferred tax liabilities", "Provisions",
             "Share capital", "Share premium", "Retained earnings", "Other reserves"]
    for round_ in range(4):
        for item in items:
            lines.append(f"| {item} ({round_ + 1}) | ${rng.randint(100, 9000):,} | ${rng.randint(100, 9000):,} | "
                         f"See note {rng.randint(1, 30)} |")
        lines.append("")
        if round_ < 3:
            lines += [f"**Segment {round_ + 2}**", "", "| Line item | 2024 | 2025 | Note |", "| --- | ---: | ---: | --- |"]
    return "\n".join(lines)


def build_questions() -> list[dict]:
    """30 questions in the mix the real quizzes use: mostly MCQ, some T/F, one of each other kind."""
    rng = random.Random(11)
    questions = []
    for n in range(1, 26):
        values = rng.sample(range(10, 400), 4)
        options = [[label, f"{value / 100:.2f}x (option {label.lower()}{n})"] for label, value in zip("ABCD", values)]
        questions.append({"question_text": f"{PREFIX} Q{n:02d}: Using the statement above, which ratio is correct for line set {n}?",
                          "options": options, "correct_label": rng.choice("ABCD"),
                          "question_type": "Multiple choice"})
    for n in range(26, 29):
        questions.append({"question_text": f"{PREFIX} Q{n:02d}: Statement {n} about the balance sheet holds.",
                          "options": [["A", "True"], ["B", "False"]], "correct_label": rng.choice("AB"),
                          "question_type": "True / False"})
    questions.append({"question_text": f"{PREFIX} Q29: Select every current asset.",
                      "options": [["A", "Inventories"], ["B", "Goodwill"], ["C", "Trade receivables"], ["D", "Long-term debt"]],
                      "correct_label": ["A", "C"], "question_type": "Multiple choice - select all that apply"})
    questions.append({"question_text": f"{PREFIX} Q30: What is 12 * 7?", "options": [],
                      "correct_label": grading.build_spec("84", grading.NUMBER), "question_type": "Fill in the blank"})
    return questions


def answer_key(questions: list[dict]) -> dict:
    """question text -> what a fully-correct student would choose, in the form the harness sends."""
    key = {}
    for q in questions:
        options = dict((label, text) for label, text in q["options"])
        if q["question_type"] == "Fill in the blank":
            key[q["question_text"]] = {"type": "text", "correct": "84", "wrong": "85"}
        elif isinstance(q["correct_label"], list):
            key[q["question_text"]] = {
                "type": "multi", "correct": sorted(q["correct_label"]),
                "wrong": ["A", "B"], "options": options}
        else:
            key[q["question_text"]] = {"type": "single", "correct": q["correct_label"], "options": options}
    return key


def make_quiz(accounts: dict, title: str, students: int, duration: int, out: Path) -> None:
    if not title.startswith(PREFIX):
        raise SystemExit("Quiz titles must start with ZZTEST so cleanup can find them.")
    ids = [s["id"] for s in accounts["students"][:students]]
    now = datetime.now(timezone.utc)
    questions = build_questions()
    quiz_id = repository.create_quiz(
        accounts["teacher_id"], title, duration, 50, allow_retake=False, show_average=True,
        opening_time=(now - timedelta(minutes=5)).isoformat(),
        closing_time=(now + timedelta(days=1)).isoformat(),
        student_ids=ids, randomize_questions=True, randomize_answers=True,
        case_material=case_material(),
    )
    repository.save_question_bank(quiz_id, questions)
    out.write_text(json.dumps({"quiz_id": quiz_id, "title": title, "students": ids,
                               "key": answer_key(questions)}, indent=1))
    print(f"quiz {quiz_id} '{title}' assigned to {len(ids)} students; key in {out}")


# --------------------------------------------------------------------------- checking


def verify(quiz: dict, results: dict) -> int:
    """Every student who said they submitted has exactly one submitted attempt, holding
    exactly the answers they gave, marked with exactly the score those answers earn."""
    rows = client().table("attempts").select("*").eq("quiz_id", quiz["quiz_id"]).execute().data
    by_student: dict[int, list] = {}
    for row in rows:
        by_student.setdefault(row["student_id"], []).append(row)
    problems = []
    checked = 0
    for vu in results["users"]:
        sid = vu["student_id"]
        attempts = by_student.get(sid, [])
        if vu.get("outcome") != "submitted":
            if attempts and any(a["submitted_at"] for a in attempts):
                problems.append(f"student {sid}: harness says {vu.get('outcome')} but DB has a submitted attempt")
            continue
        checked += 1
        if len(attempts) != 1:
            problems.append(f"student {sid}: {len(attempts)} attempts (expected 1)")
            continue
        attempt = attempts[0]
        if not attempt["submitted_at"]:
            problems.append(f"student {sid}: attempt {attempt['id']} never submitted")
            continue
        if bool(attempt["auto_submitted"]) != bool(vu.get("auto_submitted")):
            problems.append(f"student {sid}: auto_submitted={attempt['auto_submitted']}, "
                            f"expected {bool(vu.get('auto_submitted'))}")
        payload = json.loads(attempt["answers_json"])
        stored = {q["text"]: payload["answers"].get(str(i)) for i, q in enumerate(payload["questions"])}
        for text, given in vu["answers"].items():
            if stored.get(text) != given:
                problems.append(f"student {sid}: '{text[:30]}' stored {stored.get(text)!r}, gave {given!r}")
        if abs((attempt["score_percent"] or 0) - vu["expected_score"]) > 0.01:
            problems.append(f"student {sid}: score {attempt['score_percent']} != expected {vu['expected_score']}")
    extra = set(by_student) - {vu["student_id"] for vu in results["users"]}
    for sid in extra:
        problems.append(f"student {sid}: has attempts but was not part of the run")
    print(f"checked {checked} submitted students against the database: {len(problems)} problem(s)")
    for p in problems:
        print("  PROBLEM", p)
    return 1 if problems else 0


# --------------------------------------------------------------------------- removal


def drop_quiz(quiz: dict) -> None:
    supabase = client()
    rows = supabase.table("quizzes").select("id,title").eq("id", quiz["quiz_id"]).execute().data
    if rows and rows[0]["title"].startswith(PREFIX):
        supabase.table("quizzes").delete().eq("id", quiz["quiz_id"]).execute()
        print(f"deleted quiz {quiz['quiz_id']}")


def cleanup() -> None:
    supabase = client()
    teacher = repository.user_by_email(TEACHER_EMAIL)
    if teacher:
        quizzes = supabase.table("quizzes").select("id,title").eq("owner_id", teacher["id"]).execute().data
        for q in quizzes:
            if not q["title"].startswith(PREFIX):
                raise SystemExit(f"quiz {q['id']} '{q['title']}' is not ZZTEST -- refusing to touch it")
            supabase.table("quizzes").delete().eq("id", q["id"]).execute()
        print(f"deleted {len(quizzes)} quizzes")
    users = supabase.table("users").select("id,email,name").like("email", "zztest-load-%@mcq.local").execute().data
    users = [u for u in users if u["name"].startswith(PREFIX)]
    ids = [u["id"] for u in users]
    for chunk in range(0, len(ids), 50):
        part = ids[chunk:chunk + 50]
        # Attempts reference users without a cascade; none should survive the quiz
        # deletes above, but a stray one would block the user delete.
        supabase.table("attempts").delete().in_("student_id", part).execute()
        supabase.table("users").delete().in_("id", part).execute()
    print(f"deleted {len(ids)} ZZTEST users")


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("accounts")
    a.add_argument("--students", type=int, default=35)
    a.add_argument("--out", type=Path, required=True)
    q = sub.add_parser("quiz")
    q.add_argument("--accounts", type=Path, required=True)
    q.add_argument("--title", required=True)
    q.add_argument("--students", type=int, default=35)
    q.add_argument("--duration", type=int, default=30)
    q.add_argument("--out", type=Path, required=True)
    v = sub.add_parser("verify")
    v.add_argument("--quiz", type=Path, required=True)
    v.add_argument("--results", type=Path, required=True)
    d = sub.add_parser("drop-quiz")
    d.add_argument("--quiz", type=Path, required=True)
    sub.add_parser("cleanup")
    args = parser.parse_args()
    if args.cmd == "accounts":
        make_accounts(args.students, args.out)
    elif args.cmd == "quiz":
        make_quiz(json.loads(args.accounts.read_text()), args.title, args.students, args.duration, args.out)
    elif args.cmd == "verify":
        sys.exit(verify(json.loads(args.quiz.read_text()), json.loads(args.results.read_text())))
    elif args.cmd == "drop-quiz":
        drop_quiz(json.loads(args.quiz.read_text()))
    else:
        cleanup()


if __name__ == "__main__":
    main()
