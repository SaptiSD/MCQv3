"""Regressions for the editor-state and upload bugs. Run with: python test_editor_state.py

Every case here is one a tester actually hit: a removed question coming back,
an uploaded file quietly losing questions, a score disagreeing with its own
pass/fail badge.
"""

import io

import teacher_portal as TP
import ui
from ingestion import extract_upload, parse_report


results = []


def check(name, condition):
    results.append((name, bool(condition)))
    print(("  pass " if condition else "  FAIL ") + name)


class FakeUpload:
    """The two attributes `extract_upload` reads off a Streamlit file uploader."""

    def __init__(self, name, data):
        self.name = name
        self._data = data

    def getvalue(self):
        return self._data


print("== removing a question forgets it completely ==")
form = {
    "new-manual-count": 3,
    "new-type-0": "Multiple choice", "new-text-0": "Q1", "new-option-0-A": "keep",
    "new-correct-mc-0": "A",
    "new-type-1": "Multiple choice", "new-text-1": "Q2", "new-option-1-A": "gone",
    "new-correct-mc-1": "B", "new-correct-tf-1": "False", "new-correct-all-1": ["A"],
    "new-typed-1-answer": "42", "new-typed-1-format": "Number",
    "new-type-10": "Short answer", "new-text-10": "not this one",
}
TP._forget_question(form, 1)
check("the removed question's text is gone", "new-text-1" not in form)
check("its options are gone", "new-option-1-A" not in form)
check("both of its answer keys are gone",
      "new-correct-mc-1" not in form and "new-correct-tf-1" not in form)
check("its select-all answers are gone", "new-correct-all-1" not in form)
check("its typed-answer fields are gone",
      "new-typed-1-answer" not in form and "new-typed-1-format" not in form)
check("the question before it is untouched", form.get("new-text-0") == "Q1")
check("question 10 is not mistaken for question 1", form.get("new-text-10") == "not this one")
check("the count itself is left alone", form.get("new-manual-count") == 3)


print()
print("== a multiple-choice answer can't leak into True / False ==")
# The two dropdowns used to share one key, so switching type kept the letter.
mc_and_tf = {"new-correct-mc-0": "C", "new-correct-tf-0": "False"}
check("each question type keeps its own answer",
      mc_and_tf["new-correct-mc-0"] == "C" and mc_and_tf["new-correct-tf-0"] == "False")
source = open("teacher_portal.py", encoding="utf-8").read()
check("no widget is still keyed on the shared name",
      'key=f"new-correct-{index}"' not in source)
check("the publish step reads the per-type keys",
      'new-correct-mc-{index}' in source and 'new-correct-tf-{index}' in source)


print()
print("== duplicate option text is refused ==")
duplicate = [{
    "question_text": "Capital of France?",
    "options": [("A", "Paris"), ("B", " paris ")],
    "correct_label": "A",
    "question_type": "Multiple choice",
}]
errors = TP._question_errors(duplicate)
check("the same option twice is an error", any("more than once" in error for error in errors))
distinct = [{
    "question_text": "Capital of France?",
    "options": [("A", "Paris"), ("B", "Lyon")],
    "correct_label": "A",
    "question_type": "Multiple choice",
}]
check("two different options are fine", TP._question_errors(distinct) == [])


print()
print("== an uploaded bank says what it dropped ==")
bank = (
    "1. What is 1 + 1?\n"
    "A) 2\n"
    "B) 3\n"
    "2. What is 2 + 2?\n"
    "A) 4\n"
    "B) 5\n"
    "3. What is 3 + 3?\n"
    "A) 6\n"
    "B) 7\n"
    "Answer key:\n"
    "1: A\n"
    "2: E\n"
)
questions, skipped = parse_report(bank)
check("the good question is kept", [q["number"] for q in questions] == [1])
check("both bad questions are reported", len(skipped) == 2)
check("a missing answer key entry says so", any("answer key" in note for note in skipped))
check("an out-of-range answer letter says so", any("not one of its options" in note for note in skipped))


