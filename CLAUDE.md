# MCQ V3 — working notes

A Streamlit app for setting and taking multiple-choice and short-answer
assessments, backed by Supabase. Read `README.md` for the product description;
this file is for working *on* it.

## Running it

Two launch configs in `.claude/launch.json`. **Never start a dev server with
Bash** — use the Browser pane's `preview_start`.

| Config | Port | For |
| --- | --- | --- |
| `mcq` | 8511 | the human's own instance — leave it alone |
| `mcq-test` | 8512 | agent/automated testing |

A `@st.fragment(run_every=...)` never re-executes the main script body, so
Streamlit does **not** reload changed modules inside one. After editing
`student_portal.take_attempt` or anything it calls, restart the server
(`preview_stop` then `preview_start`) — a browser refresh is not enough, and a
tab left open will keep running the old code and produce phantom failures.

## Test accounts

Seeded by `db.seed_demo_data()` on startup. All on the live database.

| Role | Email | Password |
| --- | --- | --- |
| Teacher | `teacher@mcq.local` | `teacher` |
| Student | `student@mcq.local` | `student` |
| Admin | `admin@mcq.local` | `admin` |

Teachers and admins sign in on the **left** panel, students on the **right**.
Credentials are scoped to their panel and the app says so if you pick wrong.

**Real people use this database.** Owner id 17 is a real tester; other rows are
real accounts. Prefix anything you create with `ZZTEST` and delete it when you
are done. Never sign in as, edit, or delete another person's data.

## Tests

Plain scripts, no pytest. Each prints `  pass ` / `  FAIL ` lines and exits
non-zero on failure.

```bash
for t in test_*.py; do .venv/Scripts/python.exe $t; done
```

| File | Covers |
| --- | --- |
| `test_grading.py` | typed-answer marking, number tolerance, typo forgiveness |
| `test_question_bank.py` | question parsing and defaults |
| `test_db_retry.py` | the httpx transport retry in `db.py` |
| `test_admin_edit_form.py` | admin edit form following the account picker |
| `test_editor_state.py` | editor state, uploads, publish claims |
| `test_attempt_sync.py` | quiz/attempt drift — fingerprints, resync, re-keying |

`.venv/Scripts/python.exe -m pyflakes *.py` should print nothing.

## Layout

```
app.py              shell: startup, auth, routing only
ui.py               visual system, nav, login, session enforcement
teacher_portal.py   dashboard, quiz builder, question bank, analytics, roster
student_portal.py   student dashboard and the attempt-taking fragment
admin_portal.py     admin console
repository.py       all data access (PostgREST). No SQL in UI modules.
db.py               Supabase client, resilient transport, demo seed
grading.py          answer specs + marking. Pure.
attempt_sync.py     keeping an attempt in step with its quiz. Pure.
ingestion.py        question-bank file upload parsing
server_state.py     state outliving one browser tab (sign-out epochs,
                    draft mirrors, publish claims)
guide.py            teacher/student guide — also the PDF/DOCX handouts
```

Dependency direction: `grading` ← `attempt_sync` ← `repository` ← portals.
Keep `attempt_sync` and `grading` free of Streamlit and of the database.

## Invariants worth not breaking

**A quiz's paper is fixed once a real student starts it; its answer key is not.**
This is the same rule the settings have always followed (`settings_editor`), and
it is the reason most of the "a teacher edited mid-attempt" reports cannot happen
any more rather than being reconciled after the fact. Enforced in
`repository.save_question_bank` -- not in the editor, because a disabled widget is
only a rendering decision -- by comparing `bank_fingerprint(..., include_key=False)`
before and after. A teacher's own preview attempts do not count. The way out of a
genuinely wrong paper is to delete the assessment and publish a new one.
Question *order* is part of the paper and locks with it -- `move_question` raises
`PAPER_IS_FIXED` -- because with randomisation off the teacher's order is
literally the order on the student's screen.

**Unassigning a student ends any attempt they still have open.**
`set_quiz_assignments` deletes their unsubmitted attempts for that quiz.
Otherwise the paper stays submittable from their still-loaded page and the result
lies dormant until the quiz is assigned back, when it surfaces as a completed
attempt nobody remembers. Submitted attempts are deliberately untouched: a
teacher must not be able to erase a real result by unassigning someone.

**A frozen question remembers its `position`, and that is its identity.** A quiz
may legitimately ask the same thing twice and want a different answer each time,
so wording does not identify a question -- matching on it marked both twins from
the first one's key and handed a student 100% on a paper where one of their two
identical answers was wrong. `attempt_sync.live_match` anchors on position and
falls back to wording only when the wording is unique; for anything ambiguous it
returns `None` on purpose, and the caller keeps the key it already had. Positions
survive an answer-key save (`save_question_bank` re-numbers from `enumerate`) and
reordering is locked once a student starts, so the anchor holds.

