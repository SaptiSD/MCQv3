"""Classes: reading a list of students, sorting them in bulk, and the gradebook.

Run with: python test_classes.py

TAs sorting two classes of thirty did it one student at a time. The parser and
the gradebook are pure and tested without a database; moving students runs
against the real one with ZZTEST rows it creates and removes again.
"""

import uuid

import roster_import
from repository import gradebook_rows

FAILURES = []


def check(label, actual, expected):
    ok = actual == expected
    if not ok:
        FAILURES.append(label)
    print(("  pass " if ok else "  FAIL ") + label + ("" if ok else f"  expected {expected!r} got {actual!r}"))


print("== reading a pasted list ==")
reading = roster_import.parse("jordan.lee@tamu.edu\nSam Ortiz <Sam.Ortiz@tamu.edu>\n\nnot an address\njordan.lee@tamu.edu")
check("one row per address", [row["email"] for row in reading["rows"]], ["jordan.lee@tamu.edu", "sam.ortiz@tamu.edu"])
check("addresses are lower-cased", reading["rows"][1]["email"], "sam.ortiz@tamu.edu")
check("the rest of the line is the name", reading["rows"][1]["name"], "Sam Ortiz")
check("a line with no address is reported, not dropped silently", reading["problems"], ["Line 3: no email address"])
check("pasting someone twice adds them once", len(reading["rows"]), 2)
check("no class column, so no classes", reading["has_classes"], False)

to_line = roster_import.parse("a@tamu.edu; b@tamu.edu, c@tamu.edu")
check("a whole To: line gives every address", [row["email"] for row in to_line["rows"]],
      ["a@tamu.edu", "b@tamu.edu", "c@tamu.edu"])

print()
print("== reading a spreadsheet ==")
sheet = roster_import.parse("Last Name\tFirst Name\tEmail\tSection\nLee\tJordan\tjlee@tamu.edu\t501\n"
                            "Ortiz\tSam\tsortiz@tamu.edu\t502\nNoMail\tPat\t\t501")
check("tab-separated rows copied out of Excel", [row["email"] for row in sheet["rows"]], ["jlee@tamu.edu", "sortiz@tamu.edu"])
check("first and last name are joined", sheet["rows"][0]["name"], "Jordan Lee")
check("a Section column is the class", [row["class"] for row in sheet["rows"]], ["501", "502"])
check("so the list says which class each student is in", sheet["has_classes"], True)
check("a row without an address is named by its line", sheet["problems"], ["Line 4: no email address"])

csv_file = roster_import.read_upload("﻿email,name,class\r\njlee@tamu.edu,Jordan Lee,Section 501\r\n".encode("utf-8"))
check("a CSV with a byte-order mark", (csv_file["rows"][0]["email"], csv_file["rows"][0]["class"]),
      ("jlee@tamu.edu", "Section 501"))
ansi = roster_import.read_upload("email,name\njose@tamu.edu,Jos\xe9 Pe\xf1a\n".encode("cp1252"))
check("a CSV Excel saved in Windows ANSI keeps its accents", ansi["rows"][0]["name"], "José Peña")
template = roster_import.read_upload(roster_import.template_csv())
check("the downloadable template reads back", (len(template["rows"]), template["has_classes"]), (2, True))

print()
print("== the gradebook ==")
roster = [{"id": 1, "name": "Ada", "email": "ada@x"}, {"id": 2, "name": "Bo", "email": "bo@x"},
          {"id": 3, "name": "Cy", "email": "cy@x"}]
quizzes = [{"id": 10, "title": "Quiz 6"}, {"id": 11, "title": "Quiz 6"}]
attempts = [
    {"student_id": 1, "quiz_id": 10, "submitted_at": "2026-10-20T10:00:00+00:00", "score_percent": 40.0},
    {"student_id": 1, "quiz_id": 10, "submitted_at": "2026-10-20T11:00:00+00:00", "score_percent": 83.333},
    {"student_id": 2, "quiz_id": 11, "submitted_at": "2026-10-20T11:00:00+00:00", "score_percent": 100.0},
]
book = gradebook_rows(roster, quizzes, attempts, {1: [7], 2: [8]}, {7: "Section 501", 8: "Section 502"})
check("one column per quiz, same-named quizzes kept apart", book["columns"],
      ["Student", "Email", "Class", "Quiz 6", "Quiz 6 (2)"])
by_name = {row["Student"]: row for row in book["rows"]}
check("the most recent submitted score, rounded like the screen", by_name["Ada"]["Quiz 6"], 83.3)
check("blank where nothing was handed in", by_name["Ada"]["Quiz 6 (2)"], None)
check("every student on the roster appears, even with no attempts", by_name["Cy"]["Quiz 6"], None)
check("with their class", (by_name["Ada"]["Class"], by_name["Cy"]["Class"]), ("Section 501", ""))
check("sorted by class, students without one last", [row["Student"] for row in book["rows"]], ["Ada", "Bo", "Cy"])

