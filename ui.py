"""Shared visual system, navigation, and authentication entry points."""

from __future__ import annotations

from datetime import datetime, timezone

import streamlit as st

import server_state
from db import get_or_create_user
from repository import PortalMismatch, admin_by_email, authenticate, set_user_role, user_by_email


def styles() -> None:
    """The whole visual system. Loaded once per page render."""
    st.markdown("""
    <style>
    @import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700&family=Space+Grotesk:wght@500;600;700&display=swap');

    :root {
      --ink:#12211c; --body:#41544c; --muted:#6b7c74;
      --green:#12694f; --green-dark:#0b4c39; --green-soft:#e6f2ec; --green-line:#bcd9cb;
      --amber:#a8630f; --amber-soft:#fbf0dd;
      --red:#a4322a; --red-soft:#fbe9e7;
      --paper:#f6f9f7; --surface:#ffffff; --line:#dbe6e0; --line-soft:#eaf1ed;
      --shadow:0 1px 2px rgba(18,33,28,.05), 0 1px 3px rgba(18,33,28,.04);
      --shadow-lift:0 4px 14px rgba(18,33,28,.09);
      --radius:14px;
    }

    /* ---------- canvas ---------- */
    .stApp, [data-testid='stAppViewContainer'] { background:var(--paper); color:var(--body); font-family:'DM Sans',system-ui,sans-serif; }
    [data-testid='stHeader'] { background:transparent; }
    .block-container { max-width:1140px; padding-top:1.6rem; padding-bottom:4rem; }
    [data-testid='stMainBlockContainer'] { padding-top:1.6rem; }

    h1,h2,h3,h4 { font-family:'Space Grotesk',system-ui,sans-serif !important; color:var(--ink) !important; letter-spacing:-.01em !important; }
    h1 { font-size:2.5rem !important; line-height:1.1 !important; font-weight:700 !important; margin:0 0 .35rem !important; }
    h2 { font-size:1.55rem !important; font-weight:650 !important; }
    h3 { font-size:1.2rem !important; font-weight:650 !important; }
    p, li, label, [data-testid='stMarkdownContainer'] p { color:var(--body); }
    a { color:var(--green); }

    .eyebrow { color:var(--green); text-transform:uppercase; font-size:.72rem; font-weight:700; letter-spacing:.13em; margin-bottom:.35rem; }
    .lede { font-size:1.02rem; color:var(--muted); max-width:64ch; margin:.1rem 0 0; }
    .muted, [data-testid='stCaptionContainer'], [data-testid='stCaptionContainer'] p { color:var(--muted) !important; }
    hr, [data-testid='stDivider'] { border-color:var(--line-soft) !important; }

    /* ---------- top bar ---------- */
    .topbar { display:flex; align-items:center; gap:.8rem; padding:.1rem 0 1rem; border-bottom:1px solid var(--line); margin-bottom:1.1rem; }
    .topbar .mark { display:inline-flex; align-items:center; justify-content:center; min-width:38px; height:34px; padding:0 .5rem;
                    border-radius:10px; background:var(--green); color:#fff !important; font:700 .95rem 'Space Grotesk'; letter-spacing:.03em; }
    .topbar .name { font:700 1.15rem 'Space Grotesk'; color:var(--ink) !important; line-height:1.1; }
    .topbar .sub { display:block; font:500 .74rem 'DM Sans'; color:var(--muted) !important; letter-spacing:.02em; }
    .topbar .who { margin-left:auto; display:flex; align-items:center; gap:.5rem; font-size:.84rem; color:var(--body) !important; }
    .topbar .who .avatar { width:28px; height:28px; border-radius:50%; background:var(--green-soft); color:var(--green) !important;
                           display:inline-flex; align-items:center; justify-content:center; font:700 .78rem 'Space Grotesk'; }
    .topbar .who .role { color:var(--muted) !important; font-size:.78rem; }

    /* ---------- segmented nav ---------- */
    .st-key-workspace-nav [role='radiogroup'] { gap:.25rem !important; background:var(--surface); border:1px solid var(--line);
                                                border-radius:11px; padding:.25rem; box-shadow:var(--shadow); display:inline-flex; flex-wrap:wrap; }
    .st-key-workspace-nav [role='radiogroup'] > label { margin:0 !important; padding:.42rem .85rem !important; border-radius:8px;
                                                        cursor:pointer; transition:background .12s, color .12s; }
    .st-key-workspace-nav [role='radiogroup'] > label:hover { background:var(--green-soft); }
    .st-key-workspace-nav [role='radiogroup'] > label p { font-weight:600 !important; font-size:.88rem !important; color:var(--muted) !important; margin:0 !important; }
    .st-key-workspace-nav [data-testid='stRadioOption'][data-selected='true'] { background:var(--green); }
    .st-key-workspace-nav [data-testid='stRadioOption'][data-selected='true'] p { color:#fff !important; }
    .st-key-workspace-nav [data-testid='stRadioOption'][data-selected='true']:hover { background:var(--green-dark); }
    /* hide the radio dot: the nav reads as a segmented control, not a form */
    .st-key-workspace-nav [data-testid='stRadioOption'] > div > div > div:first-child { display:none !important; }
    .st-key-workspace-nav [data-testid='stRadioOption'] > div > div { gap:0 !important; }

    /* ---------- cards ---------- */
    [data-testid='stVerticalBlockBorderWrapper'] { background:var(--surface); border-radius:var(--radius) !important;
                                                   border-color:var(--line) !important; box-shadow:var(--shadow); }
    .panel { background:var(--surface); border:1px solid var(--line); border-radius:var(--radius); padding:1.25rem; margin:.4rem 0 1rem; box-shadow:var(--shadow); }

    /* ---------- metric tiles ---------- */
    .metric { background:var(--surface); border:1px solid var(--line); border-left:3px solid var(--green);
              border-radius:12px; padding:.85rem 1rem; min-height:88px; box-shadow:var(--shadow); }
    .metric strong { display:block; font:700 1.75rem 'Space Grotesk'; color:var(--ink) !important; line-height:1.15; }
    .metric small { display:block; color:var(--muted) !important; margin-top:.15rem; font-size:.78rem; font-weight:500; }

    /* ---------- pills ---------- */
    .pill { display:inline-block; padding:.16rem .6rem; border-radius:999px; font-size:.74rem; font-weight:700;
            letter-spacing:.02em; border:1px solid transparent; }
    .pill-green { background:var(--green-soft); color:var(--green-dark) !important; border-color:var(--green-line); }
    .pill-amber { background:var(--amber-soft); color:var(--amber) !important; border-color:#eeddbc; }
    .pill-red   { background:var(--red-soft);   color:var(--red) !important;   border-color:#f0cfcb; }
    .pill-grey  { background:#eef2f0;           color:var(--muted) !important; border-color:var(--line); }

    /* ---------- buttons ---------- */
    div.stButton > button, div[data-testid='stFormSubmitButton'] > button, [data-testid='stDownloadButton'] > button {
      background:var(--surface); color:var(--ink); border:1px solid var(--line); border-radius:9px;
      font-weight:600; font-size:.9rem; min-height:2.6rem; transition:background .12s, border-color .12s, box-shadow .12s, transform .06s; }
    div.stButton > button:hover, div[data-testid='stFormSubmitButton'] > button:hover, [data-testid='stDownloadButton'] > button:hover {
      background:var(--green-soft); color:var(--green-dark); border-color:var(--green-line); }
    div.stButton > button:active, div[data-testid='stFormSubmitButton'] > button:active { transform:translateY(1px); }
    div.stButton > button:focus-visible, div[data-testid='stFormSubmitButton'] > button:focus-visible {
      outline:none; box-shadow:0 0 0 3px rgba(18,105,79,.22); border-color:var(--green); }
    button[kind='primary'], [data-testid='stDownloadButton'] button[kind='primary'] {
      background:var(--green) !important; border-color:var(--green) !important; box-shadow:var(--shadow); }
    /* the label is a <p>, which would otherwise inherit the body text colour and vanish */
    button[kind='primary'], button[kind='primary'] p, button[kind='primary'] div, button[kind='primary'] span {
      color:#fff !important; }
    button[kind='primary']:hover { background:var(--green-dark) !important; border-color:var(--green-dark) !important; box-shadow:var(--shadow-lift); }
    button:not([kind='primary']) p { color:var(--ink) !important; font-weight:600; }
    /* emails and other auto-linked text inside captions should read as text, not links */
    [data-testid='stCaptionContainer'] a { color:inherit !important; text-decoration:none !important; }
    div.stButton > button:disabled, div[data-testid='stFormSubmitButton'] > button:disabled { opacity:.5; }

    /* ---------- inputs ---------- */
    [data-testid='stWidgetLabel'] p { font-weight:600 !important; font-size:.85rem !important; color:var(--ink) !important; }
    input, textarea, [data-baseweb='select'] > div, [data-baseweb='input'] > div, [data-baseweb='textarea'] > div {
      background:var(--surface) !important; color:var(--ink) !important; border-color:var(--line) !important; border-radius:9px !important; }
    input, textarea { font-size:.94rem !important; }
    input::placeholder, textarea::placeholder { color:#93a29b !important; opacity:1 !important; }
    [data-baseweb='input'] > div:focus-within, [data-baseweb='textarea'] > div:focus-within, [data-baseweb='select'] > div:focus-within {
      border-color:var(--green) !important; box-shadow:0 0 0 3px rgba(18,105,79,.15) !important; }
    [data-testid='stNumberInput'] button { border-color:var(--line) !important; }

    /* ---------- choice controls inside a quiz ---------- */
    [data-testid='stRadio'] [role='radiogroup'] > label, [data-testid='stCheckbox'] label { color:var(--ink) !important; }
    [data-testid='stRadio'] [role='radiogroup'] > label { border-radius:9px; padding:.3rem .5rem; transition:background .12s; }
    [data-testid='stRadio'] [role='radiogroup'] > label:hover { background:var(--green-soft); }
    [data-baseweb='radio'] div[aria-checked='true'], [data-baseweb='checkbox'] span[aria-checked='true'] { background:var(--green) !important; border-color:var(--green) !important; }

    /* ---------- tables, tabs, expanders ---------- */
    [data-testid='stDataFrame'] { border:1px solid var(--line); border-radius:11px; overflow:hidden; box-shadow:var(--shadow); }
    [data-testid='stExpander'] { border:1px solid var(--line) !important; border-radius:11px !important; box-shadow:var(--shadow); background:var(--surface); }
    [data-testid='stExpander'] summary p { font-weight:600 !important; color:var(--ink) !important; }
    [data-testid='stTabs'] button { color:var(--muted) !important; font-weight:600 !important; }
    [data-testid='stTabs'] button[aria-selected='true'] { color:var(--green) !important; }
    [data-testid='stTabs'] [data-baseweb='tab-highlight'] { background:var(--green) !important; }

    /* ---------- feedback ---------- */
    [data-testid='stAlert'] { border-radius:11px; border:1px solid var(--line); }
    .stProgress > div > div > div { background:var(--green) !important; }
    [data-testid='stProgress'] p { color:var(--muted) !important; font-size:.82rem; }

    /* ---------- quiz-taking surface ---------- */
    .timer { display:flex; align-items:center; gap:.55rem; background:var(--green-soft); border:1px solid var(--green-line);
             border-radius:11px; padding:.6rem .9rem; font-weight:600; color:var(--green-dark) !important; }
    .timer.low { background:var(--red-soft); border-color:#f0cfcb; color:var(--red) !important; }
    .timer .clock { font:700 1.15rem 'Space Grotesk'; font-variant-numeric:tabular-nums; }

    /* ---------- empty states ---------- */
    .empty { text-align:center; padding:2.4rem 1rem; border:1px dashed var(--line); border-radius:var(--radius); background:var(--surface); }
    .empty h3 { margin:0 0 .3rem !important; }
    .empty p { color:var(--muted) !important; margin:0 auto; max-width:44ch; }

    /* ---------- login ---------- */
    .hero { padding:2.4rem 0 1.4rem; max-width:720px; }
    .hero h1 { font-size:2.9rem !important; }

    /* ---------- readable prose (instructions) ---------- */
    .prose { max-width:70ch; }
    .prose h2 { margin-top:2rem !important; padding-top:.9rem; border-top:1px solid var(--line-soft); }
    .prose h3 { margin-top:1.3rem !important; }
    .prose li { margin-bottom:.3rem; }
    .prose code { background:var(--green-soft); color:var(--green-dark); padding:.08rem .34rem; border-radius:5px; font-size:.86em; }

    @media (max-width:640px) {
      /* metric tiles go two-up rather than one long column */
      [data-testid='stHorizontalBlock']:has(.metric) { flex-wrap:wrap !important; gap:.5rem !important; }
      [data-testid='stHorizontalBlock']:has(.metric) > [data-testid='stColumn'] {
        flex:1 1 calc(50% - .5rem) !important; min-width:calc(50% - .5rem) !important; width:auto !important; }
      .metric { min-height:76px; padding:.7rem .8rem; }
      .metric strong { font-size:1.45rem; }
      h1 { font-size:1.9rem !important; }
      .hero h1 { font-size:2.1rem !important; }
      .block-container { padding-left:1rem; padding-right:1rem; }
      .topbar .who .role { display:none; }
    }
    @media print { .topbar, .st-key-workspace-nav, [data-testid='stDownloadButton'] { display:none !important; } }
    </style>
    """, unsafe_allow_html=True)