**An attempt freezes its questions.** `attempts.answers_json` holds
`{"questions": [...], "answers": {...}, "revision": n}`. The frozen copy is why
each student gets a differently shuffled paper and why a typed question's answer
specification never reaches the browser. It also drifts the moment a teacher
edits the quiz, which is what `attempt_sync` exists to manage:

- Questions and options are matched on their **text**, never their position or
  letter. A, B, C, D are fixed slots a teacher reuses for different content.
- A **reworded** question counts as a new one. Nobody can tell a typo fix from a
  different question, and treating it as new costs one answer rather than
  crediting the wrong one.
- Two fingerprints, and the difference matters. `include_key=True` answers "is
  this attempt marking against the right answers?"; `include_key=False` answers
  "would the student notice?" -- which is also exactly the line between the fixed
  paper and the correctable key, so it is what `save_question_bank` enforces.
- The student-side "Load the updated version" flow is a **backstop**, not a
  feature. With the paper fixed it should never appear; it exists for attempts
  older than the rule and for drift introduced outside the app.
- A question bank that reads back **empty** is a half-finished save, not an
  empty quiz. Never sync an attempt to it.

**Marking uses the key as it stands at submission.** `submit_attempt` refreshes
the key first, so a student's score and a later Regrade can never disagree.

**`revision` only goes up.** Every save bumps it; each tab remembers the last it
saw. That is how a tab that has been overtaken by another one is recognised.

**A widget key outranks the `value=` / `index=` / `default=` it is re-rendered
with.** This is the single most productive bug in the codebase and it has bitten
four separate screens. When a button writes to the database and reruns, any
widget showing that same data keeps its pre-write value, the screen starts
contradicting itself, and the next Save writes the stale value back. The cure is
always to *rename* the widget, never to delete its key:

- attempts use `q-{attempt}-{paper}-{paint}-{index}`
- the question editor bumps `editor_epoch(quiz_id)`
- team membership bumps `team-epoch-{team_id}`

Deleting the key instead looks equivalent and is not: Streamlit fires the deleted
widget's `on_change`, and that callback then writes its own stale copy back.

**Option letters are data, not decoration.** A, B, C, D are what the answer key
points at, so any round trip through text -- the upload review table especially --
has to preserve them. Re-lettering by position moves the key on to whatever lands
in that slot, and validation cannot catch it because the relabelled set always
contains the key.

**Compare option and question text with `grading.normalise_text`.** Every matcher
in the app uses it; anything weaker (`.strip().casefold()`) lets through two
options that marking cannot tell apart, and which of them wins then depends on
the shuffle.

**Escape user text before it enters markup.** Streamlit escapes HTML everywhere
except `unsafe_allow_html=True`, and most headings here are drawn that way. Put
anything a person typed through `ui.text()`.

**Writes are not retried.** `db.py` retries only requests whose connection never
opened. Prefer idempotent writes (upsert, add-missing/drop-leftover) over
delete-then-insert, which also races and briefly leaves rows missing.

## Conventions

- **Line endings are per-file and mixed.** `repository.py`, `grading.py`,
  `guide.py`, `db.py`, `server_state.py`, `attempt_sync.py` and most tests are
  LF; `teacher_portal.py`, `student_portal.py`, `ui.py`, `app.py`,
  `admin_portal.py`, `ingestion.py`, `test_editor_state.py` are CRLF. Scripted
  edits on Windows silently convert LF files to CRLF and turn the diff into the
  whole file. Open with `newline=""` and check `git diff --stat` afterwards.
- Comments explain *why*, usually naming the failure that motivated the code.
  Match that: a comment restating the line above it is noise.
- No new dependencies without a reason; `requirements.txt` is deliberately short.

## Reporting a bug

The project's tester files bugs in this shape, and fixes are judged against it.
Use the same when reporting one:

```
MCQ-BUG-0NN — One line naming the wrong behaviour
Bug Description   what happens, in prose
Steps to Reproduce  numbered, from a signed-out browser
Expected Behavior   what should happen
Observed Behavior   what did
Impact              who is hurt and how
```

Numbers are global across reports and currently run to MCQ-BUG-025; check the
last commit for where the sequence has got to. A finding is not a bug until it has been **reproduced**. Read-only code review
produces plausible-looking claims that turn out to be guarded three lines up;
confirm against the running app at :8512 or against the database before fixing,
and say plainly when something could not be reproduced.
