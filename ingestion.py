"""Question-bank extraction and validation, independent of Streamlit UI.

A file is read as a list of *blocks* in document order -- lines of text, each
remembering how it was formatted, and tables -- and the reader works out which
lines are questions, which are options, which option is marked correct, and
what is left over. Leftover tables (and the sentence introducing them) become
the quiz's case material: an accounting question that says "using the data
above" is unanswerable without the balance sheet it refers to.

The rule this module keeps above all others: **nothing goes missing silently.**
Anything that is not part of a question is either kept as case material or
named in a note, and a question whose answer the file does not give is kept
with a blank answer for the teacher to fill in rather than dropped.
"""

from __future__ import annotations

import io
import re

from docx import Document
from docx.oxml.ns import qn
from docx.table import Table
from docx.text.paragraph import Paragraph

import grading

SELECT_ALL_TYPE = grading.SELECT_ALL_TYPE

# What an answer copy uses to tick the right option. U+F00C is Font Awesome's
# check, which is what a Moodle review page turns into when pasted into Word.
CHECKS = "\u2713\u2714\u2705\u2611\uf00c\uf058\uf14a\uf046"
# Crosses mark a wrong choice on the same pages. They are stripped, never read.
CROSSES = "\u2717\u2718\u274c\u2612\uf00d\uf057"
# Word's symbol fonts draw ticks from private code points instead.
_SYMBOL_CHECKS = {"wingdings": {"F0FC", "F0FE"}, "wingdings 2": {"F050", "F052"}}

_QUESTION = re.compile(r"^\s*(\d+)[.)](?:\s+|(?=\D))\s*(.+)$")
# The optional asterisk is a common way of marking the right option in a plain
# text file ("*B) Paris"); "(a)" is a common way of lettering one in Word.
_OPTION = re.compile(r"^\s*(\*\s*)?\(?([A-F])[.)]\s*(.+)$", re.I)
_MARKER = re.compile(r"^\s*answer\s*key\s*:?-?\s*(.*)$", re.I)
# "3. A, C" is a select-all answer; the lookahead keeps "1. Answer" from reading
# as question 1, option A.
_KEY_PAIR = re.compile(r"(\d+)\s*[.):-]\s*([A-F](?:\s*,\s*[A-F])*)(?![A-Za-z])", re.I)
# A per-question answer: Moodle's Aiken format ("ANSWER: B") and the feedback
# line a Moodle review page prints under every question.
_KEY_LINE = re.compile(r"^\s*(?:answer|ans|correct answer)\s*[:=]\s*(.+?)\s*$", re.I)
_CORRECT_IS = re.compile(r"^\s*the\s+correct\s+answers?\s+(?:is|are)\s*:?\s*(.+?)\s*$", re.I)
_QUESTION_HEADER = re.compile(r"^\s*question\s+(\d+)\s*[:.]?\s*$", re.I)
# Page furniture that a copied quiz carries along with its questions.
_CHROME = re.compile(
    r"^\s*(?:select one(?: or more)?|select all that apply|choose (?:one|all that apply|the (?:best|correct) answer)"
    r"|not yet answered|not answered|answer saved|marked out of [\d.]+|mark [\d.]+ out of [\d.]+"
    r"|flag question|remove flag|question text|correct|incorrect|partially correct|feedback|information"
    r"|your answer is (?:correct|incorrect|partially correct))\s*[:.!]?\s*$",
    re.I,
)
_SELECT_ALL_HINT = re.compile(r"select (?:one or more|all that apply)|choose all that apply", re.I)
_CORRECT_TAG = re.compile(r"\s*[\(\[]\s*correct\s*[\)\]]\s*$", re.I)
# Separated letters only: "A, C" or "a and c", never "Cafe" read as C, A, F, E.
_LETTERS = re.compile(r"^\(?[A-F]\)?(?:(?:\s*(?:,|and|&)\s*|\s+)\(?[A-F]\)?)*\.?$", re.I)
_NUMERIC_CELL = re.compile(r"^[\s$\u20ac\u00a3(),.%\d\u2212-]+$")

