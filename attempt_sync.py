"""Keeping a student's attempt in step with the quiz behind it.

An attempt freezes the questions it was built from into its `answers_json`
payload. That freeze earns its keep: it is what lets every student see a
differently shuffled paper, and it is what keeps a typed question's answer
specification off the wire. What it also does is drift, silently, the moment the
teacher edits the quiz — and a drifted attempt is invisible to everyone. The
student answers a question that no longer exists, or never sees one that was
added, and is marked against an answer key the teacher has since corrected.

This module is the bookkeeping that makes the drift visible and repairable:

* `bank_fingerprint()` / `payload_fingerprint()` reduce a question bank — live
  rows on one side, a frozen payload on the other — to the same short string, so
  "has this quiz changed under the attempt?" is a string comparison rather than
  a diff. Both ignore ordering, because a randomised attempt shuffles the
  questions *and* each question's options, and neither is a change.
* `resync()` rebuilds a payload from the current questions and carries the
  student's answers across, matching questions and options on their *text*. A
  teacher who reorders questions, reshuffles what sits in each A/B/C/D slot, or
  adds a question at the top does not cost the student their work.
* `refresh_answer_key()` leaves the questions exactly as the student saw them
  and re-reads only the key. That is what grading a submission and the teacher's
  regrade both need: the paper is history, the marking scheme is not.

Everything here is pure — rows in, dicts out, no database. `repository` does the
reading and writing, and `test_attempt_sync.py` covers the rules.
"""

from __future__ import annotations

import hashlib
import json
import random

import grading

SELECT_ALL_TYPE = grading.SELECT_ALL_TYPE
TEXT_ANSWER_TYPES = grading.TEXT_QUESTION_TYPES
DEFAULT_TYPE = "Multiple choice"

# The parts of a typed question's answer specification that change what is
# accepted or how the answer box behaves. Anything else in the spec is
# presentation the student cannot be marked on.
_SPEC_FIELDS = ("value", "format", "tolerance", "alternatives", "allow_typos", "max_length")


def _question_type(row_or_entry: dict) -> str:
    return row_or_entry.get("question_type") or DEFAULT_TYPE


def _options_from_row(row: dict) -> list[list]:
    """The `[label, text]` pairs stored on a live question row."""
    options = row.get("options_json")
    if isinstance(options, str):
        try:
            options = json.loads(options)
        except (TypeError, ValueError):
            return []
    return [[str(pair[0]), str(pair[1])] for pair in (options or [])
            if isinstance(pair, (list, tuple)) and len(pair) >= 2]


def live_correct(row: dict):
    """The answer key for a live question row, in the shape a frozen entry stores it."""
    question_type = _question_type(row)
    if question_type in TEXT_ANSWER_TYPES:
        return grading.answer_spec(row)
    if question_type == SELECT_ALL_TYPE:
        try:
            parsed = json.loads(row["correct_label"])
        except (TypeError, ValueError, KeyError):
            return []
        return parsed if isinstance(parsed, list) else []
    return row.get("correct_label")


def freeze(row: dict, randomize_answers: bool = False) -> dict:
    """Build the frozen copy of one question that an attempt payload stores.

    `position` is carried across because it is the only thing that tells two
    identically worded questions apart. A quiz may legitimately ask the same
    thing twice with different answers, and matching those back by wording marks
    both of them from the first one's key.
    """
    question_type = _question_type(row)
    correct = live_correct(row)
    position = row.get("position")
    anchor = {} if position is None else {"position": int(position)}
    if question_type in TEXT_ANSWER_TYPES:
        # The answer specification never leaves the server: the frozen copy
        # keeps only what the student's answer box needs to render.
        return {"text": row["question_text"], "options": [], "correct": correct,
                "question_type": question_type,
                "hint": grading.student_hint(correct), "limit": grading.input_limit(correct),
                **anchor}
    options = _options_from_row(row)
    # True/False keeps its natural order; shuffling it just reads oddly.
    if randomize_answers and question_type != "True / False":
        random.shuffle(options)
    return {"text": row["question_text"], "options": options,
            "correct": correct, "question_type": question_type, **anchor}


def freeze_all(rows, randomize_questions: bool = False, randomize_answers: bool = False) -> list[dict]:
    """The whole frozen paper one student sits."""
    rows = list(rows)
    if randomize_questions:
        random.shuffle(rows)
    return [freeze(row, randomize_answers) for row in rows]