def when(timestamp) -> str:
    """Format a stored UTC timestamp for display in the reader's local time."""
    if not timestamp or timestamp == "-":
        return "-"
    try:
        moment = datetime.fromisoformat(str(timestamp))
    except ValueError:
        return str(timestamp)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone().strftime("%b %d, %I:%M %p").replace(" 0", " ")


def flash(slot: str, message: str) -> None:
    """Queue a confirmation to show after the rerun that follows an action.

    `st.success(...)` immediately before `st.rerun()` never reaches the screen:
    the rerun throws away the half-drawn page, message and all. Stashing it here
    and drawing it on the way back in is what makes the confirmation visible.
    `slot` keeps each section's message next to the control that produced it, so
    a confirmation never lands in a part of the page the reader isn't looking at.
    """
    st.session_state[f"_flash-{slot}"] = message


def show_flash(slot: str) -> None:
    if message := st.session_state.pop(f"_flash-{slot}", None):
        st.success(message)


def page_header(eyebrow: str, title: str, lede: str = "") -> None:
    """The standard heading block at the top of every page."""
    tail = f'<p class="lede">{lede}</p>' if lede else ""
    st.markdown(f'<div class="eyebrow">{eyebrow}</div><h1>{title}</h1>{tail}', unsafe_allow_html=True)


