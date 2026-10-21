"""The AI reader, without the network. Run with: python test_ai_reader.py

What matters here is not whether the model reads well -- that was checked against
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
      "OpenAI found no questions in this file either.")

# Chapter 9, as a teacher loaded it: a typed-answer file, so the rule-based
# reader found no questions and kept every paragraph near the table -- the
# question and its "Answer:" line included -- as the Description every student
# reads. The model has said which words are the question and which the answer.
TYPED_FILE = "\n".join([
    "Cummins Inc. reports investments in affiliated companies.",
    "| Item | Year 2 |", "|---|---:|", "| Sales | $5,000 |",
    "How much income does Bava report relating to this investment for the year?",
    "Answer: $52.900",
])
RULE_CASE = ("Cummins Inc. reports investments in affiliated companies.\n\n| Item | Year 2 |\n| --- | ---: |\n"
             "| Sales | $5,000 |\n\nHow much income does Bava report relating to this investment for the year?"
             "\n\nAnswer: $52.900\n\n$")
typed_reading = ai_reader.normalise({"questions": [item(
    "How much income does Bava report relating to this investment for the year?", [], ["$52.900"],
    source="marked inline", evidence="Answer: $52.900", question_type="Short answer")]}, TYPED_FILE, RULE_CASE)
case = typed_reading["case_material"]
check("the question's own wording is taken out of the Description", "How much income" in case, False)
check("and so is the line that gives its answer", "52.900" in case, False)
check("the passage stays", case.startswith("Cummins Inc. reports"), True)
check("the table stays exactly as the file had it", "| Sales | $5,000 |" in case, True)
check("and the answer is still the key", typed_reading["questions"][0]["correct_label"], "$52.900")


# Chapter 8's Cisco question: the answer copy gives it in a table, and the model
# quotes the row without the pipes. That is pointing at the file.
table_file = "Common stock and APIC?\n| Common stock | $ 4 million |\n| Additional paid-in capital | $ 40,573 million |"
quoted = ai_reader.normalise({"questions": [item("Common stock and APIC?", [], ["$ 4 million"],
                                                 evidence="Common stock $ 4 million", question_type="Short answer")]},
                             table_file)
check("an answer quoted from a table row, pipes left out, is still found", quoted["questions"][0]["correct_label"],
      "$ 4 million")
combined = ai_reader.with_answer_copy("1. What is 2+2?", "1. What is 2+2? [answer box: 4]")
check("an answer copy comes after the questions, under its own heading",
      combined.index("What is 2+2?") < combined.index(ai_reader.ANSWER_COPY_HEADING) < combined.index("[answer box: 4]"),
      True)

print()
print("== talking to OpenAI ==")


class Reply:
    def __init__(self, status, payload):
        self.status_code, self._payload = status, payload

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


def ok(result, finish="stop"):
    return Reply(200, {"choices": [{"finish_reason": finish,
                                    "message": {"role": "assistant", "content": json.dumps(result)}}]})


def error(status, message, code=None):
    return Reply(status, {"error": {"message": message, "code": code}})


class Script:
    """A stand-in for `httpx.post` that answers from a list and remembers the calls."""

    def __init__(self, *replies):
        self.replies, self.calls = list(replies), []

    def __call__(self, url, headers=None, json=None, timeout=None):
        self.calls.append({"url": url, "headers": headers, "json": json})
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
check("the key travels in a header", script.calls[0]["headers"]["Authorization"], "Bearer SECRET-KEY")
check("and never in the URL, where logs would keep it", "SECRET-KEY" in script.calls[0]["url"], False)
check("nor in the body", "SECRET-KEY" in json.dumps(script.calls[0]["json"]), False)
check("the reply is held to a strict schema",
      script.calls[0]["json"]["response_format"]["json_schema"]["strict"], True)
check("which closes every object, as strict mode requires",
      ai_reader.SCHEMA["properties"]["questions"]["items"]["properties"]["options"]["items"]["additionalProperties"], False)

script = Script(error(503, "overloaded"), error(503, "overloaded"), ok(GOOD))
result, problem, naps = run(script)
check("a busy model is retried", (problem, len(script.calls)), (None, 3))
check("with a growing pause", naps, [2, 4])

script = Script(error(404, "The model `first-model` does not exist", "model_not_found"), ok(GOOD))
result, problem, _ = run(script)
check("a retired model falls through to the next", (problem, script.calls[1]["json"]["model"]),
      (None, "second-model"))

script = Script(httpx.ConnectError("dns"), ok(GOOD))
result, problem, _ = run(script)
check("a dropped connection is retried", problem, None)

_, problem, _ = run(Script(error(401, "Incorrect API key provided", "invalid_api_key")))
check("a bad key says so", "API key" in (problem or ""), True)
script = Script(error(429, "You exceeded your current quota", "insufficient_quota"))
_, problem, _ = run(script)
check("an account out of credit says so, and is not retried", ("credit" in (problem or ""), len(script.calls)),
      (True, 1))

_, problem, _ = run(Script(*[error(503, "overloaded")] * 6))
check("busy everywhere ends in plain words", problem.startswith("OpenAI is busy right now."), True)

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
check("however the retries add up, the wait is capped", (problem or "").startswith("OpenAI is busy"), True)
check("after three slow tries rather than six", len(slow_busy.timeouts), 3)
check("and the last try only gets the time that is left", slow_busy.timeouts[-1], ai_reader.TOTAL_SECONDS - 140)

_, problem, _ = run(Script(ok(GOOD, finish="length")))
check("a truncated reply asks for a smaller file", "Split it" in (problem or ""), True)
_, problem, _ = run(Script(Reply(200, {"choices": [{"finish_reason": "stop",
                                                    "message": {"content": None, "refusal": "I can't help"}}]})))
check("a refusal is reported with its reason", "I can't help" in (problem or ""), True)
_, problem, _ = run(Script(Reply(200, {"choices": [{"finish_reason": "stop", "message": {"content": "not json"}}]})))
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
