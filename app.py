"""MCQ V3 application shell: startup, authentication, and routing only."""

import streamlit as st

from admin_portal import dashboard as admin_dashboard
from db import init_db, seed_demo_data
from student_portal import dashboard as student_dashboard
from teacher_portal import create as create_quiz
from teacher_portal import analytics_page, dashboard as teacher_dashboard
from teacher_portal import roster_page
from ui import google_user, login_page, styles, workspace_nav


st.set_page_config(page_title="MCQ | Assessment studio", page_icon="M", layout="wide")
init_db()
seed_demo_data()
styles()


def main() -> None:
    if "user" not in st.session_state:
        authenticated_user = google_user()
        if authenticated_user:
            st.session_state.user = authenticated_user
    if "user" not in st.session_state:
        login_page()
        return
    user = st.session_state.user
    pending_page = st.session_state.get("page_override")
    page = workspace_nav(user, pending_page)
    page = st.session_state.pop("page_override", page)
    previous_page = st.session_state.get("current_page")
    if previous_page != page:
        st.session_state.pop("detail_student_id", None)
        st.session_state.pop("show_student_detail", None)
    st.session_state.current_page = page
    if user["role"] == "teacher":
        if page == "Create quiz":
            create_quiz(user)
        elif page == "Students":
            roster_page(user)
        elif page == "Analytics":
            analytics_page(user)
        elif page == "Student view":
            student_dashboard(user)
        else:
            teacher_dashboard(user)
    elif user["role"] == "admin":
        admin_dashboard(user)
    else:
        student_dashboard(user)


main()