def metric_row(items: list[tuple], per_row: int = 4) -> None:
    """A grid of number-and-label tiles."""
    for start in range(0, len(items), per_row):
        chunk = items[start:start + per_row]
        for column, (value, label) in zip(st.columns(per_row), chunk):
            with column:
                st.markdown(f'<div class="metric"><strong>{value}</strong><small>{label}</small></div>', unsafe_allow_html=True)


def pill(text: str, tone: str = "grey") -> str:
    """Inline status badge. Returns markup so it can sit inside another string."""
    return f'<span class="pill pill-{tone}">{text}</span>'


def empty_state(title: str, body: str) -> None:
    st.markdown(f'<div class="empty"><h3>{title}</h3><p>{body}</p></div>', unsafe_allow_html=True)


ROLE_LABELS = {"teacher": "teacher", "student": "student", "admin": "administrator"}


def _oidc_configured() -> bool:
    try:
        return "auth" in st.secrets
    except Exception:
        return False


def _login_panel(side: str, eyebrow: str, heading: str, blurb: str, google_ready: bool) -> None:
    """One half of the split login. `side` is "teacher" or "student"."""
    with st.container(border=True):
        st.markdown(f'<div class="eyebrow">{eyebrow}</div><h3>{heading}</h3>', unsafe_allow_html=True)
        st.caption(blurb)
        with st.form(f"login-{side}"):
            identifier = st.text_input("Email or full name", key=f"login-id-{side}")
            password = st.text_input("Password", type="password", key=f"login-pw-{side}")
            submitted = st.form_submit_button("Sign in", type="primary", width="stretch")
        if submitted:
            try:
                user = authenticate(identifier, password, expected_role=side)
            except PortalMismatch as mismatch:
                other = "Students" if mismatch.role == "student" else "Teachers & administrators"
                st.error(f"That's {'an' if mismatch.role == 'admin' else 'a'} {ROLE_LABELS[mismatch.role]} account — sign in under **{other}**.")
            else:
                if user is None:
                    st.error("No account matches that name, email, or password.")
                else:
                    st.session_state.user = user
                    # Adopt the account's current epoch. Signing in deliberately
                    # does *not* bump it: a teacher working in two tabs signs in
                    # twice and should keep both. Only signing out invalidates.
                    server_state.stamp_session(user)
                    st.rerun()
        if google_ready:
            if st.button("Continue with Google", key=f"google-{side}", width="stretch"):
                st.session_state.signup_role = side
                st.login("google")
            st.caption(f"New here? Signing in with Google from this side creates a {ROLE_LABELS[side]} account.")


