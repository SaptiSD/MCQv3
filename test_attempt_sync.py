"""Regressions for the quiz-drift bugs. Run with: python test_attempt_sync.py

An attempt freezes the questions it was built from, and a tester walked straight
into every consequence of that: questions added and removed mid-attempt that the
student never saw, an answer key corrected after the student started that never
reached their marking, and a second browser tab quietly overwriting the first.
Each case below is one of those, reduced to the pure part -- `attempt_sync` in
and out, no database.

Numbers refer to the bug report (MCQ-BUG-011 .. 018).
"""

import json

import attempt_sync as sync
import grading
import ui


results = []


def check(name, condition):
    results.append((name, bool(condition)))
    print(("  pass " if condition else "  FAIL ") + name)


def row(position, question_text, options, correct, question_type="Multiple choice"):
    """One question as the database stores it."""
    return {"id": position + 1, "quiz_id": 1, "position": position,
            "question_text": question_text, "options_json": json.dumps(options),
            "correct_label": correct, "question_type": question_type}


CAPITALS = row(0, "Capital of France?", [["A", "Paris"], ["B", "Rome"], ["C", "Madrid"]], "A")
SUMS = row(1, "What is 2 + 2?", [["A", "4"], ["B", "5"]], "A")
COLOURS = row(2, "Which is a primary colour?", [["A", "Green"], ["B", "Red"]], "B")


def attempt(rows, answers=None, **freeze_kwargs):
    """A frozen attempt payload over `rows`, as `start_attempt` would build it."""
    return {"questions": sync.freeze_all(rows, **freeze_kwargs), "answers": dict(answers or {}), "revision": 0}


print("== an untouched quiz is not mistaken for a changed one ==")
paper = attempt([CAPITALS, SUMS, COLOURS], randomize_questions=True, randomize_answers=True)
check("a shuffled paper matches the bank it came from",
      sync.payload_fingerprint(paper) == sync.bank_fingerprint([CAPITALS, SUMS, COLOURS]))
check("and still matches when the bank is read back in another order",
      sync.payload_fingerprint(paper) == sync.bank_fingerprint([COLOURS, CAPITALS, SUMS]))


print("== correcting an answer key is not a visible change ==")
# It is a change to the quiz, so grading has to notice. It is not a change to
# the student's screen, so it must not interrupt them mid-question.
CAPITALS_REKEYED = row(0, "Capital of France?", [["A", "Paris"], ["B", "Rome"], ["C", "Madrid"]], "C")
paper = attempt([CAPITALS, SUMS])
check("the quiz is seen to have changed",
      sync.payload_fingerprint(paper) != sync.bank_fingerprint([CAPITALS_REKEYED, SUMS]))
check("but the paper the student is looking at has not",
      sync.payload_fingerprint(paper, include_key=False)
      == sync.bank_fingerprint([CAPITALS_REKEYED, SUMS], include_key=False))
check("whereas rewriting an option does show up",
      sync.payload_fingerprint(paper, include_key=False)
      != sync.bank_fingerprint([row(0, "Capital of France?", [["A", "Lyon"], ["B", "Rome"], ["C", "Madrid"]], "A"), SUMS],
                               include_key=False))
check("and so does adding a question",
      sync.payload_fingerprint(paper, include_key=False)
      != sync.bank_fingerprint([CAPITALS, SUMS, COLOURS], include_key=False))

TYPED_2DP = row(0, "Give pi to two places", [], json.dumps(grading.build_spec("3.14", "number")), "Short answer")
TYPED_3DP = row(0, "Give pi to two places", [], json.dumps(grading.build_spec("3.142", "number")), "Short answer")
typed_paper = attempt([TYPED_2DP])
check("a typed answer whose hint changes counts as visible",
      sync.payload_fingerprint(typed_paper, include_key=False)
      != sync.bank_fingerprint([TYPED_3DP], include_key=False))
TYPED_ALT = row(0, "Give pi to two places", [],
                json.dumps(grading.build_spec("3.14", "number", tolerance=None, alternatives=None)), "Short answer")
