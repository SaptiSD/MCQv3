# MCQ V3

A new Streamlit implementation of the MCQ testing platform. V3 is independent
of the legacy Django application in `MCQv2`.

## Local setup

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
streamlit run app.py
```

The local demo login makes the complete teacher and student workflows usable
without OAuth credentials. SQLite data is stored in `data/mcq.db`.

For deployment, configure Google OIDC credentials through Streamlit secrets or
environment variables. Claude ingestion is optional; without `ANTHROPIC_API_KEY`
the built-in structured text parser is used.

## TODO

- [ ] Configure an email provider (e.g., SMTP) to send welcome emails on signup
      and password-reset emails for the "forgot password" flow.
