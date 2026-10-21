# MCQ V3

A Streamlit app for setting and taking multiple-choice and short-answer
assessments, backed by Supabase. Independent of the legacy Django app in `MCQv2`.

## Local setup

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
streamlit run app.py
```

Supabase credentials come from `SUPABASE_URL` / `SUPABASE_SERVICE_ROLE_KEY`
environment variables, or the `[supabase]` section of `.streamlit/secrets.toml`
(gitignored). Copy `.streamlit/example.secrets.toml` to start one: it lists every
setting the app reads, including Google sign-in and the optional OpenAI section.
The schema lives in `supabase/schema.sql`.

> The local setup points at the **live** database. Prefix anything you create
> while testing so you can find and delete it afterwards.

## Signing in

The login page has two panels — **Teachers & administrators** and **Students**.
Credentials are scoped to their panel: a student account signing in on the
teacher side is told to use the other one, and vice versa. Admins sign in on the
teacher side and land in the admin console.

**Everyone signs in with Google.** There are no MCQ passwords on the live site:
every student and teacher has a school Google account. Signing in from a panel
creates that kind of account immediately; if the OAuth round trip loses which
panel was used, the app asks once and creates the account on the spot. An
account a teacher or admin adds by email is found by Google sign-in the first
time that person arrives.

The email-and-password form survives only for testing: it is drawn when
`[app] allow_password_login = true` is set in `secrets.toml`, or when no Google
client is configured at all, and it is labelled *Local testing only*. Never set
the flag on the live host.

**Students must choose a teacher before they can do anything.** A student with
no teacher sees a blocking picker, searches for their teacher by name or email,
and on joining picks up every assessment that teacher had already given the
whole class. They can add or leave teachers later from **My teachers**.

Where passwords are used (local testing), they are stored as salted
PBKDF2-SHA256 hashes. Rows written before that change are still plaintext; they
are verified as-is and rehashed on that account's next successful sign-in.

## Activating an assessment

A published assessment is **inactive** until its teacher presses **Activate** on
the dashboard; only then does it reach assigned students, within its opening and
closing times. **Deactivate** hides it again without touching any attempt. Times
are read and shown in the class's zone, `[app] timezone` (default
`America/Chicago`), not the server's.

## Question types

`Multiple choice`, `Multiple choice - select all that apply`, `True / False`,
`Fill in the blank` and `Short answer`. The last two are typed: the student
gets a text box and the answer is marked on the server, so it never reaches the
browser.

Typed questions carry an answer specification (`grading.py`), stored as JSON in
`questions.correct_label`. Legacy rows that kept the answer in `options_json`
are still read correctly.

- **Number** answers compare values, so `6`, `6.0`, `+6` and `12/2` all match `6`.
  The tolerance comes from how precisely the teacher wrote the answer: a whole
  number demands an exact value, `33.33` accepts ±0.005. Teachers can override it.
- **Text** answers ignore capitals, surrounding spaces and trailing punctuation,
  accept a list of alternatives, and can optionally forgive one-letter typos.

`python test_grading.py` covers the rules, including the `2 * 3` and `100 / 3`
cases.

## Uploading a question bank

A `.txt` or `.docx` file is read by `ingestion.py`, in document order and with
its formatting, so it understands more than the numbered-questions-plus-answer-key
layout: unnumbered questions (a Moodle quiz pasted into Word), Aiken-style
`ANSWER: B` lines, and the right option marked in place by a tick, highlight,
bold, colour, `(correct)` or an asterisk. Everything read lands in a review
table; a question whose answer the file does not give is kept with a blank
answer, and the quiz will not publish until it is filled in.

Each row of the review table has an **Add?** box; untick it to leave that question
out.

Tables in a `.docx` become the quiz's **Description/Context** (called case
material in the code and `quizzes.case_material` in the database) — the passage or data
the questions refer to — shown to students above the questions and printed at
the top of the PDF and DOCX exports. It lives in `quizzes.case_material`; an
existing database needs that column added once (the statement is in
`supabase/schema.sql`). Until it is, quizzes without case material publish as
before and anything that would save some says what to run.

### Reading with AI (optional)

For files the rule-based reader cannot follow, **Read with AI** sends the file's
text to OpenAI (`ai_reader.py`). It appears only when a key is configured:

```toml
# .streamlit/secrets.toml
[openai]
api_key = "..."
# optional; tried in order, and retired models fall through to the next
models = ["gpt-5.4-mini", "gpt-4.1-mini"]
```

The model transcribes and never answers: an answer it cannot point to in the
file is dropped, and the table read straight out of the `.docx` is kept in
preference to the model's copy of it. Don't send files with students' personal
data.

## Documentation

`guide.py` holds the teacher and student guides. The same text is the in-app
**Guide** page, the PDF download on that page, and the files in `instructions/`.
Regenerate the handouts with:

```powershell
python instructions/build_instructions.py
```

## TODO

- [ ] Configure an email provider (e.g., SMTP) to send welcome emails on signup.