check("but widening what it accepts, without changing the hint, does not",
      sync.payload_fingerprint(typed_paper, include_key=False)
      == sync.bank_fingerprint([TYPED_ALT], include_key=False))


print()
print("== MCQ-BUG-011: a question added mid-attempt ==")
EXTRA = row(3, "Largest ocean?", [["A", "Pacific"], ["B", "Atlantic"]], "A")
started = attempt([CAPITALS, SUMS], answers={"0": "A", "1": "B"})
check("the added question makes the paper out of date",
      sync.payload_fingerprint(started) != sync.bank_fingerprint([CAPITALS, SUMS, EXTRA]))
caught_up, summary = sync.resync(started, [CAPITALS, SUMS, EXTRA])
check("syncing picks the new question up", len(caught_up["questions"]) == 3)
check("it is reported as one addition", summary["added"] == 1 and summary["removed"] == 0)
check("the new question goes on the end, not in the middle",
      caught_up["questions"][2]["text"] == "Largest ocean?")
check("the questions already answered keep their places",
      [q["text"] for q in caught_up["questions"][:2]] == ["Capital of France?", "What is 2 + 2?"])
check("and both answers survive", caught_up["answers"] == {"0": "A", "1": "B"})
check("the synced paper now matches the bank",
      sync.payload_fingerprint(caught_up) == sync.bank_fingerprint([CAPITALS, SUMS, EXTRA]))


print()
print("== MCQ-BUG-013: a question removed mid-attempt ==")
started = attempt([CAPITALS, SUMS, COLOURS], answers={"0": "A", "1": "A", "2": "B"})
check("the removal makes the paper out of date",
      sync.payload_fingerprint(started) != sync.bank_fingerprint([CAPITALS, COLOURS]))
caught_up, summary = sync.resync(started, [CAPITALS, COLOURS])
check("syncing drops the removed question", len(caught_up["questions"]) == 2)
check("it is reported as one removal", summary["removed"] == 1 and summary["added"] == 0)
check("the answers to the surviving questions shift down with them",
      caught_up["answers"] == {"0": "A", "1": "B"})
check("and land on the right questions",
      [q["text"] for q in caught_up["questions"]] == ["Capital of France?", "Which is a primary colour?"])


print()
print("== MCQ-BUG-012 / 016: the answer key moves after the student started ==")
# The teacher published with A correct, the student picked A, the teacher then
# decided B was correct. Before this fix the frozen key still said A.
CAPITALS_FIXED = row(0, "Capital of France?", [["A", "Paris"], ["B", "Rome"], ["C", "Madrid"]], "B")
paper = attempt([CAPITALS], answers={"0": "A"})
check("the student is marked correct under the original key",
      grading.score_payload(paper) == 100)
unmatched = sync.refresh_answer_key(paper, [CAPITALS_FIXED])
check("refreshing the key matches every question", unmatched == [])
check("and the same answers are now marked against it", grading.score_payload(paper) == 0)
check("the questions the student saw are untouched", len(paper["questions"]) == 1)

# The other direction: the tester's 0% case, where the teacher moved the key
# onto the answers the student had already saved.
paper = attempt([CAPITALS], answers={"0": "B"})
check("an answer that was wrong scores zero to begin with", grading.score_payload(paper) == 0)
sync.refresh_answer_key(paper, [CAPITALS_FIXED])
check("and full marks once the teacher makes it the right one", grading.score_payload(paper) == 100)


print()
print("== a rewritten option is re-keyed by its text, not its letter ==")
# The teacher rewrote the slots: what used to be B ("Rome") is now A, and the
# key points at A. Copying the letter across would mark "Paris" correct.
REORDERED = row(0, "Capital of France?", [["A", "Rome"], ["B", "Paris"], ["C", "Madrid"]], "A")
paper = attempt([CAPITALS], answers={"0": "A"})   # the student picked Paris
sync.refresh_answer_key(paper, [REORDERED])
check("the key follows the option text", paper["questions"][0]["correct"] == "B")
check("so a student who picked Paris is now wrong", grading.score_payload(paper) == 0)

paper = attempt([CAPITALS], answers={"0": "B"})   # the student picked Rome
sync.refresh_answer_key(paper, [REORDERED])
check("and one who picked Rome is right", grading.score_payload(paper) == 100)


