"""The AI reader, without the network. Run with: python test_ai_reader.py

What matters here is not whether Gemini reads well -- that was checked against
the real files -- but what the app does with whatever comes back: an answer
the model cannot point to in the file is thrown away, a busy or retired model
is survived, and a bad key says so in words a teacher can act on.
"""

import json

import httpx

import ai_reader
import grading

FAILURES = []

DOCUMENT = "\n".join([
    "| Net sales | $4,885,340 |",
    "|---|---:|",
    "What was the net operating profit margin in Year 7?",
    "==**a. 13.8% [CHECKMARK]**==",
    "b. 31.0%",
    "Using same data as above, the total debt to equity ratio was:",
    "a. 0.55",
    "b. 1.07",
])


def show(text):
    print(str(text).encode("ascii", "backslashreplace").decode("ascii"))


def check(label, actual, expected):
    ok = actual == expected
    if not ok:
        FAILURES.append(label)
    show(("  pass " if ok else "  FAIL ") + label + ("" if ok else f"  expected {expected!r} got {actual!r}"))


def item(text, options, correct, source="marked inline", evidence="", question_type="Multiple choice"):
    return {"question_text": text, "question_type": question_type,
            "options": [{"label": label, "text": value} for label, value in options],
            "correct": correct, "key_source": source, "key_evidence": evidence}


GOOD = {
    "shared_context": "| Net sales | 4885340 |",
    "questions": [
        item("What was the net operating profit margin in Year 7?", [("a", "13.8%"), ("b.", "31.0%")], ["a"],
             evidence="a. 13.8% [CHECKMARK]"),
        # The model "knows" the answer -- and the file never gave it.
        item("Using same data as above, the total debt to equity ratio was:", [("A", "0.55"), ("B", "1.07")], ["B"],
             evidence="total liabilities divided by equity"),
    ],
    "notes": [],
}


print("== the model may read an answer, never supply one ==")
reading = ai_reader.normalise(GOOD, DOCUMENT)
first, second = reading["questions"]
check("a marked answer it can point to is kept", first["correct_label"], "A")
check("letters are cleaned up", first["options"], [("A", "13.8%"), ("B", "31.0%")])
check("and the source says the AI read it", first["key_source"], "AI \u00b7 mark in file")
check("an answer it cannot point to is dropped", second["correct_label"], "")
check("with no source", second["key_source"], "")
check("and the teacher is told", any(n.startswith("Question 2: the AI gave an answer") for n in reading["notes"]), True)

blank = ai_reader.normalise({"questions": [item("What was the net operating profit margin in Year 7?",
                                                 [("A", "13.8%"), ("B", "31.0%")], ["A"], source="not in document")]},
                            DOCUMENT)
check("'not in document' means blank, whatever else it says", blank["questions"][0]["correct_label"], "")

stray = ai_reader.normalise({"questions": [item("What was the net operating profit margin in Year 7?",
                                                 [("A", "13.8%"), ("B", "31.0%")], ["E"], evidence="13.8%")]},
                            DOCUMENT)
check("an answer that is not an option is dropped", stray["questions"][0]["correct_label"], "")
check("and named", any("(E)" in n for n in stray["notes"]), True)

invented = ai_reader.normalise({"questions": [item("What is the capital of Peru?", [("A", "Lima"), ("B", "Quito")], [],
                                                   source="not in document")]}, DOCUMENT)
check("wording that is not in the file is flagged", any("doesn't match the file" in n for n in invented["notes"]), True)

relettered = ai_reader.normalise({"questions": [item("Pick", [("A", "x"), ("A", "y"), ("", "z")], [],
                                                     source="not in document")]}, "Pick x y z")
check("repeated or missing letters are re-lettered", [l for l, _ in relettered["questions"][0]["options"]], ["A", "B", "C"])

truth = ai_reader.normalise({"questions": [item("The sky is green.", [("A", "False"), ("B", "True")], ["A"],
                                                evidence="a. False", question_type="True / False")]},
                            "The sky is green.\na. False [CHECKMARK]\nb. True")
check("True / False is keyed by meaning", (truth["questions"][0]["options"], truth["questions"][0]["correct_label"]),
      ([("A", "True"), ("B", "False")], "B"))

multi = ai_reader.normalise({"questions": [item("Even?", [("A", "2"), ("B", "3"), ("C", "4")], ["A", "C"],
                                                evidence="Even?")]}, "Even?\na. 2\nb. 3\nc. 4")
check("two answers make select-all", multi["questions"][0]["question_type"], grading.SELECT_ALL_TYPE)

typed = ai_reader.normalise({"questions": [item("Capital of France?", [], ["Paris"], source="answer key section",
                                                evidence="1. Paris", question_type="Short answer")]},
                            "1. Capital of France?\nAnswer key\n1. Paris")
check("a typed question keeps its answer text", typed["questions"][0]["correct_label"], "Paris")

