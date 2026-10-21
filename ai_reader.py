"""Reading a question bank with an OpenAI model, for files the rule-based reader cannot.

The model is asked to *transcribe*, never to answer. That line matters more
here than anywhere else in the app: a model that works a question out for itself
will sometimes get it wrong -- "total debt to equity" is 0.55 on interest-bearing
debt and 1.07 on total liabilities, and both are defensible -- and a wrong key
marks every student wrong. So an answer only survives if the model can point at
the words in the file that give it, and those words are then looked for in the
file. Anything it cannot trace is left blank for the teacher to fill in.

This used to call Google Gemini, whose overloads (503) and retired models (404)
made the button unreliable. The safeguards are the same ones, pointed at
OpenAI's Chat Completions API with a strict JSON schema.

Free of Streamlit and the database. The one network call goes through `post`,
which the tests replace.
"""

from __future__ import annotations

import json
import time

import httpx

import grading

SELECT_ALL_TYPE = grading.SELECT_ALL_TYPE
TYPES = ("Multiple choice", SELECT_ALL_TYPE, "True / False", "Short answer")
PROVIDER = "OpenAI"

# Only defaults: models are retired, so `[openai] models` in secrets.toml
# overrides them without a code change, and a 404 falls through to the next.
DEFAULT_MODELS = ("gpt-5.4-mini", "gpt-4.1-mini")
ENDPOINT = "https://api.openai.com/v1/chat/completions"
TIMEOUT_SECONDS = 75
RETRIES_PER_MODEL = 3
# However the retries and fallbacks add up, a teacher is not left watching a
# spinner for longer than this.
TOTAL_SECONDS = 150
MAX_DOCUMENT_CHARACTERS = 150_000
NOT_IN_DOCUMENT = "not in document"
_SOURCES = {"answer key section": "answer key", "marked inline": "mark in file"}


class AIReadError(Exception):
    """A reading that failed, worded for the teacher who pressed the button."""


PROMPT = """You transcribe a teacher's question-bank document into structured data.

Rules:
- Transcribe. Do not write, improve, reword or solve anything.
- Keep each option's letter as the document gives it, upper-cased (a. -> A). If
  the options have no letters, letter them A, B, C... in order.
- Leave instructions such as "Select one:" and page furniture such as
  "Question 3 Marked out of 14.29" or "Report an issue" out of the question text.
- The answer must come from the document: an answer-key section, an "Answer:"
  line, or a mark on an option -- [CHECKMARK], **bold**, ==highlight==, a
  [colour ...] tag, "(correct)" or an asterisk -- that the other options of that
  question do not have. Copy the exact words that show it into key_evidence.
- "[answer box: X]" is what was filled into an answer field on this copy. In an
  answer copy that IS the answer: use X for `correct`, key_source "marked
  inline", and quote "[answer box: X]" in key_evidence.
- "[icon A]", "[icon B]"... are small pictures such as radio buttons and check
  boxes; pictures that look alike share a letter. When one option's icon is
  different from the icons on its sibling options, that option is the one
  marked -- quote its line, icon included, as key_evidence.
- A list of statements each answered True or False is one "True / False"
  question per statement: question_text is the shared question followed by the
  statement; options are A True, B False.
- A question with several blanks (Assets = ___ and Liabilities = ___, or cells
  a. to e. in a table) is one "Short answer" question per blank, each worded so
  a student knows which blank it is.
- If the document has an ANSWER COPY section, take every question's wording,
  options and shared_context from the part before it, and only the answers from
  the answer copy.
- If the document does not show which answer is correct, leave `correct` empty
  and set key_source to "not in document". NEVER work the answer out yourself,
  even if you are sure; a teacher will fill it in.
- Material several questions share -- a case, a passage, data tables -- goes in
  shared_context as Markdown, tables included, every number copied exactly.
  NEVER put answers, worked solutions, marking feedback ("You have correctly
  selected...", "Correct answer: ...") or anyone's email address in
  shared_context: every student reads it.

Document:
"""