print()
print("== a reworded or deleted question keeps the key it was set under ==")
REWORDED = row(0, "Which city is the capital of France?", [["A", "Paris"], ["B", "Rome"]], "B")
paper = attempt([CAPITALS], answers={"0": "A"})
unmatched = sync.refresh_answer_key(paper, [REWORDED])
check("the rewrite is reported as unmatched", unmatched == ["Capital of France?"])
check("the original key is left alone", paper["questions"][0]["correct"] == "A")
check("so the student keeps the mark they earned", grading.score_payload(paper) == 100)

paper = attempt([CAPITALS, SUMS], answers={"0": "A", "1": "A"})
unmatched = sync.refresh_answer_key(paper, [SUMS])
check("a deleted question is reported once", unmatched == ["Capital of France?"])
check("and the question that remains is still marked", grading.score_payload(paper) == 100)


print()
print("== MCQ-BUG-015: resuming onto a quiz that has moved on ==")
# Saved two answers, left, teacher added a question and corrected another.
SUMS_FIXED = row(1, "What is 2 + 2?", [["A", "4"], ["B", "5"]], "B")
saved = attempt([CAPITALS, SUMS], answers={"0": "A", "1": "A"})
caught_up, summary = sync.resync(saved, [CAPITALS, SUMS_FIXED, EXTRA])
check("the resumed paper has the new question", len(caught_up["questions"]) == 3)
check("the corrected question is reported as changed", summary["changed"] == 1)
check("the saved answers are kept", caught_up["answers"] == {"0": "A", "1": "A"})
check("and are marked against the corrected key", grading.score_payload(caught_up) == round(100 / 3, 10) or
      abs(grading.score_payload(caught_up) - 100 / 3) < 1e-9)


print()
print("== a student's answers survive the options being shuffled under them ==")
student_saw = {"text": "Capital of France?", "question_type": "Multiple choice",
               "options": [["B", "Rome"], ["A", "Paris"], ["C", "Madrid"]], "correct": "A"}
paper = {"questions": [student_saw], "answers": {"0": "A"}, "revision": 3}
caught_up, _ = sync.resync(paper, [CAPITALS, EXTRA], randomize_answers=True)
check("the answer still points at Paris",
      dict((label, option) for label, option in caught_up["questions"][0]["options"])[caught_up["answers"]["0"]] == "Paris")
check("and the options keep the order the student was looking at",
      [option for _, option in caught_up["questions"][0]["options"]] == ["Rome", "Paris", "Madrid"])


print()
print("== an answer whose option is gone is cleared, not left pointing elsewhere ==")
TRIMMED = row(0, "Capital of France?", [["A", "Paris"], ["B", "Rome"]], "A")
paper = attempt([CAPITALS], answers={"0": "C"})   # the student chose Madrid
caught_up, summary = sync.resync(paper, [TRIMMED])
check("the answer is dropped", caught_up["answers"] == {})
check("and reported", summary["dropped"] == 1 and summary["kept"] == 0)
check("the student is told", "cleared" in sync.describe(summary))


print()
print("== select-all and typed questions travel too ==")
SELECT_ALL = row(0, "Which are primary colours?",
                 [["A", "Red"], ["B", "Green"], ["C", "Blue"], ["D", "Purple"]],
                 json.dumps(["A", "C"]), sync.SELECT_ALL_TYPE)
SELECT_ALL_MOVED = row(0, "Which are primary colours?",
                       [["A", "Purple"], ["B", "Red"], ["C", "Green"], ["D", "Blue"]],
                       json.dumps(["B", "D"]), sync.SELECT_ALL_TYPE)
paper = attempt([SELECT_ALL], answers={"0": ["A", "C"]})
check("select-all starts correct", grading.score_payload(paper) == 100)
caught_up, _ = sync.resync(paper, [SELECT_ALL_MOVED])
check("the student's picks follow their text", sorted(caught_up["answers"]["0"]) == ["B", "D"])
check("and are still correct under the new letters", grading.score_payload(caught_up) == 100)