print()
print("== files real people upload still parse ==")
with_bom = extract_upload(FakeUpload("bank.txt", "﻿1. First question?\nA) yes\nB) no\nAnswer key:\n1: A\n".encode("utf-8")))
check("a byte-order mark doesn't eat question 1", parse_report(with_bom)[0][0]["number"] == 1)
tight = extract_upload(FakeUpload("bank.txt", b"1. Capital?\nA.Paris\nB.Lyon\nAnswer key:\n1: A\n"))
parsed, _ = parse_report(tight)
check("an option with no space after the dot is still an option",
      parsed and [label for label, _ in parsed[0]["options"]] == ["A", "B"])
utf16 = extract_upload(FakeUpload("bank.txt", "1. Wide?\nA) yes\nB) no\nAnswer key:\n1: A\n".encode("utf-16")))
check("a UTF-16 file reads as text", parse_report(utf16)[0][0]["question_text"] == "Wide?")


print()
print("== a score never contradicts its pass/fail badge ==")
check("two out of three reads as 66.7%, not 67%", ui.percent(200 / 3) == "66.7%")
check("a whole number stays whole", ui.percent(80.0) == "80%")
check("a full score has no decimal", ui.percent(100) == "100%")
check("a missing score is a dash", ui.percent(None) == "-")
check("the displayed value never rounds up past the pass mark",
      float(ui.percent(200 / 3).rstrip("%")) < 67)


print()
print("== a corrected answer key is rekeyed by option text, not by letter ==")
import repository

# The teacher fixed the question by swapping what sits in each slot, so the live
# key "A" now means Paris while the student's frozen copy still has A = London.
frozen = {"options": [("A", "London"), ("B", "Paris")], "correct": "A",
          "question_type": "Multiple choice"}
live = {"options_json": '[["A", "Paris"], ["B", "London"]]', "correct_label": "A",
        "question_type": "Multiple choice"}
check("the frozen label follows the option text",
      repository._rekeyed_choice(frozen, live, "A") == "B")

frozen_all = {"options": [("A", "One"), ("B", "Two"), ("C", "Three")],
              "question_type": "Multiple choice - select all that apply"}
live_all = {"options_json": '[["A", "Three"], ["B", "One"], ["C", "Two"]]',
            "question_type": "Multiple choice - select all that apply"}
check("select-all keys are remapped one by one",
      sorted(repository._rekeyed_choice(frozen_all, live_all, ["A", "B"])) == ["A", "C"])

gone = {"options": [("A", "London"), ("B", "Lyon")], "question_type": "Multiple choice"}
check("a correct option the student never saw is refused",
      repository._rekeyed_choice(gone, live, "A") is None)
check("an unreadable live options list is refused",
      repository._rekeyed_choice(frozen, {"options_json": "not json"}, "A") is None)

print()
print("== a draft can only be published once, from any tab ==")
import server_state

key, token = "publish-test@example.invalid", "token-1"
server_state.release_publish(key, token)
check("the first click wins the claim", server_state.claim_publish(key, token) == "claimed")
check("a second click while it runs is refused", server_state.claim_publish(key, token) == "in_flight")
server_state.finish_publish(key, token, 4242)
check("a replayed click sees it is already done", server_state.claim_publish(key, token) == "done")
check("and can find the quiz it made", server_state.publish_result(key, token) == 4242)
server_state.release_publish(key, token)
check("a failed publish hands the claim back", server_state.claim_publish(key, token) == "claimed")
check("a different draft is unaffected", server_state.claim_publish(key, "token-2") == "claimed")
server_state.release_publish(key, token)
server_state.release_publish(key, "token-2")


print()
failed = [name for name, ok in results if not ok]
print(f"{len(results) - len(failed)} passed, {len(failed)} failed.")
if failed:
    raise SystemExit("FAILED: " + ", ".join(failed))
print("All editor-state tests passed.")