print()
print("== case material: the file's own tables win ==")
check("tables read from the .docx are kept over the model's copy",
      ai_reader.normalise(GOOD, DOCUMENT, case_material="| Net sales | $4,885,340 |")["case_material"],
      "| Net sales | $4,885,340 |")
own = ai_reader.normalise(GOOD, DOCUMENT)
check("without them the model's copy is used", own["case_material"], "| Net sales | 4885340 |")
check("with a warning to check the figures", any("typed out by the AI" in n for n in own["notes"]), True)
check("an empty reading says so", ai_reader.normalise({"questions": []}, DOCUMENT)["notes"][0],
      "Gemini found no questions in this file either.")


print()
print("== talking to Google ==")


class Reply:
    def __init__(self, status, payload):
        self.status_code, self._payload = status, payload

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


def ok(result, finish="STOP"):
    return Reply(200, {"candidates": [{"finishReason": finish,
                                        "content": {"parts": [{"text": json.dumps(result)}]}}]})


def error(status, message):
    return Reply(status, {"error": {"message": message}})


class Script:
    """A stand-in for `httpx.post` that answers from a list and remembers the calls."""

    def __init__(self, *replies):
        self.replies, self.calls = list(replies), []

    def __call__(self, url, headers=None, json=None, timeout=None):
        self.calls.append({"url": url, "headers": headers})
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


def run(script, models=("first-model", "second-model")):
    naps = []
    try:
        result = ai_reader.read_document(DOCUMENT, "SECRET-KEY", models, post=script, sleep=naps.append)
        return result, None, naps
    except ai_reader.AIReadError as exc:
        return None, str(exc), naps


script = Script(ok(GOOD))
result, problem, _ = run(script)
check("a good reply is read", (problem, len(result["questions"])), (None, 2))
check("the key travels in a header", script.calls[0]["headers"]["x-goog-api-key"], "SECRET-KEY")
check("and never in the URL, where logs would keep it", "SECRET-KEY" in script.calls[0]["url"], False)

script = Script(error(503, "overloaded"), error(503, "overloaded"), ok(GOOD))
result, problem, naps = run(script)
check("a busy model is retried", (problem, len(script.calls)), (None, 3))
check("with a growing pause", naps, [2, 4])

script = Script(error(404, "no longer available"), ok(GOOD))
result, problem, _ = run(script)
check("a retired model falls through to the next", (problem, script.calls[1]["url"].split("/")[-1]),
      (None, "second-model:generateContent"))

script = Script(httpx.ConnectError("dns"), ok(GOOD))
result, problem, _ = run(script)
check("a dropped connection is retried", problem, None)

_, problem, _ = run(Script(error(400, "API key not valid. Please pass a valid API key.")))
check("a bad key says so", "API key" in (problem or ""), True)
_, problem, _ = run(Script(error(403, "forbidden")))
check("so does a forbidden one", "API key" in (problem or ""), True)

_, problem, _ = run(Script(*[error(503, "overloaded")] * 6))
check("busy everywhere ends in plain words", problem.startswith("Gemini is busy right now."), True)

now = [0.0]


def slow_busy(url, headers=None, json=None, timeout=None):
    """A model that is busy and takes 70 seconds to say so."""
    now[0] += 70
    slow_busy.timeouts.append(timeout)
    return error(503, "overloaded")


slow_busy.timeouts = []
try:
    ai_reader.read_document(DOCUMENT, "k", ("one", "two"), post=slow_busy, sleep=lambda s: None, clock=lambda: now[0])
    problem = None
except ai_reader.AIReadError as exc:
    problem = str(exc)
check("however the retries add up, the wait is capped", (problem or "").startswith("Gemini is busy"), True)
check("after three slow tries rather than six", len(slow_busy.timeouts), 3)
check("and the last try only gets the time that is left", slow_busy.timeouts[-1], ai_reader.TOTAL_SECONDS - 140)

_, problem, _ = run(Script(ok(GOOD, finish="MAX_TOKENS")))
check("a truncated reply asks for a smaller file", "Split it" in (problem or ""), True)
_, problem, _ = run(Script(Reply(200, {"promptFeedback": {"blockReason": "SAFETY"}})))
check("a refusal is reported with its reason", "SAFETY" in (problem or ""), True)
_, problem, _ = run(Script(Reply(200, {"candidates": [{"content": {"parts": [{"text": "not json"}]}}]})))
check("an unreadable reply is an error, not a crash", "could not be read" in (problem or ""), True)

calls = Script()
try:
    ai_reader.read_document("x" * (ai_reader.MAX_DOCUMENT_CHARACTERS + 1), "k", post=calls)
except ai_reader.AIReadError as exc:
    check("an oversized file is refused before anything is sent", ("Split it" in str(exc), calls.calls), (True, []))


print()
if FAILURES:
    print(f"{len(FAILURES)} FAILURE(S): {FAILURES}")
    raise SystemExit(1)
print("All AI-reader tests passed.")