TYPED = row(0, "Capital of France?", [], json.dumps(grading.build_spec("Paris")), "Short answer")
TYPED_WIDER = row(0, "Capital of France?", [],
                  json.dumps(grading.build_spec("Paris", alternatives=["Lutetia"])), "Short answer")
paper = attempt([TYPED], answers={"0": "Lutetia"})
check("a typed answer outside the key is wrong", grading.score_payload(paper) == 0)
check("widening the key is seen as a change",
      sync.payload_fingerprint(paper) != sync.bank_fingerprint([TYPED_WIDER]))
sync.refresh_answer_key(paper, [TYPED_WIDER])
check("and the same answer is accepted afterwards", grading.score_payload(paper) == 100)
check("the answer specification stays on the server as a spec",
      isinstance(paper["questions"][0]["correct"], dict))


print()
print("== MCQ-BUG-014: two tabs on one attempt ==")
# Tab 1 saved the right answers; tab 2 has been sitting open since before that.
# The revision is what tab 2 is recognised by, so it can be repainted from what
# is stored instead of writing its own stale screen back over the top.
stored = attempt([CAPITALS, SUMS], answers={"0": "A", "1": "A"})
stored["revision"] = 5
check("a tab that has just opened the attempt is not behind",
      sync.overtaken(stored, None) is False)
check("a tab holding the current revision is not behind",
      sync.overtaken(stored, 5) is False)
check("a tab that last read an older one is",
      sync.overtaken(stored, 4) is True)
check("and a tab from before revisions existed is too",
      sync.overtaken(stored, 0) is True)
check("an attempt with no revision yet reads as zero",
      sync.revision({"answers": {}}) == 0)
check("as does one with a revision that isn't a number",
      sync.revision({"revision": "nonsense"}) == 0)
check("which leaves a fresh tab on it unbehind",
      sync.overtaken({"revision": "nonsense"}, 0) is False)


print("== the paper is fixed once a student starts; the key is not ==")
# This is the rule `repository.save_question_bank` enforces. The decision is
# whether the *visible* fingerprint moved, so it can be checked without a
# database: an unchanged fingerprint means the save is allowed through.
LIVE = [CAPITALS, SUMS]


def allowed(proposed):
    """Would `save_question_bank` let this through once a student has started?"""
    return (sync.bank_fingerprint(proposed, include_key=False)
            == sync.bank_fingerprint(LIVE, include_key=False))


check("correcting an answer key is allowed",
      allowed([row(0, "Capital of France?", [["A", "Paris"], ["B", "Rome"], ["C", "Madrid"]], "C"), SUMS]))
check("re-ordering the questions is allowed", allowed([SUMS, CAPITALS]))
check("adding a question is refused", not allowed([CAPITALS, SUMS, COLOURS]))
check("removing a question is refused", not allowed([CAPITALS]))
check("rewording a question is refused",
      not allowed([row(0, "Which city is the capital of France?", [["A", "Paris"], ["B", "Rome"], ["C", "Madrid"]], "A"), SUMS]))
check("rewriting an option is refused",
      not allowed([row(0, "Capital of France?", [["A", "Lyon"], ["B", "Rome"], ["C", "Madrid"]], "A"), SUMS]))
check("dropping an option is refused",
      not allowed([row(0, "Capital of France?", [["A", "Paris"], ["B", "Rome"]], "A"), SUMS]))
check("widening what a typed answer accepts is allowed",
      sync.bank_fingerprint([TYPED_WIDER], include_key=False) == sync.bank_fingerprint([TYPED], include_key=False))


print()
print("== MCQ-BUG-019: two questions that read the same, keyed differently ==")
# A quiz may legitimately ask the same thing twice and want a different answer
# each time. Matching the frozen paper back to the bank by wording marked both
# of them from the first one's key, and a student who answered A twice scored
# 100% on a paper where only one A was right.
SAME_A = row(0, "Capital of France?", [["A", "Paris"], ["B", "Rome"]], "A")
SAME_B = row(1, "Capital of France?", [["A", "Paris"], ["B", "Rome"]], "B")
twice = [SAME_A, SAME_B]

paper = attempt(twice, randomize_questions=True, randomize_answers=True)
for index in range(len(paper["questions"])):
    paper["answers"][str(index)] = "A"