# Prose before the first question is a passage worth keeping only if there is
# enough of it to be one; a title line is not case material.
PASSAGE_CHARACTERS = 150


# -- Reading the file ---------------------------------------------------------


def _decode(raw: bytes) -> str:
    # A byte-order mark says outright what the encoding is, so trust it. Without
    # one, utf-16 must never be guessed at: `bytes.decode("utf-16")` succeeds on
    # almost any even-length input by pairing bytes up, so a Windows ANSI file --
    # what "Save as plain text" writes the moment the document contains a curly
    # apostrophe -- came back as CJK noise and parsed as nothing at all. Because
    # it turned on the file's length being even, adding a space to the end could
    # fix or break it, which made it look intermittent.
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        try:
            return raw.decode("utf-16")
        except UnicodeDecodeError:
            pass
    # utf-8-sig also eats the utf-8 BOM, which is not whitespace to `re` and so
    # kept question 1 from ever matching. cp1252 is the Windows ANSI fallback.
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            return raw.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", errors="replace")


def extract_upload(upload) -> str:
    """The upload as plain text: a .docx's paragraphs, or a text file decoded."""
    if upload.name.lower().endswith(".docx"):
        return "\n".join(p.text for p in Document(io.BytesIO(upload.getvalue())).paragraphs)
    return _decode(upload.getvalue())


def upload_blocks(upload) -> list[dict]:
    """The upload as blocks, in document order.

    Reading a .docx through `Document.paragraphs`, as this used to, skips every
    table without a trace -- the balance sheet a question was about simply
    vanished, and nothing on screen said so.
    """
    if upload.name.lower().endswith(".docx"):
        return _docx_blocks(Document(io.BytesIO(upload.getvalue())))
    return text_blocks(_decode(upload.getvalue()))


def text_blocks(raw: str) -> list[dict]:
    return [{"kind": "text", "text": line, "marks": set()} for line in raw.splitlines()]


def _docx_blocks(document) -> list[dict]:
    blocks: list[dict] = []
    for element in _body_elements(document.element.body):
        if element.tag == qn("w:p"):
            blocks.extend(_paragraph_lines(Paragraph(element, document)))
        else:
            rows = _table_rows(Table(element, document))
            if any(cell for row in rows for cell in row):
                blocks.append({"kind": "table", "rows": rows})
    return blocks


def _body_elements(parent):
    for child in parent.iterchildren():
        if child.tag in (qn("w:p"), qn("w:tbl")):
            yield child
        elif child.tag == qn("w:sdt"):
            # A content control wraps ordinary paragraphs and tables.
            content = child.find(qn("w:sdtContent"))
            if content is not None:
                yield from _body_elements(content)


def _paragraph_lines(paragraph) -> list[dict]:
    """A paragraph as one block per line, each with the marks its text carries.

    A soft line break is a new line to the reader, because a question pasted
    from the web often arrives as one paragraph with its options on breaks.
    """
    lines: list[list[tuple[str, set]]] = [[]]
    for item in paragraph.iter_inner_content():
        for run in getattr(item, "runs", [item]):
            marks = _run_marks(run)
            for index, piece in enumerate((run.text + _symbol_checks(run)).split("\n")):
                if index:
                    lines.append([])
                lines[-1].append((piece, marks))
    return [{"kind": "text", "text": "".join(piece for piece, _ in parts), "marks": _line_marks(parts)}
            for parts in lines]


def _symbol_checks(run) -> str:
    ticks = ""
    for symbol in run._r.findall(qn("w:sym")):
        font = (symbol.get(qn("w:font")) or "").strip().casefold()
        char = (symbol.get(qn("w:char")) or "").upper().rjust(4, "0")
        if char in _SYMBOL_CHECKS.get(font, set()):
            ticks += "\u2713"
    return ticks


