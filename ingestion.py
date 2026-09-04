"""Question-bank extraction and validation, independent of Streamlit UI."""

from __future__ import annotations

import io
import re

from docx import Document


def extract_upload(upload) -> str:
    if upload.name.lower().endswith(".docx"):
        return "\n".join(p.text for p in Document(io.BytesIO(upload.getvalue())).paragraphs)
    return upload.getvalue().decode("utf-8", errors="replace")


def parse_bank(raw: str) -> list[dict]:
    marker = re.compile(r"^\s*answer\s*key\s*:?-?\s*(.*)$", re.I)
    question = re.compile(r"^\s*(\d+)[.)]\s+(.+)$")
    option = re.compile(r"^\s*([A-F])[.)]\s+(.+)$", re.I)
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
    return [{**item, "correct_label": answers[item["number"]]} for item in parsed if answers.get(item["number"]) in {label for label, _ in item["options"]}]
