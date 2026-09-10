"""Answer specifications and grading for typed (short answer / fill in the blank) questions.

A typed question stores its whole answer specification as JSON in
`questions.correct_label`. Older rows kept the answer as the question's single
option (`options_json = [["A", "Paris"]]`) with `correct_label = "A"`;
`answer_spec()` reads both shapes so nothing needs migrating.

Number answers are graded with a tolerance derived from how precisely the
teacher wrote the answer:

    teacher wrote "6"      -> exact value: 6, 6.0, +6 and 06 pass; 6.5 and 7 fail
    teacher wrote "33.33"  -> anything within +/-0.005, so 33.3333 passes and 33.3 fails

which is the same rule as "round the student's answer to the number of decimal
places the teacher used". A teacher can override the tolerance per question.
"""

from __future__ import annotations

import json
import re
import unicodedata
from decimal import Decimal, InvalidOperation

TEXT = "text"
NUMBER = "number"

TEXT_QUESTION_TYPES = {"Fill in the blank", "Short answer"}

# Enough room for a long word or a short phrase without inviting an essay.
DEFAULT_TEXT_LIMIT = 60
MAX_TEXT_LIMIT = 200


def build_spec(
    value: str,
    answer_format: str = TEXT,
    tolerance: str | None = None,
    max_length: int | None = None,
    alternatives: list[str] | None = None,
    allow_typos: bool = False,
) -> dict:
    """Build the JSON answer specification stored in `correct_label`."""
    spec = {
        "value": (value or "").strip(),
        "format": NUMBER if answer_format == NUMBER else TEXT,
        "alternatives": [item.strip() for item in (alternatives or []) if item.strip()],
        "allow_typos": bool(allow_typos),
    }
    if tolerance not in (None, ""):
        spec["tolerance"] = str(tolerance).strip()
    if max_length:
        spec["max_length"] = int(max_length)
    return spec


def answer_spec(question: dict) -> dict:
    """Return the answer specification for a typed question, whatever shape it is stored in."""
    raw = question.get("correct_label")
    if isinstance(raw, dict):
        return {**build_spec(""), **raw}
    if isinstance(raw, str) and raw.strip().startswith("{"):
        try:
            stored = json.loads(raw)
        except ValueError:
            stored = None
        if isinstance(stored, dict) and "value" in stored:
            return {**build_spec(""), **stored}
    # Legacy row: the answer lived in the question's single option.
    options = question.get("options_json")
    if isinstance(options, str):
        try:
            options = json.loads(options)
        except ValueError:
            options = []
    options = options or []
    legacy_value = options[0][1] if options and len(options[0]) > 1 else ""
    return build_spec(legacy_value, NUMBER if _to_number(legacy_value) is not None else TEXT)


# --------------------------------------------------------------------------- numbers


_FRACTION = re.compile(r"^([+-]?\d+(?:\.\d+)?)\s*/\s*(\d+(?:\.\d+)?)$")


def _clean_number_text(text: str) -> str:
    text = unicodedata.normalize("NFKC", str(text or "")).strip()
    text = text.replace("−", "-").replace("–", "-")  # minus sign, en dash
    text = re.sub(r"(?<=\d),(?=\d{3}\b)", "", text)  # 1,234 -> 1234
    return text.replace(" ", "")


def _to_number(text: str) -> Decimal | None:
    """Parse a student's or teacher's numeric answer, allowing simple fractions."""
    cleaned = _clean_number_text(text)
    if not cleaned:
        return None
    fraction = _FRACTION.match(cleaned)
    if fraction:
        try:
            numerator, denominator = Decimal(fraction.group(1)), Decimal(fraction.group(2))
        except (InvalidOperation, ValueError):
            return None
        if denominator == 0 or not (numerator.is_finite() and denominator.is_finite()):
            return None
        try:
            return numerator / denominator
        except (InvalidOperation, ArithmeticError):
            return None
    try:
        parsed = Decimal(cleaned)
    except (InvalidOperation, ValueError):
        return None
    # Decimal happily parses "NaN" and "Infinity"; both blow up on arithmetic.
    return parsed if parsed.is_finite() else None


def decimals_written(value: str) -> int:
    """How many decimal places the teacher actually typed."""
    cleaned = _clean_number_text(value)
    if "/" in cleaned:
        return 0
    _, _, fraction = cleaned.partition(".")
    return len(fraction)


def tolerance_for(spec: dict) -> Decimal:
    """The +/- window a student's answer may fall in. Zero means an exact value match."""
    override = spec.get("tolerance")
    if override not in (None, ""):
        parsed = _to_number(override)
        if parsed is not None and parsed >= 0:
            return parsed
    places = decimals_written(spec.get("value", ""))
    if places == 0:
        return Decimal(0)
    return Decimal("0.5") * (Decimal(10) ** -places)


# --------------------------------------------------------------------------- text


_PUNCTUATION = re.compile(r"^[\s\"'(\[]+|[\s\"')\].,!?;:]+$")


def normalise_text(text: str) -> str:
    text = unicodedata.normalize("NFKC", str(text or "")).casefold()
    text = _PUNCTUATION.sub("", text)
    return re.sub(r"\s+", " ", text).strip()