# --------------------------------------------------------------------------- fingerprints


def _canonical(value) -> str:
    return json.dumps(value, sort_keys=True, default=str)


def _signature(text, question_type: str, options, correct, include_key: bool) -> str:
    """One question reduced to a string that ignores presentation order.

    Option *labels* are part of it and option *order* is not: a shuffle moves the
    pairs around but keeps each letter with its text, so two attempts at an
    unedited quiz agree while a teacher who swaps what B and C say does not.

    `include_key` is what separates the two questions worth asking of a paper.
    With it, "is this attempt still marking against the right answers?" — which
    is the whole quiz. Without it, "would the student notice?" — which is only
    what is on their screen, and is the one that decides whether to interrupt
    them. Correcting an answer key changes nothing they can see, so it should
    not stop them mid-question to announce itself; it is simply applied when
    their paper is marked.
    """
    if question_type in TEXT_ANSWER_TYPES:
        spec = correct if isinstance(correct, dict) else grading.build_spec(str(correct or ""))
        # The hint and the box width are derived from the answer, so a typed
        # question can look different without its key being part of this.
        body = _canonical({field: spec.get(field) for field in _SPEC_FIELDS} if include_key
                          else {"hint": grading.student_hint(spec), "limit": grading.input_limit(spec)})
    else:
        pairs = sorted((str(label), grading.normalise_text(option_text)) for label, option_text in options)
        key = sorted(str(item) for item in correct) if isinstance(correct, list) else [str(correct)]
        body = _canonical({"options": pairs, "correct": key} if include_key else {"options": pairs})
    return _canonical([grading.normalise_text(text), question_type, body])


def entry_signature(entry: dict, include_key: bool = True) -> str:
    """The signature of one question as an attempt froze it."""
    return _signature(entry.get("text", ""), _question_type(entry),
                      [(pair[0], pair[1]) for pair in (entry.get("options") or []) if len(pair) >= 2],
                      entry.get("correct"), include_key)


def row_signature(row: dict, include_key: bool = True) -> str:
    """The signature of one question as it stands in the question bank."""
    return _signature(row.get("question_text", ""), _question_type(row),
                      _options_from_row(row), live_correct(row), include_key)


def _digest(signatures) -> str:
    return hashlib.sha256("".join(sorted(signatures)).encode("utf-8")).hexdigest()[:32]


def bank_fingerprint(rows, include_key: bool = True) -> str:
    """A short, order-insensitive digest of a quiz's current questions."""
    return _digest(row_signature(row, include_key) for row in rows)


def payload_fingerprint(payload: dict, include_key: bool = True) -> str:
    """The same digest, computed from the questions an attempt froze.

    Deriving it rather than storing it means an attempt that predates this
    module is still comparable — there is no stamp to be missing.
    """
    return _digest(entry_signature(entry, include_key) for entry in (payload.get("questions") or []))


# --------------------------------------------------------------------------- re-keying


def rekey_choice(frozen: dict, live_row: dict, live_answer):
    """Express a live answer key in the labels the frozen attempt actually showed.

    A, B, C, D are fixed slots in the editor, so a teacher who corrects a
    question by rewriting what sits in each slot reuses the same letters for
    different text. Copying the live letter straight across would then mark a
    different option correct than the one the teacher chose. Matching on the
    option *text* survives that; `None` means this question can't be rekeyed
    safely and should keep the key it was graded under.
    """
    live_options = dict(_options_from_row(live_row))
    if not live_options:
        return None
    frozen_labels = {
        grading.normalise_text(text): label
        for label, text in (frozen.get("options") or [])
    }
    wanted = live_answer if isinstance(live_answer, list) else [live_answer]
    mapped = []
    for label in wanted:
        text = live_options.get(str(label))
        if text is None:
            return None
        frozen_label = frozen_labels.get(grading.normalise_text(text))
        if frozen_label is None:
            return None
        mapped.append(frozen_label)
    if isinstance(live_answer, list):
        return mapped
    return mapped[0] if mapped else None


def _text_key(text, question_type: str) -> tuple:
    return (grading.normalise_text(text), question_type)