ANSWER_COPY_HEADING = "=== ANSWER COPY: the same quiz again, with the answers filled in ==="


def with_answer_copy(document: str, answer_copy: str) -> str:
    """The question file and its filled-in answer copy, as one document.

    Teachers often have both: the quiz as students should see it -- its tables
    with the blanks still blank -- and a copy someone completed. The questions
    and the Description/Context have to come from the first, or the answers are
    printed in the tables every student reads; the key comes from the second.
    """
    return f"{document.rstrip()}\n\n{ANSWER_COPY_HEADING}\n{answer_copy.strip()}"


def _strict(node):
    """The schema in the form OpenAI's strict mode insists on."""
    if isinstance(node, dict):
        node = {key: _strict(value) for key, value in node.items()}
        if node.get("type") == "object":
            node["additionalProperties"] = False
            node["required"] = list(node.get("properties", {}))
        return node
    if isinstance(node, list):
        return [_strict(value) for value in node]
    return node


SCHEMA = _strict({
    "type": "object",
    "properties": {
        "shared_context": {"type": "string", "description": "Case, passage or tables the questions refer to, as Markdown. Empty if none."},
        "questions": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "question_text": {"type": "string"},
                "question_type": {"type": "string", "enum": list(TYPES)},
                "options": {"type": "array", "items": {
                    "type": "object",
                    "properties": {"label": {"type": "string"}, "text": {"type": "string"}},
                }},
                "correct": {"type": "array", "items": {"type": "string"},
                            "description": "Option letters (or, for Short answer, the answer) the DOCUMENT marks as correct."},
                "key_source": {"type": "string", "enum": ["answer key section", "marked inline", NOT_IN_DOCUMENT]},
                "key_evidence": {"type": "string", "description": "The exact words in the document that show the answer."},
            },
        }},
        "notes": {"type": "array", "items": {"type": "string"}, "description": "Anything the teacher should check."},
    },
})


def read_document(document: str, api_key: str, models=DEFAULT_MODELS, *, case_material: str = "",
                  post=None, sleep=time.sleep, clock=time.monotonic) -> dict:
    """Read `document` (from `ingestion.document_markdown`) into the same shape
    `ingestion.read_blocks` returns.

    `case_material` is what the rule-based reader already took from the file's
    tables. It is kept in preference to the model's copy: a table read straight
    out of the .docx is exact, and a model re-typing a balance sheet is not
    something anyone should have to proof-read.
    """
    if not document.strip():
        raise AIReadError("The file is empty.")
    if len(document) > MAX_DOCUMENT_CHARACTERS:
        raise AIReadError("This file is too long to read in one go. Split it into smaller files and upload each one.")
    raw = _call(document, api_key, tuple(models) or DEFAULT_MODELS, post or httpx.post, sleep, clock)
    return normalise(raw, document, case_material)


def _body(model: str, document: str) -> dict:
    body = {
        "model": model,
        "messages": [{"role": "user", "content": PROMPT + document}],
        "response_format": {"type": "json_schema",
                            "json_schema": {"name": "question_bank", "strict": True, "schema": SCHEMA}},
    }
    if model.startswith(("gpt-5", "o")):
        # Transcribing needs no deliberation, and the default effort is several
        # times slower for the same reading.
        body["reasoning_effort"] = "low"
    return body