print()
print("== sorting a roster into classes in bulk ==")

import db  # noqa: E402  (the pure tests above need no database)
import repository as R  # noqa: E402

supabase = db.client()
tag = uuid.uuid4().hex[:8]
teacher = db.get_or_create_user(f"zztest-teacher-{tag}@mcq.local", f"ZZTEST Teacher {tag}", "teacher")
other_teacher = db.get_or_create_user(f"zztest-other-{tag}@mcq.local", f"ZZTEST Other {tag}", "teacher")
made_emails = [f"zztest-s{n}-{tag}@mcq.local" for n in range(6)]
try:
    first = R.create_class(teacher["id"], "ZZTEST Section 501")
    second = R.create_class(teacher["id"], "ZZTEST Section 502")
    try:
        R.create_class(teacher["id"], "zztest section 501")
        duplicate = None
    except ValueError as exc:
        duplicate = str(exc)
    check("two classes cannot share a name", duplicate, R.CLASS_NAME_TAKEN)

    report = R.enrol_by_email(teacher["id"], [(email, f"ZZTEST S{n}") for n, email in enumerate(made_emails[:4])], first)
    check("a pasted list joins the roster in one go", len(report["added"]), 4)
    check("and lands in the chosen class", R.team_student_ids(first),
          {row["id"] for row in R.students(teacher["id"]) if row["email"] in made_emails[:4]})
    again = R.enrol_by_email(teacher["id"], [(made_emails[0], "")], first)
    check("pasting the same list again adds nobody twice", (again["added"], again["already"]), ([], [made_emails[0]]))
    refused = R.enrol_by_email(teacher["id"], [(other_teacher["email"], ""), ("not-an-email", "")], first)
    check("a teacher's address is refused, and so is a non-address",
          (refused["not_students"], refused["invalid"]), ([other_teacher["email"]], ["not-an-email"]))

    ids = sorted(R.team_student_ids(first))
    moved = R.move_students_to_class(teacher["id"], ids[:2], second)
    check("moving many students is one call", moved, 2)
    check("they are in the new class", R.team_student_ids(second), set(ids[:2]))
    check("and out of the old one -- one class at a time", R.team_student_ids(first), set(ids[2:]))
    memberships = R.class_memberships(teacher["id"])
    check("one query tells every student's class", {student: classes for student, classes in memberships.items()},
          {ids[0]: [second], ids[1]: [second], ids[2]: [first], ids[3]: [first]})
    check("moving them again finds nothing to do", (R.move_students_to_class(teacher["id"], ids[:2], second),
                                                    R.team_student_ids(second)), (2, set(ids[:2])))
    R.move_students_to_class(teacher["id"], [ids[3]], None)
    check("no class takes a student out of all of them", ids[3] in R.class_memberships(teacher["id"]), False)

    stranger = db.get_or_create_user(made_emails[5], "ZZTEST stranger", "student")
    check("a student not on this roster is never touched",
          R.move_students_to_class(teacher["id"], [stranger["id"]], first), 0)

    R.rename_class(teacher["id"], second, "ZZTEST Section 503")
    check("a class can be renamed", [row["name"] for row in R.teams_for_teacher(teacher["id"])],
          ["ZZTEST Section 501", "ZZTEST Section 503"])
    R.delete_class(teacher["id"], second)
    check("deleting a class keeps its students on the roster",
          {row["id"] for row in R.students(teacher["id"])} >= set(ids[:2]), True)

    book = R.gradebook(teacher["id"])
    # Ids were handed out in creation order, so ids[n] is made_emails[n]: the
    # first two went to 503 (now deleted), the fourth was taken out of every class.
    check("the gradebook lists every rostered student with their class",
          sorted((row["Email"], row["Class"]) for row in book["rows"]),
          sorted([(made_emails[0], ""), (made_emails[1], ""), (made_emails[2], "ZZTEST Section 501"),
                  (made_emails[3], "")]))
finally:
    users = supabase.table("users").select("id").like("email", f"zztest-%-{tag}@mcq.local").execute().data
    user_ids = [row["id"] for row in users]
    supabase.table("teams").delete().eq("teacher_id", teacher["id"]).execute()
    if user_ids:
        supabase.table("teacher_students").delete().in_("teacher_id", user_ids).execute()
        supabase.table("teacher_students").delete().in_("student_id", user_ids).execute()
        supabase.table("users").delete().in_("id", user_ids).execute()
    leftovers = supabase.table("users").select("id").like("email", f"zztest-%-{tag}@mcq.local").execute().data
    check("every ZZTEST row is gone", leftovers, [])

print()
if FAILURES:
    print(f"{len(FAILURES)} FAILURE(S): {FAILURES}")
    raise SystemExit(1)
print("All class tests passed.")
