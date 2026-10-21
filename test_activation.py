"""A quiz stays hidden until its teacher activates it. Run with: python test_activation.py

The rule this pins down was broken in October 2026, and students saw quizzes
that had not been covered in class yet. Saving a quiz's questions set it
"active", so the first publish put it on every assigned student's dashboard,
and the only things holding it back were its opening time and its audience.
The opening time was itself five hours early: the server runs on UTC, and
"8:00 AM" typed in Texas was saved as 8:00 UTC. A student who joined the roster
was handed every class-wide quiz on the spot.

The first half runs against the real database with ZZTEST rows it creates and
removes again. The second half is the clock arithmetic, which needs no database.
"""

import uuid
from datetime import date, datetime, time, timedelta, timezone

import local_time

FAILURES = []


def check(label, actual, expected):
    ok = actual == expected
    if not ok:
        FAILURES.append(label)
    print(("  pass " if ok else "  FAIL ") + label + ("" if ok else f"  expected {expected!r} got {actual!r}"))


print("== the class's clock, not the server's ==")
chicago = local_time.zone()
check("the default zone is the class's", chicago.key, "America/Chicago")
eight = local_time.combine(date(2026, 10, 21), time(8, 0))
check("8:00 AM typed by a teacher is 8:00 AM in Texas", eight.isoformat(), "2026-10-21T08:00:00-05:00")
check("which is 13:00 UTC, not 08:00 UTC", eight.astimezone(timezone.utc).hour, 13)
check("in winter it moves with daylight saving",
      local_time.combine(date(2026, 12, 1), time(8, 0)).astimezone(timezone.utc).hour, 14)
check("a stored UTC time reads back in Texas time", local_time.fmt("2026-10-21T08:00:00+00:00"),
      "Oct 21, 3:00 AM CDT")
check("a naive stored time is taken as UTC", local_time.fmt("2026-10-21T13:00:00", with_zone=False), "Oct 21, 8:00 AM")
check("round trip: what the teacher typed is what the editor shows again",
      local_time.parse(eight.isoformat()).time(), time(8, 0))


print()
print("== a published quiz is invisible until activated ==")

import db  # noqa: E402  (the clock tests above need no database)
import repository as R  # noqa: E402
import student_portal as SP  # noqa: E402
import streamlit as st  # noqa: E402

supabase = db.client()
tag = uuid.uuid4().hex[:8]
teacher = db.get_or_create_user(f"zztest-teacher-{tag}@mcq.local", f"ZZTEST Teacher {tag}", "teacher")
student = db.get_or_create_user(f"zztest-student-{tag}@mcq.local", f"ZZTEST Student {tag}", "student")
late = db.get_or_create_user(f"zztest-late-{tag}@mcq.local", f"ZZTEST Late {tag}", "student")
quiz_id = None
QUESTIONS = [{"question_text": "ZZTEST 2+2?", "options": [("A", "3"), ("B", "4")], "correct_label": "B",
              "question_type": "Multiple choice"}]


def offered(user_id):
    return [quiz["id"] for quiz in R.available_quizzes(user_id)]


try:
    R.join_teacher(student["id"], teacher["id"])
    now = datetime.now(timezone.utc)
    quiz_id = R.create_quiz(teacher["id"], f"ZZTEST activation {tag}", 10, 50, False, False,
                            (now - timedelta(hours=1)).isoformat(), (now + timedelta(hours=5)).isoformat(),
                            [student["id"]])
    R.save_question_bank(quiz_id, QUESTIONS)
    check("publishing leaves the quiz inactive", R.get_quiz(quiz_id)["status"], R.INACTIVE)
    check("so an assigned student, inside the window, does not see it", quiz_id in offered(student["id"]), False)
    check("but the teacher's own Student view does",
          quiz_id in [quiz["id"] for quiz in R.available_quizzes(teacher["id"], teacher["id"])], True)

    # Javier's route in: he joined the roster after the quiz was assigned to all.
    R.join_teacher(late["id"], teacher["id"])
    check("a student who joins later is assigned the class-wide quiz", late["id"] in R.assigned_student_ids(quiz_id), True)
    check("and still cannot see it while it is inactive", quiz_id in offered(late["id"]), False)

    # A card drawn before the teacher deactivated, or a replayed click.
    st.session_state.clear()
    SP.start_attempt(student, R.get_quiz(quiz_id))
    check("Start on a stale card is refused", st.session_state.get("quiz_not_active"), f"ZZTEST activation {tag}")
    check("and no attempt is made", R.open_attempt(quiz_id, student["id"]), None)

    check("activating it is the teacher's alone",
          R.set_quiz_active(quiz_id, student["id"], True), False)
    check("the quiz is still inactive", R.get_quiz(quiz_id)["status"], R.INACTIVE)
    check("the owner activates it", R.set_quiz_active(quiz_id, teacher["id"], True), True)
    check("and the student sees it", quiz_id in offered(student["id"]), True)

    R.save_question_bank(quiz_id, [{**QUESTIONS[0], "correct_label": "A"}])
    check("saving the questions again does not change whether it is live", R.get_quiz(quiz_id)["status"], R.ACTIVE)

    st.session_state.clear()
    SP.start_attempt(student, R.get_quiz(quiz_id))
    attempt = R.open_attempt(quiz_id, student["id"])
    check("an active quiz can be started", attempt is not None, True)

    R.set_quiz_active(quiz_id, teacher["id"], False)
    check("deactivating hides it again", quiz_id in offered(student["id"]), False)
    check("but never throws away a paper being written", R.open_attempt(quiz_id, student["id"]) is not None, True)
    st.session_state.clear()
    SP.start_attempt(student, R.get_quiz(quiz_id))
    check("which its student can still pick back up", st.session_state.get("attempt_id"), attempt["id"])

    empty = R.create_quiz(teacher["id"], f"ZZTEST empty {tag}", 10, 50, False, False, now.isoformat(),
                          (now + timedelta(hours=1)).isoformat(), [student["id"]])
    try:
        R.set_quiz_active(empty, teacher["id"], True)
        refused = None
    except ValueError as exc:
        refused = str(exc)
    check("a quiz with no questions cannot be activated", refused, R.QUIZ_HAS_NO_QUESTIONS)
    R.delete_quiz(empty, teacher["id"])
finally:
    if quiz_id is not None:
        R.delete_quiz(quiz_id, teacher["id"])
    for user in (teacher, student, late):
        supabase.table("teacher_students").delete().eq("teacher_id", user["id"]).execute()
        supabase.table("teacher_students").delete().eq("student_id", user["id"]).execute()
        supabase.table("users").delete().eq("id", user["id"]).execute()
    leftovers = supabase.table("users").select("id").like("email", f"zztest-%-{tag}@mcq.local").execute().data
    check("every ZZTEST row is gone", leftovers, [])

print()
if FAILURES:
    print(f"{len(FAILURES)} FAILURE(S): {FAILURES}")
    raise SystemExit(1)
print("All activation tests passed.")