def _run_marks(run) -> set:
    marks = set()
    if run.bold:
        marks.add("bold")
    if run.font.highlight_color is not None:
        marks.add("highlight")
    else:
        properties = run._r.rPr
        shading = properties.find(qn("w:shd")) if properties is not None else None
        if shading is not None and (shading.get(qn("w:fill")) or "auto").upper() not in ("AUTO", "FFFFFF"):
            marks.add("highlight")
    colour = run.font.color
    if colour is not None and colour.type is not None:
        value = str(colour.rgb or colour.theme_color or "")
        if value and value.upper() not in ("000000", "TEXT_1 (13)", "AUTO"):
            marks.add(f"colour {value}")
    return marks


def _line_marks(parts) -> set:
    """Marks every *meaningful* piece of a line shares.

    The option letter is often its own run in a different style -- Moodle copies
    put "a" plain and ". 13.8%" bold and highlighted -- so pieces that are only
    a letter, punctuation or a tick do not get a vote.
    """
    voting = [marks for piece, marks in parts if _meaningful(piece)]
    if not voting:
        return set()
    return set.intersection(*voting)


def _meaningful(piece: str) -> bool:
    core = re.sub(r"[\W_]+", "", piece)
    return bool(core) and not re.fullmatch(r"[A-Fa-f]", core)


def _table_rows(table) -> list[list[str]]:
    rows = []
    for row in table.rows:
        cells, previous = [], None
        for cell in row.cells:
            # A merged cell comes back once per grid column it spans.
            cells.append("" if cell._tc is previous else " ".join(cell.text.split()))
            previous = cell._tc
        rows.append(cells)
    return rows


def markdown_table(rows: list[list[str]]) -> str:
    """A table as Markdown, numeric columns right-aligned like a statement."""
    width = max(len(row) for row in rows)
    rows = [[cell.replace("|", "\\|") for cell in row] + [""] * (width - len(row)) for row in rows]
    head, body = rows[0], rows[1:]
    alignment = []
    for column in range(width):
        values = [row[column] for row in body if row[column]]
        numeric = values and sum(bool(_NUMERIC_CELL.match(v)) for v in values) >= 0.6 * len(values)
        alignment.append(" ---: |" if numeric else " --- |")
    lines = ["| " + " | ".join(head) + " |", "|" + "".join(alignment)]
    lines += ["| " + " | ".join(row) + " |" for row in body]
    return "\n".join(lines)


def document_markdown(blocks: list[dict]) -> str:
    """The blocks as Markdown, formatting marks included, for the AI reader.

    The marks are the whole point: a model cannot see that an option was bold
    or highlighted unless it is told, and that is how answer copies say which
    option is right.
    """
    out = []
    for block in blocks:
        if block["kind"] == "table":
            out.append(markdown_table(block["rows"]))
            continue
        line = block["text"].strip()
        if not line:
            continue
        for tick in CHECKS:
            line = line.replace(tick, " [CHECKMARK] ")
        line = " ".join(line.split())
        marks = block["marks"]
        if "bold" in marks:
            line = f"**{line}**"
        if "highlight" in marks:
            line = f"=={line}=="
        colour = next((mark for mark in marks if mark.startswith("colour ")), None)
        if colour:
            line = f"[{colour}] {line}"
        out.append(line)
    return "\n".join(out)


# -- Making sense of it -------------------------------------------------------


def parse_bank(raw: str) -> list[dict]:
    return parse_report(raw)[0]


def parse_report(raw: str) -> tuple[list[dict], list[str]]:
    """The strict reading: only questions whose answer the file gives, plus a note
    about every question it had to drop.

    Dropping questions silently is how a 40-question upload becomes a
    35-question quiz with nobody the wiser.
    """
    questions, skipped = [], []
    for question in read_blocks(text_blocks(raw))["questions"]:
        labels = {label for label, _ in question["options"]}
        correct = question["correct_label"]
        given = question.get("given_key")
        if question["options"] and correct and set(correct if isinstance(correct, list) else [correct]) <= labels:
            questions.append(question)
        elif given:
            skipped.append(f"Question {question['number']}: the answer key says {given}, which is not one of its options")
        else:
            skipped.append(f"Question {question['number']}: no entry in the answer key")
    return questions, skipped