def _edit_distance_within_one(left: str, right: str) -> bool:
    """True when one edit turns `left` into `right`.

    An edit is a single insert, delete, substitution, or a swap of two adjacent
    characters. Swaps count as one edit even though plain Levenshtein scores them
    as two: typing "Itlay" for "Italy" is the commonest slip there is, and a
    student who clearly knew the answer should not lose the mark for it.
    """
    if left == right:
        return True
    length_left, length_right = len(left), len(right)
    if abs(length_left - length_right) > 1:
        return False
    # Strip the matching head and tail; whatever disagrees is left in the middle.
    shortest = min(length_left, length_right)
    head = 0
    while head < shortest and left[head] == right[head]:
        head += 1
    tail = 0
    while tail < shortest - head and left[length_left - 1 - tail] == right[length_right - 1 - tail]:
        tail += 1
    middle_left = left[head:length_left - tail]
    middle_right = right[head:length_right - tail]
    if length_left == length_right:
        # One substitution leaves a single odd character; a swap leaves a reversed pair.
        return len(middle_left) <= 1 or (len(middle_left) == 2 and middle_left == middle_right[::-1])
    # One insert or delete: the shorter string's middle has to be empty.
    return not (middle_left if length_left < length_right else middle_right)


# --------------------------------------------------------------------------- grading


def grade(spec: dict, given) -> bool:
    """Mark one typed answer."""
    given = "" if given is None else str(given)
    if not given.strip():
        return False
    if spec.get("format") == NUMBER:
        student = _to_number(given)
        correct = _to_number(spec.get("value", ""))
        if student is None or correct is None:
            return False
        try:
            return abs(student - correct) <= tolerance_for(spec)
        except ArithmeticError:
            return False

    targets = [spec.get("value", ""), *spec.get("alternatives", [])]
    student = normalise_text(given)
    for target in targets:
        target = normalise_text(target)
        if not target:
            continue
        if student == target:
            return True
        if spec.get("allow_typos") and len(target) >= 4 and _edit_distance_within_one(student, target):
            return True
    return False


# --------------------------------------------------------------------------- presentation


def input_limit(spec: dict) -> int:
    """Character limit for the student's answer box."""
    stored = spec.get("max_length")
    if stored:
        return max(1, min(int(stored), MAX_TEXT_LIMIT))
    value = str(spec.get("value", ""))
    if spec.get("format") == NUMBER:
        # Room for a sign, the answer, and a couple of extra decimal places.
        return max(4, min(len(_clean_number_text(value)) + 3, 24))
    return max(len(value) + 10, DEFAULT_TEXT_LIMIT)


def student_hint(spec: dict) -> str:
    """A short note under the answer box telling the student what shape the answer takes."""
    if spec.get("format") != NUMBER:
        return f"Type your answer · up to {input_limit(spec)} characters"
    places = decimals_written(spec.get("value", ""))
    tolerance = tolerance_for(spec)
    if spec.get("tolerance") not in (None, ""):
        return f"Enter a number · accepted within ±{_trim(tolerance)}"
    if places == 0:
        return "Enter a whole number"
    unit = "decimal place" if places == 1 else "decimal places"
    return f"Enter a number · round to {places} {unit}"


def teacher_summary(spec: dict) -> str:
    """One line describing exactly what will be accepted, shown while building the quiz."""
    value = str(spec.get("value", "")).strip()
    if not value:
        return "Enter the correct answer above."
    if spec.get("format") == NUMBER:
        correct = _to_number(value)
        if correct is None:
            return "That isn't a number — switch the format to Text, or fix the answer."
        tolerance = tolerance_for(spec)
        if tolerance == 0:
            exact = _trim(correct)
            return f"Accepts exactly {exact} (also {exact}.0 and +{exact}). Rejects anything else."
        low, high = correct - tolerance, correct + tolerance
        return f"Accepts {_trim(low)} to {_trim(high)} (that is {_trim(correct)} ± {_trim(tolerance)})."
    extras = spec.get("alternatives") or []
    parts = [f'Accepts "{value}"']
    if extras:
        parts.append("or " + ", ".join(f'"{item}"' for item in extras))
    parts.append("— capitals and extra spaces are ignored")
    if spec.get("allow_typos"):
        parts.append("and a one-letter typo or a swapped pair is forgiven")
    return " ".join(parts) + "."


def _trim(number: Decimal) -> str:
    text = format(number, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


# --------------------------------------------------------------------------- scoring an attempt

SELECT_ALL_TYPE = "Multiple choice - select all that apply"


def is_correct(question: dict, given) -> bool:
    """Mark one answer against a frozen attempt question.

    `question` is an entry from an attempt's `answers_json` payload: it carries
    the question type and the `correct` value that applied when the attempt was
    built (a label, a list of labels, or a typed answer specification).
    """
    question_type = question.get("question_type")
    if question_type == SELECT_ALL_TYPE:
        correct = question.get("correct") or []
        return bool(correct) and set(given or []) == set(correct)
    if question_type in TEXT_QUESTION_TYPES:
        correct = question.get("correct")
        spec = correct if isinstance(correct, dict) else build_spec(str(correct or ""))
        return grade(spec, given)
    return given is not None and given == question.get("correct")


def score_payload(payload: dict) -> float:
    """Percentage score for an attempt payload, 0 when it holds no questions."""
    questions = payload.get("questions") or []
    if not questions:
        return 0.0
    answers = payload.get("answers") or {}
    correct = sum(is_correct(question, answers.get(str(index))) for index, question in enumerate(questions))
    return correct / len(questions) * 100