def login_page() -> None:
    st.markdown(
        '<div class="hero"><div class="eyebrow">MCQ / assessment studio</div>'
        '<h1>Make every question count.</h1>'
        '<p class="lede">A focused place for teachers to build quizzes and for students to take them — '
        'multiple choice, true/false, and typed answers marked automatically.</p></div>',
        unsafe_allow_html=True,
    )
    google_ready = _oidc_configured()
    teacher_side, student_side = st.columns(2, gap="large")
    with teacher_side:
        _login_panel(
            "teacher", "Teachers & administrators", "Build and review",
            "Create assessments, manage your roster, and read the results. Administrators sign in here too.",
            google_ready,
        )
    with student_side:
        _login_panel(
            "student", "Students", "Take your assessments",
            "Open the tests your teachers assigned and see how you did.",
            google_ready,
        )
    if not google_ready:
        st.caption("Google sign-in can be connected through Streamlit secrets for deployment.")


def choose_role_page() -> None:
    """Shown once when a Google sign-in arrives without a known role (e.g. the redirect dropped it)."""
    name = (getattr(st.user, "name", "") or "").split()[0] if getattr(st.user, "name", "") else "there"
    st.markdown(f'<div class="eyebrow">One quick thing</div><h1>Welcome, {name}.</h1>', unsafe_allow_html=True)
    st.caption("Tell us how you'll use MCQ and we'll set your workspace up right away.")
    teacher_side, student_side = st.columns(2, gap="large")
    with teacher_side:
        with st.container(border=True):
            st.markdown("<h3>I'm a teacher</h3>", unsafe_allow_html=True)
            st.caption("Build assessments, manage a roster, and review results.")
            if st.button("Continue as a teacher", key="role-pick-teacher", type="primary", width="stretch"):
                st.session_state.signup_role = "teacher"
                st.rerun()
    with student_side:
        with st.container(border=True):
            st.markdown("<h3>I'm a student</h3>", unsafe_allow_html=True)
            st.caption("Take the assessments your teachers assign you.")
            if st.button("Continue as a student", key="role-pick-student", type="primary", width="stretch"):
                st.session_state.signup_role = "student"
                st.rerun()
    st.divider()
    if st.button("Sign out", key="role-pick-sign-out"):
        sign_out()