def read_blocks(blocks: list[dict]) -> dict:
    """Everything the review screen needs from one file.

    Returns `questions` (every one found, with a blank `correct_label` where the
    file does not say), `case_material` (Markdown), and `notes` -- plain
    sentences about anything the teacher has to check or fill in.
    """
    reader = _Reader(numbered=_uses_numbers(blocks))
    for block in blocks:
        reader.feed(block)
    return reader.finish()


def _uses_numbers(blocks: list[dict]) -> bool:
    """Whether this file numbers its questions ("1. ...", followed by options).

    A numbered file is read exactly as it always was. A file without numbers --
    a Moodle quiz pasted into Word -- is read by layout instead: a line of text
    followed by lettered options is a question.
    """
    lines = [block["text"].strip() for block in blocks
             if block["kind"] == "text" and block["text"].strip() and not _CHROME.match(block["text"])]
    return any(_QUESTION.match(line) and _OPTION.match(following)
               for line, following in zip(lines, lines[1:]))


class _Reader:
    def __init__(self, numbered: bool):
        self.numbered = numbered
        self.questions: list[dict] = []
        self.current: dict | None = None
        self.pending: list[str] = []      # prose no question has claimed yet
        self.trailing: list[str] = []     # prose after a numbered question's options
        self.case: list[str] = []
        self.ignored: list[str] = []
        self.answers: dict[int, str] = {}
        self.in_answer_key = False
        self.select_all_hint = False
        self.next_number: int | None = None
        self.stray_options = 0
        self.tables = 0

    # Each block, in order.

    def feed(self, block: dict) -> None:
        if block["kind"] == "table":
            self.tables += 1
            if self.in_answer_key:
                for row in block["rows"]:
                    self._answer_line(". ".join(cell for cell in row if cell))
                return
            self._claim_pending_as_case()
            self._claim_trailing_as_case()
            self.case.append(markdown_table(block["rows"]))
            return
        line = " ".join(block["text"].split())
        if not line:
            return
        if (marker := _MARKER.match(line)):
            self._settle()
            self.in_answer_key = True
            self._answer_line(marker.group(1))
            return
        if self.in_answer_key:
            self._answer_line(line)
            return
        if _CHROME.match(line):
            if _SELECT_ALL_HINT.search(line):
                if self.current is not None and not self.current["options"] and self.numbered:
                    self.current["select_all"] = True
                else:
                    self.select_all_hint = True
            return
        if (header := _QUESTION_HEADER.match(line)):
            self._settle()
            self.next_number = int(header.group(1))
            return
        key = _KEY_LINE.match(line) or _CORRECT_IS.match(line)
        if key and self.current is not None:
            self.current["key_line"] = key.group(1)
            return
        numbered = _QUESTION.match(line) if self.numbered else None
        option = _OPTION.match(line)
        if numbered:
            self._settle()
            self._start(numbered.group(2), int(numbered.group(1)))
        elif option:
            self._option(option, block["marks"])
        elif self.numbered:
            if self.current is None:
                self.pending.append(line)
            elif not self.current["options"]:
                self.current["question_text"] += " " + line
            else:
                self.trailing.append(line)
        else:
            self.pending.append(line)

    def _option(self, match, marks: set) -> None:
        if not self.numbered and (self.current is None or self.pending or not self.current["options"]):
            if self.pending:
                passage, text = _split_passage(self.pending)
                self.pending = []
                if passage:
                    self.case.extend(passage)
                self._start(text, self.next_number)
            elif self.current is None:
                self.stray_options += 1
                return
        if self.current is None:
            self.stray_options += 1
            return
        if self.trailing:
            # A numbered question's prose that runs on after its options.
            self.current["question_text"] += " " + " ".join(self.trailing)
            self.trailing = []
        text, inline = _option_marks(match.group(3))
        if match.group(1):
            inline.add("asterisk")
        if text:
            self.current["options"].append((match.group(2).upper(), text))
            self.current["marks"].append(inline | marks)

    def _start(self, text: str, number: int | None) -> None:
        if number is None:
            number = (self.questions[-1]["number"] + 1) if self.questions else 1
        self.current = {"number": number, "question_text": text, "options": [], "marks": [],
                        "select_all": self.select_all_hint}
        self.select_all_hint = False
        self.next_number = None
        self.questions.append(self.current)

    def _answer_line(self, line: str) -> None:
        for number, letters in _KEY_PAIR.findall(line or ""):
            self.answers[int(number)] = letters

    # Leftovers.

    def _settle(self) -> None:
        """Close the question in progress before something new starts."""
        if self.trailing and self.current is not None:
            self.current["question_text"] += " " + " ".join(self.trailing)
        self.trailing = []
        self._claim_pending_as_leftover()

    def _claim_pending_as_case(self) -> None:
        self.case.extend(self.pending)
        self.pending = []

    def _claim_trailing_as_case(self) -> None:
        self.case.extend(self.trailing)
        self.trailing = []

    def _claim_pending_as_leftover(self) -> None:
        if not self.pending:
            return
        if not self.questions and sum(len(line) for line in self.pending) >= PASSAGE_CHARACTERS:
            self.case.extend(self.pending)
        else:
            self.ignored.extend(self.pending)
        self.pending = []

    def finish(self) -> dict:
        self._settle()
        notes: list[str] = []
        questions = [_resolve(question, self.answers, notes) for question in self.questions]
        if self.ignored:
            shown = [f"\u201c{_clip(line)}\u201d" for line in self.ignored[:5]]
            more = f" and {len(self.ignored) - 5} more line(s)" if len(self.ignored) > 5 else ""
            notes.append("Not part of any question, so left out: " + "; ".join(shown) + more + ".")
        if self.stray_options:
            notes.append(f"{self.stray_options} lettered line(s) had no question before them and were left out.")
        case = "\n\n".join(self.case).strip()
        if not questions:
            kept = ""
            if case and self.tables:
                kept = (f" Its {self.tables} table{'s' if self.tables != 1 else ''} and the text around "
                        f"{'them' if self.tables != 1 else 'it'} were kept as the quiz's Description/Context.")
            elif case:
                kept = " Its text was kept as the quiz's Description/Context."
            notes.insert(0, (
                "No questions found. A question is a line of text followed by lettered options "
                "(A. / B. / C. or a) b) c)), with the right one marked by an answer key, a tick, bold or highlight."
                + kept
            ))
        # `unread` is how much of the file nothing could place -- the case for
        # suggesting the AI reader. A file of nothing but tables has none.
        return {"questions": questions, "case_material": case, "notes": notes, "tables": self.tables,
                "unread": len(self.ignored) + self.stray_options}


