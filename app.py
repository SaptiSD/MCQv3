"""MCQ V3 application shell: startup, authentication, and routing only."""

import streamlit as st

import guide
import server_state
from admin_portal import dashboard as admin_dashboard
from db import init_db, seed_demo_data
from student_portal import choose_teacher_page, dashboard as student_dashboard
from student_portal import my_teachers_page, needs_a_teacher
from teacher_portal import create as create_quiz
from teacher_portal import analytics_page, dashboard as teacher_dashboard
from teacher_portal import roster_page
from ui import (choose_role_page, confirm_discard_dialog, enforce_session, google_user, login_page,
                signed_out_elsewhere_notice, styles, warn_before_leaving, workspace_nav)


st.set_page_config(page_title="MCQ | Assessment studio", page_icon="M", layout="wide")
init_db()
seed_demo_data()
styles()


def main() -> None:
    if "user" not in st.session_state:
        authenticated_user = google_user()
        if authenticated_user:
            st.session_state.user = authenticated_user
            server_state.stamp_session(authenticated_user)
        elif getattr(st.user, "is_logged_in", False):
            # Signed in with Google but we don't know which side they came from.
            choose_role_page()
            return
    if "user" not in st.session_state:
        signed_out_elsewhere_notice()
        login_page()
        return
    user = st.session_state.user
    if not enforce_session(user):
        return
    if user["role"] == "student" and needs_a_teacher(user):
        choose_teacher_page(user)
        return
    pending_page = st.session_state.get("page_override")
    page = workspace_nav(user, pending_page)
    page = st.session_state.pop("page_override", page)
    previous_page = st.session_state.get("current_page")
    if (
        user["role"] == "teacher"
        and previous_page == "Create quiz"
        and page != "Create quiz"
        and st.session_state.get("create_dirty")
        and not st.session_state.get("create_nav_guard")
    ):
        st.session_state.create_nav_request = page
        st.session_state.create_nav_guard = True
        st.rerun()
    if page == "Create quiz" and previous_page != "Create quiz":
        st.session_state.pop("new-quiz-section", None)
        # Arriving fresh clears the guard that stops a double-clicked Publish
        # from creating the same quiz twice.
        st.session_state.pop("publish_in_flight", None)
    if previous_page != page:
        st.session_state.pop("detail_student_id", None)
        st.session_state.pop("show_student_detail", None)
    st.session_state.current_page = page
    if page == "Guide":
        guide.page(user)
        return
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
        if page == "My teachers":
            my_teachers_page(user)
        else:
            student_dashboard(user)
    if user["role"] == "teacher" and st.session_state.get("create_nav_guard"):
        confirm_discard_dialog()
    # Refreshing or navigating away mid-build now costs a browser confirmation
    # rather than the whole draft (which is also mirrored server-side).
    warn_before_leaving(bool(st.session_state.get("create_dirty")))


main()