def workspace_nav(user, selected_page: str | None = None) -> str:
    name = user.get("name") or user.get("email", "")
    initials = "".join(part[0] for part in name.split()[:2]).upper() or "?"
    st.markdown(
        f"<div class='topbar'><span class='mark'>MCQ</span>"
        f"<span><span class='name'>Assessment studio</span>"
        f"<span class='sub'>Quizzes for teachers and students</span></span>"
        f"<span class='who'><span class='avatar'>{initials}</span>"
        f"<span>{name}<br><span class='role'>{user['role'].title()}</span></span></span></div>",
        unsafe_allow_html=True,
    )
    if user["role"] == "teacher":
        pages = ["Dashboard", "Create quiz", "Students", "Analytics", "Student view", "Guide"]
    elif user["role"] == "admin":
        pages = ["Dashboard", "Guide"]
    else:
        pages = ["Dashboard", "My teachers", "Guide"]
    nav, sign_out_col = st.columns([8, 1], vertical_alignment="center")
    with nav:
        default_page = selected_page if selected_page in pages else pages[0]
        if selected_page and selected_page in pages:
            st.session_state["workspace-nav"] = selected_page
            default_page = selected_page
        elif st.session_state.get("create_nav_guard") and user["role"] == "teacher" and st.session_state.get("current_page") == "Create quiz":
            st.session_state["workspace-nav"] = "Create quiz"
            default_page = "Create quiz"
        if len(pages) > 1:
            page = st.radio("Workspace", pages, index=pages.index(default_page), horizontal=True, key="workspace-nav", label_visibility="collapsed")
        else:
            page = pages[0]
    with sign_out_col:
        if st.button("Sign out", key="top-sign-out", width="stretch"):
            if user["role"] == "teacher" and st.session_state.get("current_page") == "Create quiz" and st.session_state.get("create_dirty") and not st.session_state.get("create_nav_guard"):
                st.session_state.create_nav_guard = True
                st.session_state.create_nav_request = "Sign out"
                st.rerun()
            sign_out()
    return page