def _split_passage(pending: list[str]) -> tuple[list[str], str]:
    """Separate a passage from the question that follows it.

    Without numbers the only boundary is layout, so several paragraphs before a
    set of options all read as one question. When the last one is plainly the
    question and the rest is long enough to be a passage, the rest is case
    material instead.
    """
    if (len(pending) > 1 and pending[-1].rstrip().endswith(("?", ":"))
            and sum(len(line) for line in pending[:-1]) >= PASSAGE_CHARACTERS):
        return pending[:-1], pending[-1]
    return [], " ".join(pending)


def _option_marks(text: str) -> tuple[str, set]:
    marks = set()
    if any(tick in text for tick in CHECKS):
        marks.add("check")
    for character in CHECKS + CROSSES:
        text = text.replace(character, " ")
    if _CORRECT_TAG.search(text):
        marks.add("tag")
        text = _CORRECT_TAG.sub("", text)
    if re.search(r"\s\*+\s*$", text):
        marks.add("asterisk")
        text = re.sub(r"\s\*+\s*$", "", text)
    return " ".join(text.split()), marks


_SOURCES = {"check": "\u2713 mark", "asterisk": "asterisk", "tag": "(correct) label"}


def _resolve(question: dict, answers: dict, notes: list[str]) -> dict:
    """Decide a question's type and answer from whatever the file offered."""
    number, options, marks = question["number"], question["options"], question["marks"]
    labels = [label for label, _ in options]
    explicit, source, given = None, "", None
    if question.get("key_line"):
        explicit, given = _key_labels(question["key_line"], options), question["key_line"]
        source = "answer line"
    elif number in answers:
        explicit, given = _key_labels(answers[number], options), answers[number]
        source = "answer key"

    marked, mark_source = [], ""
    strong = [label for label, option_marks in zip(labels, marks) if option_marks & set(_SOURCES)]
    if strong:
        marked = strong
        mark_source = _SOURCES[next(iter(sorted(marks[labels.index(strong[0])] & set(_SOURCES))))]
    else:
        # Formatting only counts when it tells the options apart: a file with
        # every option in bold is saying nothing about which is right.
        for flag, name in (("highlight", "highlight"), ("bold", "bold")):
            flagged = [label for label, option_marks in zip(labels, marks) if flag in option_marks]
            if 0 < len(flagged) < len(labels):
                marked, mark_source = flagged, name
                break
        else:
            colours = [next((m for m in option_marks if m.startswith("colour ")), None) for option_marks in marks]
            coloured = [label for label, colour in zip(labels, colours) if colour]
            if 0 < len(coloured) < len(labels) and len({c for c in colours if c}) == 1:
                marked, mark_source = coloured, "coloured text"

    if not options:
        # A question with no options is a typed one; its answer line is the answer.
        answer = question.get("key_line") or ""
        if not answer:
            notes.append(f"Question {number}: no options and no answer, so it was read as a typed question "
                         "with its answer left blank. Fill it in, or delete the row.")
        return {**_public(question), "question_type": "Short answer", "correct_label": answer,
                "key_source": "answer line" if answer else "", "given_key": given}

    if explicit is not None:
        correct = explicit
        if marked and set(marked) != set(explicit) and set(explicit) <= set(labels):
            notes.append(f"Question {number}: the {source} says {', '.join(explicit)} but the "
                         f"{mark_source} is on {', '.join(marked)}. The {source} was used; check it.")
    else:
        correct, source = marked, mark_source

    if correct and not set(correct) <= set(labels):
        notes.append(f"Question {number}: the {source} says {given}, which is not one of its options. "
                     "Fill in the right one.")
        correct, source = [], ""
    if not correct:
        if explicit is None:
            notes.append(f"Question {number}: the file doesn't show which answer is right. Fill it in.")
        source = ""

    texts = [grading.normalise_text(text) for _, text in options]
    if sorted(texts) == ["false", "true"]:
        by_text = dict(zip(labels, texts))
        key = "A" if correct and by_text[correct[0]] == "true" else "B" if correct else ""
        return {**_public(question), "options": [("A", "True"), ("B", "False")],
                "question_type": "True / False", "correct_label": key, "key_source": source, "given_key": given}
    if len(correct) > 1 or question.get("select_all"):
        if len(correct) > 1 and not question.get("select_all"):
            notes.append(f"Question {number} has more than one answer marked, so it was read as "
                         "\u201cselect all that apply\u201d. Check that is what you meant.")
        return {**_public(question), "question_type": SELECT_ALL_TYPE, "correct_label": correct,
                "key_source": source, "given_key": given}
    return {**_public(question), "question_type": "Multiple choice", "correct_label": correct[0] if correct else "",
            "key_source": source, "given_key": given}


def _public(question: dict) -> dict:
    return {"number": question["number"], "question_text": question["question_text"].strip(),
            "options": list(question["options"])}


def _key_labels(value: str, options: list) -> list[str]:
    """An answer given as letters ("B", "A, C") or as the option's own text."""
    value = value.strip()
    if _LETTERS.match(value):
        return list(dict.fromkeys(re.findall(r"(?<![A-Z])[A-F](?![A-Z])", value.upper())))
    wanted = grading.normalise_text(value)
    return [label for label, text in options if grading.normalise_text(text) == wanted] or [value]


def _clip(line: str, limit: int = 70) -> str:
    return line if len(line) <= limit else line[: limit - 1].rstrip() + "\u2026"
