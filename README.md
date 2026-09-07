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
(gitignored). The schema lives in `supabase/schema.sql`.

> The local setup points at the **live** database. Prefix anything you create
> while testing so you can find and delete it afterwards.

## Signing in

The login page has two panels — **Teachers & administrators** and **Students**.
Credentials are scoped to their panel: a student account signing in on the
teacher side is told to use the other one, and vice versa. Admins sign in on the
teacher side and land in the admin console.

There is no approval queue. Signing in with Google from a panel creates that
kind of account immediately; if the OAuth round trip loses which panel was used,
the app asks once and creates the account on the spot.

**Students must choose a teacher before they can do anything.** A student with
no teacher sees a blocking picker, searches for their teacher by name or email,
and on joining picks up every assessment that teacher had already given the
whole class. They can add or leave teachers later from **My teachers**.

Passwords are stored as salted PBKDF2-SHA256 hashes. Rows written before that
change are still plaintext; they are verified as-is and rehashed on that
account's next successful sign-in.

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

## Documentation

`guide.py` holds the teacher and student guides. The same text is the in-app
**Guide** page, the PDF download on that page, and the files in `instructions/`.
Regenerate the handouts with:

```powershell
python instructions/build_instructions.py
```

## TODO

- [ ] Configure an email provider (e.g., SMTP) to send welcome emails on signup
      and password-reset emails for the "forgot password" flow.
- [ ] Students added to a roster by a teacher have no password, so they can only
      sign in with Google until the invite flow above exists.