def _live_lookup(rows) -> tuple[dict, dict]:
    """Index the live questions by position, and by wording.

    Position is the anchor: the paper is fixed as soon as a student starts, so a
    frozen question's position still names the row it came from. Wording is the
    fallback for attempts frozen before positions were recorded.
    """
    by_position: dict = {}
    by_text: dict = {}
    for row in rows:
        position = row.get("position")
        if position is not None:
            by_position.setdefault(int(position), row)
        by_text.setdefault(_text_key(row.get("question_text", ""), _question_type(row)), []).append(row)
    return by_position, by_text


def live_match(entry: dict, by_position: dict, by_text: dict):
    """The live row a frozen question came from, or `None` if it cannot be told.

    `None` is a deliberate answer rather than a failure. Two questions that read
    identically cannot be distinguished by their wording, and refreshing one from
    the other's key is exactly how a student came to be marked right for an
    answer that was wrong.
    """
    key = _text_key(entry.get("text", ""), _question_type(entry))
    position = entry.get("position")
    if position is not None:
        row = by_position.get(int(position))
        if row is not None and _text_key(row.get("question_text", ""), _question_type(row)) == key:
            return row
    candidates = by_text.get(key) or []
    return candidates[0] if len(candidates) == 1 else None


def refresh_answer_key(payload: dict, rows) -> list[str]:
    """Re-read the answer key for every frozen question that still exists.

    The questions the student saw are left exactly as they were; only `correct`
    moves. A question the teacher has since reworded or deleted keeps the key it
    was set under, because there is nothing to match it to and guessing would be
    worse than leaving it alone.

    Mutates `payload` and returns the text of the questions that could not be
    matched, so a caller can say how much of the paper it left behind.
    """
    by_position, by_text = _live_lookup(rows)
    unmatched: list[str] = []
    for entry in payload.get("questions") or []:
        text = entry.get("text", "")
        row = live_match(entry, by_position, by_text)
        if row is None:
            unmatched.append(text)
            continue
        correct = live_correct(row)
        if _question_type(entry) in TEXT_ANSWER_TYPES:
            entry["correct"] = correct
            entry["hint"] = grading.student_hint(correct)
            entry["limit"] = grading.input_limit(correct)
            continue
        rekeyed = rekey_choice(entry, row, correct)
        if rekeyed is None:
            unmatched.append(text)
            continue
        entry["correct"] = rekeyed
    return unmatched


# --------------------------------------------------------------------------- re-syncing


def _realign(old_entry: dict, new_entry: dict) -> list[list]:
    """Show a surviving question's options in the order the student already saw.

    Re-freezing reshuffles, and a student who has been staring at the same four
    options should not watch them jump around because their teacher corrected a
    different question. Options the teacher has just added sort to the end.
    """
    seen = {grading.normalise_text(text): position
            for position, (_, text) in enumerate(old_entry.get("options") or [])}
    options = list(new_entry.get("options") or [])
    return sorted(options, key=lambda pair: seen.get(grading.normalise_text(pair[1]), len(seen)))


def translate_answer(old_entry: dict, new_entry: dict, answer):
    """Express an answer given against `old_entry` in `new_entry`'s labels.

    `None` means the option the student picked is no longer on offer, so the
    question goes back to unanswered rather than quietly pointing somewhere else.
    """
    question_type = _question_type(new_entry)
    if question_type in TEXT_ANSWER_TYPES:
        typed = str(answer or "").strip()
        return typed or None
    old_text = {str(label): grading.normalise_text(text) for label, text in (old_entry.get("options") or [])}
    new_label = {grading.normalise_text(text): str(label) for label, text in (new_entry.get("options") or [])}
    wanted = answer if isinstance(answer, list) else [answer]
    moved = []
    for label in wanted:
        text = old_text.get(str(label))
        if text is None:
            continue
        label_now = new_label.get(text)
        if label_now is not None and label_now not in moved:
            moved.append(label_now)
    if question_type == SELECT_ALL_TYPE:
        return moved or None
    return moved[0] if moved else None


