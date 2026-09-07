"""The upload -> review table -> save path. Run with: python test_question_bank.py

Covers parsing an uploaded bank, the table the teacher reviews, and rebuilding
questions from it after they change a row's Type.
"""

import pandas as pd

import grading as g
import teacher_portal as TP
from ingestion import extract_upload, parse_bank

FAILURES = []


def show(text):
    """The Windows console is cp1252; keep the report readable regardless."""
    print(str(text).encode("ascii", "backslashreplace").decode("ascii"))


def check(label, actual, expected):
    ok = actual == expected
    if not ok:
        FAILURES.append(label)
    show(("  pass " if ok else "  FAIL ") + label + ("" if ok else f"  expected {expected!r} got {actual!r}"))


class FakeUpload:
    """Stands in for Streamlit's UploadedFile."""

    def __init__(self, name, data):
        self.name, self._data = name, data

    def getvalue(self):
        return self._data


BANK = "\n".join([
    "1. What does RAM stand for?",
    "A. Random Access Memory",
    "B. Rapid Access Module",
    "C. Readable Active Memory",
    "D. Remote Array Mount",
    "",
    "2. Which language runs in a browser?",
    "A. Python",
    "B. JavaScript",
    "C. Ruby",
    "D. Go",
    "",
    "Answer Key: 1: A, 2: B",
]).encode("utf-8")


print("\n== parsing the uploaded .txt ==")
parsed = parse_bank(extract_upload(FakeUpload("bank.txt", BANK)))
check("two questions found", len(parsed), 2)
check("first question text", parsed[0]["question_text"], "What does RAM stand for?")
check("first answer", parsed[0]["correct_label"], "A")
check("four options", len(parsed[0]["options"]), 4)

print("\n== the review table the teacher sees ==")
table = pd.DataFrame([
    {"Question": q["question_text"], "Type": q.get("question_type", "Multiple choice"),
     "Options": " | ".join(f"{a}) {b}" for a, b in q["options"]), "Correct": q["correct_label"]}
    for q in parsed
])
check("Type column present", "Type" in table.columns, True)
check("Type defaults to Multiple choice", list(table["Type"]), ["Multiple choice", "Multiple choice"])

print("\n== saving it back unchanged ==")
out = TP._questions_from_table(table)
check("two questions", len(out), 2)
check("type preserved", out[0]["question_type"], "Multiple choice")
check("options rebuilt", out[0]["options"], [("A", "Random Access Memory"), ("B", "Rapid Access Module"),
                                             ("C", "Readable Active Memory"), ("D", "Remote Array Mount")])
check("correct letter", out[0]["correct_label"], "A")
check("validates clean", TP._question_errors(out), [])

print("\n== the teacher changes a row's Type ==")
edited = table.copy()
edited.loc[0, "Type"] = "True / False"
edited.loc[0, "Correct"] = "B"
edited.loc[1, "Type"] = "Short answer"
edited.loc[1, "Correct"] = "JavaScript"
out = TP._questions_from_table(edited)
check("row 0 is now True/False", out[0]["question_type"], "True / False")
check("True/False gets fixed options", out[0]["options"], [("A", "True"), ("B", "False")])
check("True/False answer kept", out[0]["correct_label"], "B")
check("row 1 is now Short answer", out[1]["question_type"], "Short answer")
check("typed answer became a spec", isinstance(out[1]["correct_label"], dict), True)
check("typed answer value", out[1]["correct_label"]["value"], "JavaScript")
check("text answer detected as text", out[1]["correct_label"]["format"], g.TEXT)
check("typed question carries no options", out[1]["options"], [])
check("both validate", TP._question_errors(out), [])

print("\n== a numeric typed answer is detected automatically ==")
numeric = pd.DataFrame([{"Question": "What is 9 * 9?", "Type": "Short answer", "Options": "", "Correct": "81"}])
out = TP._questions_from_table(numeric)
check("format is number", out[0]["correct_label"]["format"], g.NUMBER)
check("grades 81", g.grade(out[0]["correct_label"], "81"), True)
check("grades 81.0", g.grade(out[0]["correct_label"], "81.0"), True)
check("rejects 80", g.grade(out[0]["correct_label"], "80"), False)

print("\n== select all that apply from the table ==")
select_all = pd.DataFrame([{"Question": "Pick the vowels", "Type": "Multiple choice - select all that apply",
                            "Options": "A) a | B) b | C) e | D) f", "Correct": "A, C"}])
out = TP._questions_from_table(select_all)
check("labels parsed", out[0]["correct_label"], ["A", "C"])
check("validates", TP._question_errors(out), [])

print("\n== bad rows are caught, not silently saved ==")
bad = pd.DataFrame([
    {"Question": "", "Type": "Multiple choice", "Options": "A) x | B) y", "Correct": "A"},
    {"Question": "Only one option", "Type": "Multiple choice", "Options": "A) x", "Correct": "A"},
    {"Question": "Answer not an option", "Type": "Multiple choice", "Options": "A) x | B) y", "Correct": "D"},
    {"Question": "Empty typed answer", "Type": "Short answer", "Options": "", "Correct": ""},
])
out = TP._questions_from_table(bad)
check("blank question row dropped", len(out), 3)
check("three problems reported", len(TP._question_errors(out)), 3)

print("\n== an unknown Type falls back rather than crashing ==")
weird = pd.DataFrame([{"Question": "Hmm", "Type": "Essay", "Options": "A) x | B) y", "Correct": "A"}])
check("falls back to Multiple choice", TP._questions_from_table(weird)[0]["question_type"], "Multiple choice")

print()
if FAILURES:
    print(f"{len(FAILURES)} FAILURE(S): {FAILURES}")
    raise SystemExit(1)
print("All question-bank tests passed.")