def _call(document: str, api_key: str, models: tuple, post, sleep, clock) -> dict:
    problem = "OpenAI could not be reached."
    deadline = clock() + TOTAL_SECONDS
    for model in models:
        for attempt in range(RETRIES_PER_MODEL):
            if attempt:
                sleep(2 * 2 ** (attempt - 1))
            remaining = deadline - clock()
            if remaining <= 5:
                raise AIReadError(f"{problem} Try again in a minute, or use Read question bank instead.")
            try:
                # The key goes in a header, never the URL, where proxies and
                # server logs would keep a copy of it.
                response = post(ENDPOINT, headers={"Authorization": f"Bearer {api_key}",
                                                   "Content-Type": "application/json"},
                                json=_body(model, document), timeout=min(TIMEOUT_SECONDS, remaining))
            except httpx.TransportError:
                problem = "OpenAI could not be reached."
                continue
            status = response.status_code
            if status == 200:
                return _payload(response)
            message, code = _error(response)
            if status == 401 or code == "invalid_api_key":
                raise AIReadError("OpenAI rejected the API key. Check [openai] api_key in .streamlit/secrets.toml.")
            if code == "insufficient_quota":
                # A 429 like a busy server, but retrying cannot help.
                raise AIReadError("OpenAI says this API key has run out of credit. Add credit to the account, "
                                  "or use Read question bank instead.")
            if status == 404 or code == "model_not_found":
                # Retired or not offered to this key: the next model may be fine.
                problem = f"The OpenAI model {model} isn't available."
                break
            if status in (429, 500, 502, 503, 504):
                problem = "OpenAI is busy right now."
                continue
            raise AIReadError(f"OpenAI refused the request ({status}): {message or 'no reason given'}")
    raise AIReadError(f"{problem} Try again in a minute, or use Read question bank instead.")


def _error(response) -> tuple[str, str]:
    try:
        error = response.json().get("error") or {}
        return str(error.get("message") or ""), str(error.get("code") or "")
    except (ValueError, AttributeError):
        return "", ""


def _payload(response) -> dict:
    try:
        data = response.json()
    except ValueError:
        raise AIReadError("OpenAI's reply could not be read. Try again.") from None
    choices = data.get("choices") or []
    if not choices:
        raise AIReadError("OpenAI's reply could not be read. Try again.")
    choice = choices[0]
    if choice.get("finish_reason") == "length":
        raise AIReadError("This file is too long to read in one go. Split it into smaller files and upload each one.")
    message = choice.get("message") or {}
    if message.get("refusal"):
        raise AIReadError(f"OpenAI declined to read this file ({message['refusal']}).")
    try:
        result = json.loads(message.get("content") or "")
    except ValueError:
        raise AIReadError("OpenAI's reply could not be read. Try again.") from None
    if not isinstance(result, dict):
        raise AIReadError("OpenAI's reply could not be read. Try again.")
    return result


def normalise(raw: dict, document: str, case_material: str = "") -> dict:
    """Hold the model's reading to the same standard as the rule-based one.

    Pure, so it can be tested without a network: letters are cleaned up, an
    answer that is not one of the question's options is dropped, and an answer
    whose evidence is not in the file is dropped too.
    """
    haystack = _plain(document)
    notes: list[str] = []
    questions: list[dict] = []
    for item in raw.get("questions") or []:
        if not isinstance(item, dict):
            continue
        text = " ".join(str(item.get("question_text") or "").split())
        if not text:
            continue
        number = len(questions) + 1
        question_type = item.get("question_type") if item.get("question_type") in TYPES else "Multiple choice"
        options = _options(item.get("options"))
        correct = [str(value).strip() for value in item.get("correct") or [] if str(value).strip()]
        source = item.get("key_source") if item.get("key_source") in _SOURCES else NOT_IN_DOCUMENT
        if source == NOT_IN_DOCUMENT or not correct:
            correct = []
        elif not _found(str(item.get("key_evidence") or ""), haystack):
            notes.append(f"Question {number}: the AI gave an answer it couldn't point to in the file, "
                         "so it was left blank. Fill it in.")
            correct = []
        if not _found(text, haystack):
            notes.append(f"Question {number}: its wording doesn't match the file exactly. Check it.")
        key_source = f"AI \u00b7 {_SOURCES[source]}" if correct else ""
        base = {"number": number, "question_text": text, "key_source": key_source}

        if question_type == "Short answer" or not options:
            if not correct:
                notes.append(f"Question {number}: no answer in the file. Fill it in.")
            questions.append({**base, "options": [], "question_type": "Short answer",
                              "correct_label": correct[0] if correct else ""})
            continue
        labels = [label for label, _ in options]
        correct = list(dict.fromkeys(value.strip(".)( ").upper() for value in correct))
        if not set(correct) <= set(labels):
            notes.append(f"Question {number}: the AI's answer ({', '.join(correct)}) isn't one of its options, "
                         "so it was left blank. Fill it in.")
            correct, base["key_source"] = [], ""
        elif not correct:
            notes.append(f"Question {number}: the file doesn't show which answer is right. Fill it in.")
        texts = [grading.normalise_text(option_text) for _, option_text in options]
        if question_type == "True / False" or sorted(texts) == ["false", "true"]:
            by_label = dict(zip(labels, texts))
            key = ("A" if by_label.get(correct[0]) == "true" else "B") if correct else ""
            questions.append({**base, "options": [("A", "True"), ("B", "False")],
                              "question_type": "True / False", "correct_label": key})
        elif question_type == SELECT_ALL_TYPE or len(correct) > 1:
            if question_type != SELECT_ALL_TYPE:
                notes.append(f"Question {number} has more than one answer marked, so it was read as "
                             "\u201cselect all that apply\u201d. Check that is what you meant.")
            questions.append({**base, "options": options, "question_type": SELECT_ALL_TYPE, "correct_label": correct})
        else:
            questions.append({**base, "options": options, "question_type": "Multiple choice",
                              "correct_label": correct[0] if correct else ""})

    shared = str(raw.get("shared_context") or "").strip()
    if case_material.strip():
        shared = _without_questions(case_material, questions, raw.get("questions") or [])
    elif shared:
        notes.append("The description/context was typed out by the AI. Check its figures against your file before publishing.")
    for remark in (raw.get("notes") or [])[:5]:
        if str(remark).strip():
            notes.append(f"{PROVIDER}: {str(remark).strip()}")
    if not questions:
        notes.insert(0, f"{PROVIDER} found no questions in this file either.")
    return {"questions": questions, "case_material": shared, "notes": notes}


