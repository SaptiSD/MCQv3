"""Grading rules for typed answers. Run with: python test_grading.py"""

import json

import grading as g

FAILURES = []


def show(text):
    """The Windows console is cp1252; keep the report readable regardless."""
    print(str(text).encode("ascii", "backslashreplace").decode("ascii"))


def check(label, actual, expected):
    if actual != expected:
        FAILURES.append(f"{label}: expected {expected}, got {actual}")
        show(f"  FAIL {label}: expected {expected}, got {actual}")
    else:
        show(f"  pass {label}")


def accepts(spec, *answers):
    for answer in answers:
        check(f'"{answer}" accepted', g.grade(spec, answer), True)


def rejects(spec, *answers):
    for answer in answers:
        check(f'"{answer}" rejected', g.grade(spec, answer), False)


print("\n== whole number: What is 2 * 3? -> 6 ==")
six = g.build_spec("6", g.NUMBER)
accepts(six, "6", "6.0", "6.00", " 6 ", "+6", "06", "12/2", "6.000000")
rejects(six, "7", "6.5", "5.999", "-6", "six", "", "  ", "60", "0.6")
check("hint", g.student_hint(six), "Enter a whole number")

print("\n== repeating decimal: What is 100 / 3? -> teacher writes 33.33 ==")
third = g.build_spec("33.33", g.NUMBER)
accepts(third, "33.33", "33.333", "33.3333", "33.33333333", "100/3", "33.335", "33.325")
rejects(third, "33.3", "33", "33.4", "34", "33.2", "3.333")
check("hint", g.student_hint(third), "Enter a number · round to 2 decimal places")

print("\n== one decimal place ==")
one_dp = g.build_spec("2.5", g.NUMBER)
accepts(one_dp, "2.5", "2.50", "2.54", "2.46", "5/2")
rejects(one_dp, "2", "3", "2.6", "2.4")

print("\n== teacher overrides the tolerance ==")
loose = g.build_spec("100", g.NUMBER, tolerance="5")
accepts(loose, "100", "95", "105", "97.5")
rejects(loose, "94", "106")

print("\n== negatives, large numbers, thousands separators ==")
negative = g.build_spec("-40", g.NUMBER)
accepts(negative, "-40", "-40.0", "−40")
rejects(negative, "40")
big = g.build_spec("1500000", g.NUMBER)
accepts(big, "1500000", "1,500,000")
rejects(big, "150000")

print("\n== rubbish input never crashes or passes ==")
rejects(six, "abc", "6a", "--6", "6/0", "1/2/3", "NaN", "Infinity", "1e400", None)
check("1e1 is 10, not 6", g.grade(six, "1e1"), False)
check("6e0 is 6", g.grade(six, "6e0"), True)

print("\n== text answers ==")
paris = g.build_spec("Paris", g.TEXT)
accepts(paris, "Paris", "paris", "  PARIS  ", "paris.", '"Paris"')
rejects(paris, "Pari", "London", "Paris France", "")
check("hint mentions a limit", "characters" in g.student_hint(paris), True)

print("\n== text alternatives ==")
usa = g.build_spec("United States", g.TEXT, alternatives=["USA", "US", "America"])
accepts(usa, "United States", "usa", "  America ", "US")
rejects(usa, "United Kingdom", "U")

print("\n== typo tolerance is opt-in ==")
strict = g.build_spec("Photosynthesis", g.TEXT)
rejects(strict, "Photosynthesus")
forgiving = g.build_spec("Photosynthesis", g.TEXT, allow_typos=True)
accepts(forgiving, "Photosynthesis", "Photosynthesus", "Photosynthesi", "Photosynthesiss")
rejects(forgiving, "Photo", "Respiration")
short = g.build_spec("cat", g.TEXT, allow_typos=True)
rejects(short, "bat", "cot")  # too short to guess at

# A swapped pair of adjacent letters is one slip, not two.
swaps = g.build_spec("Italy", g.TEXT, allow_typos=True)
accepts(swaps, "Itlay", "Itlay ", "itlay")
rejects(swaps, "Ilaty", "Spain")  # two letters out of place is a different word
accepts(g.build_spec("Photosynthesis", g.TEXT, allow_typos=True), "Photosynthseis")

print("\n== input limits ==")
check("whole number box is small", g.input_limit(six), 4)
check("two-digit answer box", g.input_limit(g.build_spec("42", g.NUMBER)), 5)
check("explicit limit wins", g.input_limit(g.build_spec("42", g.NUMBER, max_length=2)), 2)
check("text default", g.input_limit(paris), g.DEFAULT_TEXT_LIMIT)
check("limit is capped", g.input_limit(g.build_spec("x", g.TEXT, max_length=9999)), g.MAX_TEXT_LIMIT)

print("\n== teacher preview text ==")
check("exact preview", "exactly 6" in g.teacher_summary(six), True)
check("range preview", g.teacher_summary(third), "Accepts 33.325 to 33.335 (that is 33.33 ± 0.005).")
check("text preview", 'Accepts "Paris"' in g.teacher_summary(paris), True)
check("non-numeric warning", "isn't a number" in g.teacher_summary(g.build_spec("blue", g.NUMBER)), True)
check("empty prompt", "Enter the correct answer" in g.teacher_summary(g.build_spec("", g.NUMBER)), True)

print("\n== reading answers back out of the database ==")
stored = {"correct_label": json.dumps(six), "options_json": "[]", "question_type": "Short answer"}
check("json round trip", g.grade(g.answer_spec(stored), "6.0"), True)
legacy = {"correct_label": "A", "options_json": json.dumps([["A", "Paris"]]), "question_type": "Fill in the blank"}
check("legacy row still graded", g.grade(g.answer_spec(legacy), "paris"), True)
check("legacy row detected as text", g.answer_spec(legacy)["format"], g.TEXT)
legacy_number = {"correct_label": "A", "options_json": json.dumps([["A", "6"]]), "question_type": "Short answer"}
check("legacy number detected", g.answer_spec(legacy_number)["format"], g.NUMBER)
check("legacy number graded", g.grade(g.answer_spec(legacy_number), "6.0"), True)
empty = {"correct_label": "A", "options_json": "[]", "question_type": "Short answer"}
check("empty legacy row rejects everything", g.grade(g.answer_spec(empty), "anything"), False)

print()
if FAILURES:
    print(f"{len(FAILURES)} FAILURE(S)")
    raise SystemExit(1)
print("All grading tests passed.")
