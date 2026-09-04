"""Shared visual system, navigation, and authentication entry points."""

from __future__ import annotations

import os

import streamlit as st

from db import get_or_create_user


def styles() -> None:
    st.markdown("""
    <style>
    @import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700&family=Space+Grotesk:wght@500;600;700&display=swap');
    :root { --ink:#15231f; --muted:#53635d; --green:#176b52; --green-dark:#0e513d; --orange:#c75b32; --paper:#f7faf7; --surface:#ffffff; --line:#c9d8d0; --soft:#e8f3ed; }
    .stApp, [data-testid='stAppViewContainer'] { background:var(--paper); color:var(--ink); font-family:'DM Sans',sans-serif; }
    [data-testid='stHeader'] { background:var(--paper); }
    h1,h2,h3 { font-family:'Space Grotesk',sans-serif !important; letter-spacing:0 !important; color:var(--ink) !important; }
    h1 { font-size:2.8rem !important; line-height:1.05 !important; }
    .block-container { max-width:1180px; padding-top:2rem; }
    .top-brand { display:flex; align-items:center; gap:.75rem; padding:.25rem 0 1.15rem; border-bottom:1px solid var(--line); margin-bottom:1rem; }
    .top-brand strong { font:700 1.65rem 'Space Grotesk'; color:var(--green) !important; }
    .top-brand span { color:var(--muted) !important; font-size:.9rem; }
    .top-brand .top-user { margin-left:auto; font-size:.82rem; }
    .eyebrow { color:var(--green); text-transform:uppercase; font-size:.75rem; font-weight:700; letter-spacing:.12em; }
    .hero { padding:3.5rem 0 2rem; max-width:760px; }
    .hero p { font-size:1.15rem; color:var(--muted); max-width:650px; }
    .panel { background:var(--surface); border:1px solid var(--line); border-radius:14px; padding:1.4rem; margin:.5rem 0 1rem; }
    .metric { background:var(--soft); border:1px solid #d2e5db; border-radius:12px; padding:1rem; min-height:108px; }
    .metric strong { display:block; font:700 2rem 'Space Grotesk'; color:var(--green); }
    .metric small { display:block; color:var(--muted); margin-top:.2rem; }
    .muted, [data-testid='stCaptionContainer'], [data-testid='stWidgetLabel'] p { color:var(--muted) !important; }
    div.stButton > button, div[data-testid='stFormSubmitButton'] > button, [data-testid='stDownloadButton'] > button { background:var(--surface); color:var(--ink); border-radius:8px; font-weight:600; border:1px solid var(--line); min-height:2.6rem; }
    div.stButton > button:hover, div[data-testid='stFormSubmitButton'] > button:hover, [data-testid='stDownloadButton'] > button:hover { background:var(--soft); color:var(--green-dark); border-color:var(--green); }
    div.stButton > button[kind='primary'], div[data-testid='stFormSubmitButton'] > button[kind='primary'] { background:var(--green); color:#fff; border-color:var(--green); }
    div.stButton > button[kind='primary']:hover, div[data-testid='stFormSubmitButton'] > button[kind='primary']:hover { background:var(--green-dark); color:#fff; border-color:var(--green-dark); }
    input, textarea, [data-baseweb='select'] > div, [data-baseweb='input'] > div { background:var(--surface) !important; color:var(--ink) !important; border-color:var(--line) !important; }
    input::placeholder, textarea::placeholder { color:#718079 !important; opacity:1 !important; }
    input:focus, textarea:focus, [data-baseweb='select'] > div:focus-within, [data-baseweb='input'] > div:focus-within { border-color:var(--green) !important; box-shadow:0 0 0 1px var(--green) !important; }
    [data-testid='stRadio'] label, [data-testid='stRadio'] p, [data-testid='stCheckbox'] label, [data-testid='stMultiSelect'] label, [data-testid='stSelectbox'] label { color:var(--ink) !important; }
    [data-testid='stRadio'] [role='radiogroup'] > div { border-radius:8px; }
    [data-testid='stRadio'] [role='radiogroup'] > div:hover { background:var(--soft); }
    [data-testid='stRadio'] [role='radiogroup'] label { font-weight:500; }
    [data-testid='stRadio'] [data-baseweb='radio'] div, [data-testid='stCheckbox'] [data-baseweb='checkbox'] div { border-color:var(--green) !important; }
    [data-testid='stDataFrame'] { border:1px solid var(--line); border-radius:10px; overflow:hidden; }
    [data-testid='stDataFrame'] button { color:var(--ink) !important; background:var(--surface) !important; }
    [data-testid='stTabs'] button { color:var(--muted) !important; }
    [data-testid='stTabs'] button[aria-selected='true'] { color:var(--green) !important; }
    .stProgress > div > div { background:var(--orange); }
    </style>
    """, unsafe_allow_html=True)


def login_page() -> None:
    st.markdown('<div class="hero"><div class="eyebrow">MCQ / testing platform</div><h1>Make every question count.</h1><p>A focused place for teachers to shape assessments and students to take them with confidence.</p></div>', unsafe_allow_html=True)
    left, right = st.columns(2, gap="large")
    with left:
        st.markdown('<div class="panel"><div class="eyebrow">For educators</div><h2>Build a better quiz</h2><p class="muted">Upload a question bank, set the rules, and assign it to exactly the students who need it.</p></div>', unsafe_allow_html=True)
        if st.button("Enter teacher workspace", type="primary", width="stretch"):
            st.session_state.user = dict(get_or_create_user("teacher@mcq.local", "Avery Morgan", "teacher")); st.rerun()
    with right:
        st.markdown('<div class="panel"><div class="eyebrow">For students</div><h2>Take the right test</h2><p class="muted">See your assigned assessments, save your progress, and get a clear result.</p></div>', unsafe_allow_html=True)
        if st.button("Enter student workspace", width="stretch"):
            st.session_state.user = dict(get_or_create_user("student@mcq.local", "Jordan Lee", "student")); st.rerun()
    try:
        oidc_configured = "auth" in st.secrets
    except Exception:
        oidc_configured = False
    if oidc_configured:
        st.divider()
        if st.button("Sign in with Google", width="stretch"):
            st.login("google")
    st.caption("Demo access is enabled locally. Google OIDC can be connected through Streamlit secrets for deployment.")


def workspace_nav(user, selected_page: str | None = None) -> str:
    st.markdown(f"<div class='top-brand'><strong>MCQ</strong><span>Assessment studio</span><span class='top-user'>{user['name']} · {user['role'].title()}</span></div>", unsafe_allow_html=True)
    pages = ["Dashboard", "Create quiz", "Students", "Analytics"] if user["role"] == "teacher" else ["Dashboard"]
    nav, sign_out = st.columns([8, 1], vertical_alignment="center")
    with nav:
        default_page = selected_page if selected_page in pages else pages[0]
        page = st.radio("Workspace", pages, index=pages.index(default_page), horizontal=True, key="workspace-nav", label_visibility="collapsed")
    with sign_out:
        if st.button("Sign out", key="top-sign-out", width="stretch"):
            st.session_state.pop("user", None); st.rerun()
    return page


def google_user():
    if not getattr(st.user, "is_logged_in", False):
        return None
    email = st.user.email.lower()
    teacher_emails = {item.strip().lower() for item in os.getenv("MCQ_TEACHER_EMAILS", "").split(",") if item.strip()}
    role = "teacher" if email in teacher_emails else "student"
    return dict(get_or_create_user(email, st.user.name or email.split("@")[0], role))
