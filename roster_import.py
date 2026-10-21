"""Reading a list of students -- pasted, or a CSV -- for sorting them into classes.

TAs sorting two classes of thirty did it one student at a time. What they
actually have is a list: a column copied out of the registrar's spreadsheet,
a CSV export, or addresses pasted from an email. This reads any of those into
(email, name, class) rows. Pure: no Streamlit, no database.
"""

from __future__ import annotations

import csv
import io
import re

_EMAIL = re.compile(r"[\w.+'-]+@[\w-]+(?:\.[\w-]+)+")
_EMAIL_HEADERS = {"email", "e-mail", "email address", "mail", "tamu email", "student email"}
_NAME_HEADERS = {"name", "student", "student name", "full name"}
_FIRST_HEADERS = {"first", "first name", "given name"}
_LAST_HEADERS = {"last", "last name", "surname", "family name"}
_CLASS_HEADERS = {"class", "section", "class name", "group", "team"}


def parse(text: str) -> dict:
    """Rows of {"email", "name", "class"} plus `problems`, lines that had no address.

    With a header row naming its columns, the columns are used. Without one,
    every address on every line is taken, and the rest of the line is its name.
    Repeated addresses are kept once (the first), so pasting a list twice adds
    nobody twice.
    """
    text = (text or "").lstrip("﻿")
    lines = [line for line in text.splitlines() if line.strip()]
    if not lines:
        return {"rows": [], "problems": [], "has_classes": False}
    delimiter = "\t" if "\t" in lines[0] else ","
    header = [cell.strip().casefold() for cell in next(csv.reader([lines[0]], delimiter=delimiter))]
    if any(cell in _EMAIL_HEADERS for cell in header):
        rows, problems = _with_header(lines, delimiter, header)
    else:
        rows, problems = _free_form(lines)
    seen, unique = set(), []
    for row in rows:
        if row["email"] not in seen:
            seen.add(row["email"])
            unique.append(row)
    return {"rows": unique, "problems": problems, "has_classes": any(row["class"] for row in unique)}


def _column(header: list[str], names: set) -> int | None:
    return next((index for index, cell in enumerate(header) if cell in names), None)


def _with_header(lines, delimiter, header):
    email_at = _column(header, _EMAIL_HEADERS)
    name_at, first_at, last_at = _column(header, _NAME_HEADERS), _column(header, _FIRST_HEADERS), _column(header, _LAST_HEADERS)
    class_at = _column(header, _CLASS_HEADERS)
    rows, problems = [], []
    for number, cells in enumerate(csv.reader(lines[1:], delimiter=delimiter), start=2):
        def cell(index):
            return cells[index].strip() if index is not None and index < len(cells) else ""
        found = _EMAIL.search(cell(email_at))
        if not found:
            if any(value.strip() for value in cells):
                problems.append(f"Line {number}: no email address")
            continue
        name = cell(name_at) or " ".join(part for part in (cell(first_at), cell(last_at)) if part)
        rows.append({"email": found.group().lower(), "name": name, "class": " ".join(cell(class_at).split())})
    return rows, problems


def _free_form(lines):
    rows, problems = [], []
    for number, line in enumerate(lines, start=1):
        found = list(_EMAIL.finditer(line))
        if not found:
            problems.append(f"Line {number}: no email address")
            continue
        # "Jordan Lee <jlee@tamu.edu>" or "jlee@tamu.edu, Jordan Lee": what is
        # left once the address is taken out is the name. A line of several
        # addresses (pasted from an email's To: field) has no names to give.
        rest = _EMAIL.sub(" ", line) if len(found) == 1 else ""
        name = " ".join(re.sub(r"[<>,;\t\"]", " ", rest).split())
        for match in found:
            rows.append({"email": match.group().lower(), "name": name, "class": ""})
    return rows, problems


def read_upload(raw: bytes) -> dict:
    """A CSV file, in whatever encoding a spreadsheet saved it."""
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            return parse(raw.decode(encoding))
        except UnicodeDecodeError:
            continue
    return parse(raw.decode("utf-8", errors="replace"))


def template_csv() -> bytes:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["email", "name", "class"])
    writer.writerow(["jordan.lee@tamu.edu", "Jordan Lee", "Section 501"])
    writer.writerow(["sam.ortiz@tamu.edu", "Sam Ortiz", "Section 502"])
    return buffer.getvalue().encode("utf-8")