check("the frozen paper marks them apart to begin with", grading.score_payload(paper) == 50)
check("each frozen question remembers where it came from",
      sorted(q["position"] for q in paper["questions"]) == [0, 1])
unmatched = sync.refresh_answer_key(paper, twice)
check("refreshing matches both", unmatched == [])
check("and still marks them apart", grading.score_payload(paper) == 50)
check("the second question keeps its own key",
      [q["correct"] for q in sorted(paper["questions"], key=lambda q: q["position"])] == ["A", "B"])

# Correcting one of the pair has to land on that one alone.
FIXED_B = row(1, "Capital of France?", [["A", "Paris"], ["B", "Rome"]], "A")
paper = attempt(twice)
paper["answers"] = {"0": "A", "1": "A"}
sync.refresh_answer_key(paper, [SAME_A, FIXED_B])
check("a correction to one twin reaches only that twin", grading.score_payload(paper) == 100)

# An attempt frozen before positions were recorded cannot tell them apart, so it
# must leave both keys alone rather than guess.
legacy = {"questions": [{k: v for k, v in q.items() if k != "position"}
                        for q in sync.freeze_all(twice)],
          "answers": {"0": "A", "1": "A"}, "revision": 0}
check("a legacy attempt starts correctly marked", grading.score_payload(legacy) == 50)
unmatched = sync.refresh_answer_key(legacy, twice)
check("it refuses to guess between them", len(unmatched) == 2)
check("so its marking is left as it was", grading.score_payload(legacy) == 50)

# Wording that is unique still matches without a position to go on.
lone = {"questions": [{k: v for k, v in q.items() if k != "position"}
                      for q in sync.freeze_all([CAPITALS])],
        "answers": {"0": "A"}, "revision": 0}
sync.refresh_answer_key(lone, [CAPITALS_REKEYED])
check("a legacy attempt still follows a unique question's key", grading.score_payload(lone) == 0)

# resync keeps the pair apart too.
caught_up, summary = sync.resync(attempt(twice, answers={"0": "A", "1": "B"}), twice)
check("resync pairs the twins by position, not wording", summary["added"] == 0 and summary["removed"] == 0)
check("and carries each answer to its own question", caught_up["answers"] == {"0": "A", "1": "B"})
check("both keys survive resync",
      [q["correct"] for q in caught_up["questions"]] == ["A", "B"])


print()
print("== MCQ-BUG-025: which questions are still blank ==")
# The student portal builds this list; an empty string and an empty list are
# both "not answered", and a legitimate "0" or "False" is not.
def blanks(answers, count):
    return [index + 1 for index in range(count) if answers.get(str(index)) in (None, "", [])]


check("nothing answered is all of them", blanks({}, 3) == [1, 2, 3])
check("a gap in the middle is found", blanks({"0": "A", "2": "B"}, 3) == [2])
check("an empty typed answer counts as blank", blanks({"0": ""}, 1) == [1])
check("an empty select-all counts as blank", blanks({"0": []}, 1) == [1])
check("a full paper has none", blanks({"0": "A", "1": "B"}, 2) == [])
check("the answer \"0\" is an answer", blanks({"0": "0"}, 1) == [])


print()
print("== MCQ-BUG-017: angle brackets survive to the screen ==")
title = "HTML Basics: Understanding <div> and <p> Tags"
rendered = ui.text(title)
check("the tags are escaped rather than swallowed",
      "&lt;div&gt;" in rendered and "&lt;p&gt;" in rendered)
check("nothing is lost from the title", "Understanding" in rendered and "Tags" in rendered)
check("a script tag cannot escape into the page",
      "<script>" not in ui.text("<script>alert(1)</script>"))
check("a badge escapes its label too", "<b>" not in ui.pill("<b>Draft</b>"))
check("but keeps its own markup", ui.pill("Draft", "green").startswith("<span class="))


print()
failed = [name for name, ok in results if not ok]
print(f"{len(results) - len(failed)} passed, {len(failed)} failed.")
if failed:
    raise SystemExit("FAILED: " + ", ".join(failed))
print("All attempt-sync tests passed.")