@st.dialog("Discard unsaved changes?")
def confirm_discard_dialog() -> None:
    st.write("You have an assessment in progress. Your questions and settings **won't be saved** if you leave now.")
    leave, cancel = st.columns(2)
    if leave.button("Continue", key="confirm-discard-leave", type="primary", width="stretch"):
        target = st.session_state.pop("create_nav_request", None)
        st.session_state.pop("create_nav_guard", None)
        st.session_state.pop("create_dirty", None)
        if target == "Sign out":
            sign_out()
            return
        if target:
            st.session_state.page_override = target
        st.rerun()
    if cancel.button("Cancel", key="confirm-discard-cancel", width="stretch"):
        st.session_state.pop("create_nav_guard", None)
        st.session_state.pop("create_nav_request", None)
        st.rerun()


def google_user():
    """Resolve the signed-in Google identity to a session, creating the account on first sign-in.

    New accounts get their role from the login panel the person came in through,
    so nobody waits for an administrator to approve them. If the OAuth round trip
    lost that choice, `main()` falls back to `choose_role_page()`.
    """
    if not getattr(st.user, "is_logged_in", False):
        return None
    email = (st.user.email or "").lower()
    if not email:
        return None
    admin = admin_by_email(email)
    if admin:
        return admin
    existing = user_by_email(email)
    if existing and existing["role"] in ("teacher", "student"):
        session = dict(existing)
        session.pop("password", None)
        return session
    role = st.session_state.get("signup_role")
    if role not in ("teacher", "student"):
        return None
    name = st.user.name or email.split("@")[0]
    if existing:
        set_user_role(existing["id"], role)
        session = {**existing, "role": role}
        session.pop("password", None)
        return session
    session = dict(get_or_create_user(email, name, role))
    session.pop("password", None)
    return session


PRESERVED_KEYS = {"signup_role"}


def reset_session() -> None:
    """Drop per-user state so the next sign-in never inherits the last person's work."""
    for key in [key for key in st.session_state if key not in PRESERVED_KEYS]:
        st.session_state.pop(key, None)


def sign_out() -> None:
    google_session = getattr(st.user, "is_logged_in", False)
    user = st.session_state.get("user")
    if user:
        # Every other tab signed in to this account is invalidated too.
        server_state.revoke(server_state.account_key(user))
    reset_session()
    st.session_state.pop("signup_role", None)
    if google_session:
        st.logout()
    else:
        st.rerun()


def enforce_session(user) -> bool:
    """Sign this tab out if the account was signed out (or re-signed-in) elsewhere.

    Returns True when the tab is still valid. When it isn't, the session is
    cleared and the login page is shown with a short explanation, so a stale tab
    can't keep using protected pages until someone happens to refresh it.
    """
    if not server_state.session_is_stale(user):
        return True
    reset_session()
    st.session_state["signed_out_elsewhere"] = True
    st.rerun()
    return False


def signed_out_elsewhere_notice() -> None:
    if st.session_state.pop("signed_out_elsewhere", False):
        st.warning("You were signed out because this account was signed out (or signed in again) in another tab or window.")


def warn_before_leaving(active: bool) -> None:
    """Ask the browser to confirm before a refresh or Back throws away unsaved work.

    Streamlit hands us an iframe, so the handler is installed on the parent
    document. The browser shows its own generic wording; the text below is only
    a fallback for very old engines.
    """
    from streamlit.components.v1 import html

    state = "true" if active else "false"
    html(
        f"""
        <script>
        (function () {{
          const parentWindow = window.parent;
          if (!parentWindow) return;
          if (parentWindow.__mcqUnloadGuard === undefined) {{
            parentWindow.__mcqUnloadGuard = false;
            parentWindow.addEventListener('beforeunload', function (event) {{
              if (!parentWindow.__mcqUnloadGuard) return;
              event.preventDefault();
              event.returnValue = 'You have an assessment in progress that has not been published yet.';
              return event.returnValue;
            }});
          }}
          parentWindow.__mcqUnloadGuard = {state};
        }})();
        </script>
        """,
        height=0,
    )
