"""Question-bank extraction and validation, independent of Streamlit UI."""

from __future__ import annotations

import io
import re

from docx import Document


def extract_upload(upload) -> str:
    if upload.name.lower().endswith(".docx"):
        return "\n".join(p.text for p in Document(io.BytesIO(upload.getvalue())).paragraphs)
    raw = upload.getvalue()
    # Notepad and Excel write a byte-order mark. Decoded as plain utf-8 it stays
    # on the front of the first line, where it is not whitespace to `re`, so
    # question 1 never matched and was dropped without a word. utf-8-sig eats it,
    # and utf-16 covers the other thing "Save as Unicode" produces.
    for encoding in ("utf-8-sig", "utf-16"):
        try:
            return raw.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", errors="replace")


def parse_bank(raw: str) -> list[dict]:
    parsed, answers = _parse(raw)
    return [item for item, _ in _matched(parsed, answers)]


def _parse(raw: str) -> tuple[list[dict], dict]:
    marker = re.compile(r"^\s*answer\s*key\s*:?-?\s*(.*)$", re.I)
    question = re.compile(r"^\s*(\d+)[.)]\s+(.+)$")
    # `\s*`, not `\s+`: "A.Paris" with no space is still an option, and treating
    # it as prose folded it into the question text and then dropped the question.
    option = re.compile(r"^\s*([A-F])[.)]\s*(.+)$", re.I)
    answer = re.compile(r"^\s*(\d+)\s*[.):-]\s*([A-F])\s*$", re.I)
    parsed, answers, current, mode = [], {}, None, "questions"
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        marker_match = marker.match(line)
        if marker_match:
            mode = "answers"
            for pair in re.findall(r"(\d+)\s*[.):-]\s*([A-F])", marker_match.group(1), re.I):
                answers[int(pair[0])] = pair[1].upper()
        elif mode == "answers":
            match = answer.match(line)
            if match:
                answers[int(match.group(1))] = match.group(2).upper()
        elif (match := question.match(line)):
            if current:
                parsed.append(current)
            current = {"number": int(match.group(1)), "question_text": match.group(2), "options": []}
        elif (match := option.match(line)) and current:
            current["options"].append((match.group(1).upper(), match.group(2)))
        elif current and not current["options"]:
            current["question_text"] += " " + line
        elif current:
            current["question_text"] += " " + line
    if current:
        parsed.append(current)
    return parsed, answers


def parse_report(raw: str) -> tuple[list[dict], list[str]]:
    """`parse_bank`, plus a note about every question it had to drop.

    Dropping questions silently is how a 40-question upload becomes a
    35-question quiz with nobody the wiser.
    """
    questions, skipped = [], []
    parsed, answers = _parse(raw)
    for item, reason in _matched(parsed, answers, keep_rejected=True):
        if reason is None:
            questions.append(item)
        else:
            skipped.append(f"Question {item['number']}: {reason}")
    return questions, skipped


def _matched(parsed, answers, keep_rejected=False):
    for item in parsed:
        labels = {label for label, _ in item["options"]}
        correct = answers.get(item["number"])
        if correct is None:
            reason = "no entry in the answer key"
        elif correct not in labels:
            reason = f"the answer key says {correct}, which is not one of its options"
        else:
            reason = None
        if reason is None:
            yield {**item, "correct_label": correct}, None
        elif keep_rejected:
            yield item, reason