def _without_questions(case_material: str, questions: list[dict], raw_questions: list) -> str:
    """The rule-based reader's Description/Context, minus what the model placed.

    When the rule-based reader finds no questions -- typed-answer files, where
    nothing has lettered options -- every paragraph near a table becomes case
    material, so the questions themselves and the lines giving their answers
    were shown to every student above every question. The model has said which
    words are questions and which are answers; those come out, and the tables
    and the passage stay exactly as the file had them.
    """
    placed = [_plain(question["question_text"]) for question in questions]
    placed += [_plain(str(item.get("key_evidence") or "")) for item in raw_questions if isinstance(item, dict)]
    placed = [text for text in placed if len(text) >= 8]
    kept = []
    for paragraph in case_material.strip().split("\n\n"):
        text = _plain(paragraph)
        # A table is kept whatever it says; so is a scrap too short to say
        # whose it is -- a lone "$" is inside every question about money.
        if paragraph.lstrip().startswith("|") or len(text) < 8:
            kept.append(paragraph)
        elif not any(text in question or question in text for question in placed):
            kept.append(paragraph)
    return "\n\n".join(kept).strip()


def _options(raw) -> list[tuple[str, str]]:
    options: list[tuple[str, str]] = []
    taken: set[str] = set()
    for option in raw or []:
        if not isinstance(option, dict):
            continue
        text = " ".join(str(option.get("text") or "").split())
        if not text:
            continue
        label = str(option.get("label") or "").strip(" .)(").upper()
        if len(label) != 1 or label not in "ABCDEF" or label in taken:
            label = next((letter for letter in "ABCDEF" if letter not in taken), None)
            if label is None:
                continue
        taken.add(label)
        options.append((label, text))
    return options


def _plain(text: str) -> str:
    # Table pipes too: a model quoting the row "Common stock $ 4 million" from
    # "| Common stock | $ 4 million |" is pointing at the file, and was refused.
    for token in ("[CHECKMARK]", "**", "==", "|", "---:", "---"):
        text = text.replace(token, " ")
    return grading.normalise_text(text)


def _found(snippet: str, haystack: str) -> bool:
    needle = _plain(snippet)
    return bool(needle) and needle in haystack