def resync(payload: dict, rows, randomize_answers: bool = False) -> tuple[dict, dict]:
    """Rebuild `payload` from the quiz's current questions, keeping the answers.

    Questions are matched on their text, so a reworded question counts as a new
    one. That is the safe reading: nobody can tell from the outside whether a
    rewrite fixed a typo or asked something else entirely, and treating it as new
    costs one answer rather than crediting the wrong one.

    Surviving questions stay where they were and additions go on the end, so the
    paper the student is halfway through does not renumber under them.

    Returns `(new_payload, summary)` and leaves `payload` untouched.
    """
    old_entries = list(payload.get("questions") or [])
    old_answers = payload.get("answers") or {}
    old_by_position: dict = {}
    old_by_text: dict = {}
    for index, entry in enumerate(old_entries):
        position = entry.get("position")
        if position is not None:
            old_by_position.setdefault(int(position), index)
        old_by_text.setdefault(_text_key(entry.get("text", ""), _question_type(entry)), []).append(index)

    fresh = [freeze(row, randomize_answers) for row in rows]
    matched: dict[int, int] = {}
    claimed: set[int] = set()
    for new_index, entry in enumerate(fresh):
        key = _text_key(entry["text"], _question_type(entry))
        old_index = None
        position = entry.get("position")
        if position is not None:
            candidate = old_by_position.get(int(position))
            if candidate is not None and _text_key(old_entries[candidate].get("text", ""),
                                                   _question_type(old_entries[candidate])) == key:
                old_index = candidate
        if old_index is None:
            # Wording only identifies a question when it is the only one worded
            # that way; anything else is a guess, and a wrong guess carries an
            # answer onto a question with a different key.
            same_wording = old_by_text.get(key) or []
            if len(same_wording) == 1:
                old_index = same_wording[0]
        if old_index is not None and old_index not in claimed:
            matched[new_index] = old_index
            claimed.add(old_index)

    survivors = sorted(matched, key=lambda new_index: matched[new_index])
    additions = [new_index for new_index in range(len(fresh)) if new_index not in matched]

    questions: list[dict] = []
    answers: dict[str, object] = {}
    kept = dropped = changed = 0
    for position, new_index in enumerate(survivors + additions):
        entry = fresh[new_index]
        old_index = matched.get(new_index)
        if old_index is not None:
            old_entry = old_entries[old_index]
            entry = {**entry, "options": _realign(old_entry, entry)}
            if entry_signature(old_entry) != entry_signature(entry):
                changed += 1
            given = old_answers.get(str(old_index))
            if given is not None:
                moved = translate_answer(old_entry, entry, given)
                if moved is None:
                    dropped += 1
                else:
                    answers[str(position)] = moved
                    kept += 1
        questions.append(entry)

    new_payload = {**payload, "questions": questions, "answers": answers}
    summary = {
        "added": len(additions),
        "removed": len(old_entries) - len(matched),
        "changed": changed,
        "kept": kept,
        "dropped": dropped,
    }
    return new_payload, summary


# --------------------------------------------------------------------------- tabs


def revision(payload: dict) -> int:
    """How many times this attempt has been saved. Only ever goes up."""
    try:
        return int(payload.get("revision") or 0)
    except (TypeError, ValueError):
        return 0


def overtaken(payload: dict, last_seen) -> bool:
    """True when someone has saved newer progress than this tab last read.

    The same student with the quiz open in two tabs is two Streamlit sessions
    that cannot see each other, so "newer" has to be written down somewhere both
    can read it. `last_seen` of `None` is a tab that has only just opened the
    attempt: it is by definition current, not behind.
    """
    if last_seen is None:
        return False
    try:
        return revision(payload) > int(last_seen)
    except (TypeError, ValueError):
        return False


# --------------------------------------------------------------------------- wording


def _count(number: int, noun: str) -> str:
    return f"{number} {noun}{'' if number == 1 else 's'}"


def describe(summary: dict) -> str:
    """A one-line, student-facing account of what a resync just did.

    Named in the student's terms — questions and answers, not payloads — because
    this is the only explanation they get for a paper that changed shape while
    they were holding it.
    """
    moves = []
    if summary.get("added"):
        moves.append(f"{_count(summary['added'], 'question')} added")
    if summary.get("removed"):
        moves.append(f"{_count(summary['removed'], 'question')} removed")
    if summary.get("changed"):
        moves.append(f"{_count(summary['changed'], 'question')} changed")
    if not moves:
        return "Your teacher updated this assessment."
    sentence = f"Your teacher updated this assessment: {', '.join(moves)}."
    if summary.get("dropped"):
        answers = _count(summary["dropped"], "answer")
        sentence += f" {answers.capitalize()} no longer matched an option on offer, so {'it was' if summary['dropped'] == 1 else 'they were'} cleared."
    if summary.get("kept"):
        sentence += " Your other answers were kept."
    return sentence
