"""Teacher-facing assessment management screens."""

from __future__ import annotations

import io
import json
from datetime import datetime, time, timedelta, timezone

import pandas as pd
import streamlit as st
from docx import Document
from fpdf import FPDF

import grading
from ingestion import extract_upload, parse_bank
from ui import empty_state, metric_row, page_header, pill, when
from repository import (add_student_to_roster, assigned_student_ids, create_quiz,
                        delete_quiz, move_question, questions_for_quiz, quiz_counts_for_teacher,
                        quiz_for_teacher, quizzes_for_teacher,
                        quiz_has_attempts, save_question_bank, set_quiz_assignments, students,
                        student_analytics, student_detail_analytics, student_progress_for_quiz,
                        set_team_members, student_ids_for_teams, team_student_ids, teams_for_student, teams_for_teacher,
                        teacher_analytics, update_quiz_settings)


SELECT_ALL_TYPE = "Multiple choice - select all that apply"
QUESTION_TYPES = ["Multiple choice", SELECT_ALL_TYPE, "True / False", "Fill in the blank", "Short answer"]
ANSWER_FORMATS = {"Text": grading.TEXT, "Number": grading.NUMBER}
TEXT_QUESTION_TYPES = grading.TEXT_QUESTION_TYPES


def _question_errors(questions: list[dict]) -> list[str]:
    """Validate a built question list the same way for both editors."""
    errors = []
    for index, question in enumerate(questions, 1):
        question_type = question.get("question_type")
        if not question["question_text"]:
            errors.append(f"Question {index} needs text.")
        if question_type in TEXT_QUESTION_TYPES:
            spec = question["correct_label"]
            if not (isinstance(spec, dict) and spec.get("value")):
                errors.append(f"Question {index} needs a correct answer.")
            elif spec.get("format") == grading.NUMBER and grading._to_number(spec["value"]) is None:
                errors.append(f"Question {index} has a Number answer that isn't a number.")
            continue
        if len(question["options"]) < 2:
            errors.append(f"Question {index} needs at least two options.")
        labels = question["correct_label"] if question_type == SELECT_ALL_TYPE else [question["correct_label"]]
        if question_type == SELECT_ALL_TYPE and not labels:
            errors.append(f"Question {index} needs at least one correct answer.")
        elif not set(labels).issubset({label for label, _ in question["options"]}):
            errors.append(f"Question {index} needs its selected correct option filled in.")
    return errors


def _typed_spec_from(store, prefix: str) -> dict:
    """Rebuild an answer specification from whichever state store the editor saved into."""
    get = store.get
    limit = str(get(f"{prefix}-limit", "") or "").strip()
    alternatives = str(get(f"{prefix}-alternatives", "") or "").split(",")
    label = get(f"{prefix}-format", "Text")
    return grading.build_spec(
        str(get(f"{prefix}-answer", "") or ""),
        ANSWER_FORMATS.get(label, grading.TEXT),
        str(get(f"{prefix}-tolerance", "") or "").strip() or None,
        int(limit) if limit.isdigit() and int(limit) > 0 else None,
        alternatives,
        bool(get(f"{prefix}-typos", False)),
    )


def _create_save_typed(key: str) -> None:
    """Persist a typed-answer widget into the new-quiz draft."""
    form = st.session_state.setdefault("new_quiz_data", {})
    form[key] = st.session_state[key]
    st.session_state["create_dirty"] = True


def typed_answer_editor(prefix: str, read, write=None) -> dict:
    """Answer controls for a Fill in the blank / Short answer question.

    `read(name, default)` fetches a stored value and `write(name)` is the
    on_change callback; the two question editors keep their state differently,
    so they pass their own accessors in.
    """
    fmt_label = read("format", "Text")
    fmt_label = fmt_label if fmt_label in ANSWER_FORMATS else "Text"
    answer_col, format_col = st.columns([3, 2])
    with answer_col:
        value = st.text_input(
            "Correct answer", value=read("answer", ""), key=f"{prefix}-answer",
            placeholder="e.g. 6  ·  33.33  ·  Paris",
            **({"on_change": write, "args": (f"{prefix}-answer",)} if write else {}),
        )
    with format_col:
        fmt_label = st.selectbox(
            "Answer type", list(ANSWER_FORMATS), index=list(ANSWER_FORMATS).index(fmt_label),
            key=f"{prefix}-format",
            help="Number grades 6, 6.0 and 6.00 as the same answer. Text ignores capitals and extra spaces.",
            **({"on_change": write, "args": (f"{prefix}-format",)} if write else {}),
        )
    answer_format = ANSWER_FORMATS[fmt_label]
    alternatives, allow_typos, tolerance, max_length = [], False, "", None
    with st.expander("Marking options"):
        if answer_format == grading.NUMBER:
            tolerance = st.text_input(
                "Accept answers within ±", value=read("tolerance", ""), key=f"{prefix}-tolerance",
                placeholder="leave blank to use the answer's own precision",
                **({"on_change": write, "args": (f"{prefix}-tolerance",)} if write else {}),
            )
        else:
            alternatives = [
                item for item in st.text_input(
                    "Also accept (comma separated)", value=read("alternatives", ""),
                    key=f"{prefix}-alternatives", placeholder="e.g. USA, US, America",
                    **({"on_change": write, "args": (f"{prefix}-alternatives",)} if write else {}),
                ).split(",")
            ]
            allow_typos = st.checkbox(
                "Forgive single-letter spelling slips", value=bool(read("typos", False)),
                key=f"{prefix}-typos",
                **({"on_change": write, "args": (f"{prefix}-typos",)} if write else {}),
            )
        limit_raw = st.text_input(
            "Limit the answer box to (characters)", value=read("limit", ""), key=f"{prefix}-limit",
            placeholder="leave blank to size it automatically",
            **({"on_change": write, "args": (f"{prefix}-limit",)} if write else {}),
        )
        max_length = int(limit_raw) if limit_raw.strip().isdigit() and int(limit_raw) > 0 else None
    spec = grading.build_spec(value, answer_format, tolerance or None, max_length, alternatives, allow_typos)
    st.caption(grading.teacher_summary(spec))
    return spec


@st.dialog("Delete assessment?")
def delete_quiz_dialog(user, quiz_id: int, title: str) -> None:
    st.write(f"Delete **{title}** and all its questions, assignments, and results?")
    confirm, cancel = st.columns(2)
    if confirm.button("Yes, delete", key=f"confirm-delete-{quiz_id}", type="primary", width="stretch"):
        delete_quiz(quiz_id, user["id"])
        st.session_state.pop("manage_quiz", None)
        st.rerun()
    if cancel.button("Cancel", key=f"cancel-delete-{quiz_id}", width="stretch"):
        st.rerun()


def dashboard(user) -> None:
    quizzes = quizzes_for_teacher(user["id"])
    roster = students(user["id"])
    analytics = teacher_analytics(user["id"])
    created_title = st.session_state.pop("quiz_created", None)
    if created_title:
        st.success(f"**{created_title}** was created.")
    page_header("Teacher workspace", "Your assessments", "Create, assign, review, and understand the assessments you own.")
    metric_row([
        (analytics["quizzes"], "Total quizzes"),
        (analytics["active_quizzes"], "Published"),
        (len(roster), "Students in roster"),
        (analytics["completed"], "Completed attempts"),
        (f"{analytics['average_score'] or 0:.0f}%", "Average score"),
        (f"{(analytics['pass_rate'] or 0) * 100:.0f}%", "Pass rate"),
        (analytics["assigned_students"], "Assigned students"),
        (f"{(analytics['completed'] / analytics['attempts'] * 100) if analytics['attempts'] else 0:.0f}%", "Completion rate"),
    ])
    st.divider()
    quiz_search = st.text_input("Search quizzes", placeholder="Search by title", label_visibility="collapsed", key="dashboard-quiz-search")
    visible_quizzes = [quiz for quiz in quizzes if not quiz_search.strip() or quiz_search.lower() in quiz["title"].lower()]
    if not visible_quizzes:
        if quizzes:
            empty_state("Nothing matches that search", "Try a different word, or clear the search box to see every assessment.")
        else:
            empty_state("No assessments yet", "Head to Create quiz to build your first one. It takes about a minute.")
        return
    counts = quiz_counts_for_teacher(user["id"])
    for quiz in visible_quizzes:
        with st.container(border=True):
            details, action = st.columns([4, 1])
            with details:
                summary = counts.get(quiz["id"], {"questions": 0, "assigned": 0})
                assigned = summary["assigned"]
                if quiz["status"] != "active" or not summary["questions"]:
                    badge = pill("Draft", "grey")
                elif not assigned:
                    badge = pill("Not assigned", "amber")
                else:
                    badge = pill("Published", "green")
                st.markdown(f"### {quiz['title']} &nbsp;{badge}", unsafe_allow_html=True)
                audience = f"{assigned} assigned student{'s' if assigned != 1 else ''}" if assigned else "Not assigned"
                st.caption(f"{summary['questions']} questions  ·  {quiz['duration_minutes']} minutes  ·  pass at {quiz['passing_score']}%  ·  {audience}")
            with action:
                if st.button("Manage", key=f"manage-{quiz['id']}", width="stretch"):
                    st.session_state.manage_quiz = quiz["id"]; st.rerun()
                if st.button("Delete", key=f"delete-{quiz['id']}", width="stretch"):
                    delete_quiz_dialog(user, quiz["id"], quiz["title"])
        if st.session_state.get("manage_quiz") == quiz["id"]:
            manage_quiz(user, quiz["id"])


def analytics_page(user) -> None:
    analytics = teacher_analytics(user["id"])
    students_data = student_analytics(user["id"])
    page_header("Teacher workspace", "Performance overview", "A quick read on assessment health, student outcomes, and completion.")
    metric_row([
        (analytics["attempts"], "Total attempts"),
        (analytics["completed"], "Completed"),
        (f"{analytics['average_score'] or 0:.0f}%", "Average score"),
        (f"{(analytics['pass_rate'] or 0) * 100:.0f}%", "Pass rate"),
        (f"{(analytics['completed'] / analytics['attempts'] * 100) if analytics['attempts'] else 0:.0f}%", "Completion rate"),
        (len(students_data), "Students tracked"),
    ], per_row=3)
    st.divider()
    st.subheader("Student performance")
    if students_data:
        st.dataframe(pd.DataFrame([{"Student": row["name"], "Email": row["email"], "Assigned": row["assigned_quizzes"], "Attempts": row["attempts"], "Completed": row["completed"], "Average score": f"{row['average_score']:.1f}%" if row["average_score"] is not None else "-", "Pass rate": f"{row['pass_rate'] * 100:.0f}%" if row["pass_rate"] is not None else "-", "Last activity": when(row["last_activity"])} for row in students_data]), width="stretch", hide_index=True)
    else:
        empty_state("No students tracked yet", "Students appear here once they choose you as their teacher, or once you add them from the Students page.")
    st.divider()
    st.subheader("Exam participation")
    quizzes = quizzes_for_teacher(user["id"])
    if quizzes:
        quiz_options = {quiz["id"]: quiz["title"] for quiz in quizzes}
        selected_quiz_id = st.selectbox("Select an exam", list(quiz_options), format_func=quiz_options.get, key="analytics-exam")
        progress = student_progress_for_quiz(user["id"], selected_quiz_id)
        if progress:
            search = st.text_input("Look up a student on this test", placeholder="Search by name or email", key="analytics-student-search")
            if search.strip():
                progress = [row for row in progress if search.lower() in row["student"].lower() or search.lower() in row["email"].lower()]
            counts = {status: sum(row["status"] == status for row in progress) for status in ("Not started", "In progress", "Completed")}
            metric_row([(counts["Not started"], "Not started"), (counts["In progress"], "In progress"), (counts["Completed"], "Completed")], per_row=3)
            st.dataframe(pd.DataFrame([{"Student": row["student"], "Email": row["email"], "Status": row["status"], "Score": f"{row['score']:.1f}%" if row["score"] is not None else "-", "Result": row["result"], "Last activity": when(row["last_activity"])} for row in progress]), width="stretch", hide_index=True)
        else:
            st.info("No students are assigned to this exam yet.")


def _create_save_setting(key: str) -> None:
    if "new_quiz_data" not in st.session_state:
        st.session_state["new_quiz_data"] = {}
    st.session_state["new_quiz_data"][key] = st.session_state[key]
    st.session_state["create_dirty"] = True


def create(user) -> None:
    form = st.session_state.setdefault("new_quiz_data", {})
    page_header("New assessment", "Build an assessment", "Set it up first, then write the questions, and publish when everything is ready.")
    top_publish = st.button("Publish quiz", key="new-quiz-publish-top", type="primary", width="stretch")
    section = st.session_state.get("new-quiz-section", "settings")
    settings_button, questions_button = st.columns(2)
    if settings_button.button("Quiz settings", key="new-quiz-settings", type="primary" if section == "settings" else "secondary", width="stretch"):
        st.session_state["new-quiz-section"] = "settings"
        st.rerun()
    if questions_button.button("Questions", key="new-quiz-questions", type="primary" if section == "questions" else "secondary", width="stretch"):
        st.session_state["new-quiz-section"] = "questions"
        st.rerun()

    if section == "questions":
        with st.container(border=True):
            st.subheader("Questions")
            question_mode = st.radio("Add questions", ["Create manually", "Upload question bank"], horizontal=True, key="new-quiz-mode", on_change=_create_save_setting, args=("new-quiz-mode",))
            if question_mode == "Upload question bank":
                upload = st.file_uploader("Question bank (.txt or .docx)", type=["txt", "docx"], key="new-quiz-upload")
                if upload and st.button("Read question bank", key="new-quiz-parse"):
                    try:
                        form["new-uploaded-questions"] = parse_bank(extract_upload(upload))
                    except Exception as exc:
                        form["new-uploaded-questions"] = []
                        st.error(f"Could not read this question bank: {exc}")
                    st.session_state["create_dirty"] = True
                uploaded_questions = form.get("new-uploaded-questions", [])
                if not isinstance(uploaded_questions, list):
                    uploaded_questions = []
                st.write(f"Questions ready: **{len(uploaded_questions)}**")
                if upload and not uploaded_questions:
                    st.warning("No valid questions found. Include numbered questions, options, and an Answer Key before publishing.")
            else:
                count_key = "new-manual-count"
                if count_key not in form:
                    form[count_key] = 1
                count = form[count_key]
                add_col, remove_col = st.columns(2)
                st.write(f"**{int(count)}** question{'s' if int(count) != 1 else ''} in this quiz")
                if add_col.button("Add another question", key="new-manual-add", width="stretch"):
                    form[count_key] = int(count) + 1
                    st.session_state["create_dirty"] = True
                    st.rerun()
                if remove_col.button("Remove last question", key="new-manual-remove", width="stretch", disabled=int(count) <= 1):
                    form[count_key] = int(count) - 1
                    st.session_state["create_dirty"] = True
                    st.rerun()
                for index in range(int(count)):
                    with st.container(border=True):
                        st.markdown(f"**Question {index + 1}**")
                        current_type = form.get(f"new-type-{index}", "Multiple choice")
                        question_type = st.selectbox("Question type", QUESTION_TYPES, index=QUESTION_TYPES.index(current_type) if current_type in QUESTION_TYPES else 0, key=f"new-type-{index}", on_change=_create_save_setting, args=(f"new-type-{index}",))
                        st.text_area("Question text", key=f"new-text-{index}", height=80, value=form.get(f"new-text-{index}", ""), on_change=_create_save_setting, args=(f"new-text-{index}",))
                        if question_type in {"Multiple choice", SELECT_ALL_TYPE}:
                            option_cols = st.columns(4)
                            for option_index, label in enumerate(("A", "B", "C", "D")):
                                with option_cols[option_index]:
                                    st.text_input(f"Option {label}", key=f"new-option-{index}-{label}", value=form.get(f"new-option-{index}-{label}", ""), on_change=_create_save_setting, args=(f"new-option-{index}-{label}",))
                            if question_type == SELECT_ALL_TYPE:
                                st.multiselect("Correct answers", ["A", "B", "C", "D"], key=f"new-correct-all-{index}", default=form.get(f"new-correct-all-{index}", []), on_change=_create_save_setting, args=(f"new-correct-all-{index}",))
                            else:
                                correct_cfg = ["A", "B", "C", "D"]
                                correct_val = form.get(f"new-correct-{index}")
                                st.selectbox("Correct answer", correct_cfg, index=(correct_cfg.index(correct_val) if correct_val in correct_cfg else 0), key=f"new-correct-{index}", on_change=_create_save_setting, args=(f"new-correct-{index}",))
                        elif question_type == "True / False":
                            tf_val = form.get(f"new-correct-{index}")
                            st.selectbox("Correct answer", ["True", "False"], index=(0 if tf_val != "False" else 1), key=f"new-correct-{index}", on_change=_create_save_setting, args=(f"new-correct-{index}",))
                        else:
                            typed_answer_editor(
                                f"new-typed-{index}",
                                lambda name, default, i=index: form.get(f"new-typed-{i}-{name}", default),
                                _create_save_typed,
                            )
    else:
        with st.container(border=True):
            st.subheader("Quiz settings")
            st.text_input("Quiz title", placeholder="e.g. Foundations of Computing", key="new-title",
                          value=form.get("new-title", ""), on_change=_create_save_setting, args=("new-title",))
            first, second = st.columns(2)
            with first: st.number_input("Time allowed (minutes)", 1, 480, form.get("new-duration", 30), key="new-duration", on_change=_create_save_setting, args=("new-duration",))
            with second: st.number_input("Passing score (%)", 0, 100, form.get("new-passing", 70), key="new-passing", on_change=_create_save_setting, args=("new-passing",))
            st.checkbox("Allow retakes", form.get("new-retakes", False), key="new-retakes", on_change=_create_save_setting, args=("new-retakes",))
            st.checkbox("Show class average to students", form.get("new-average", False), key="new-average", on_change=_create_save_setting, args=("new-average",))
            st.checkbox("Randomize question order", form.get("new-randomize-questions", True), key="new-randomize-questions", on_change=_create_save_setting, args=("new-randomize-questions",))
            st.checkbox("Randomize answer order", form.get("new-randomize-answers", True), key="new-randomize-answers", on_change=_create_save_setting, args=("new-randomize-answers",))
            today = datetime.now().date()
            tomorrow = today + timedelta(days=1)
            opening_enabled = form.get("new-opening-enabled", True)
            st.checkbox("Enable opening date and time", opening_enabled, key="new-opening-enabled", on_change=_create_save_setting, args=("new-opening-enabled",))
            opening_date, opening_time = st.columns(2)
            with opening_date: st.date_input("Opens on", form.get("new-opening-day", today), key="new-opening-day", disabled=not opening_enabled, on_change=_create_save_setting, args=("new-opening-day",))
            with opening_time: st.time_input("Opening time", form.get("new-opening-clock", time(8, 0)), key="new-opening-clock", disabled=not opening_enabled, on_change=_create_save_setting, args=("new-opening-clock",))
            closing_enabled = form.get("new-closing-enabled", True)
            st.checkbox("Enable closing date and time", closing_enabled, key="new-closing-enabled", on_change=_create_save_setting, args=("new-closing-enabled",))
            closing_date, closing_time = st.columns(2)
            with closing_date: st.date_input("Closes on", form.get("new-closing-day", tomorrow), key="new-closing-day", disabled=not closing_enabled, on_change=_create_save_setting, args=("new-closing-day",))
            with closing_time: st.time_input("Closing time", form.get("new-closing-clock", time(17, 0)), key="new-closing-clock", disabled=not closing_enabled, on_change=_create_save_setting, args=("new-closing-clock",))
            st.divider(); st.subheader("Audience")
            roster = students(user["id"])
            audience_mode = st.radio("Assign to", ["All", "Students", "Teams"], horizontal=True, key="new-audience-mode",
                                     index=0 if form.get("new-audience-mode") != "Students" and form.get("new-audience-mode") != "Teams" else 1 if form.get("new-audience-mode") == "Students" else 2,
                                     on_change=_create_save_setting, args=("new-audience-mode",))
            if audience_mode == "Students":
                st.multiselect("Assign to students", options=roster, default=form.get("new-selected", []), format_func=lambda row: f"{row['name']}  ·  {row['email']}", key="new-selected", on_change=_create_save_setting, args=("new-selected",))
            elif audience_mode == "Teams":
                st.multiselect("Assign to teams", options=teams_for_teacher(user["id"]), default=form.get("new-team-selected", []), format_func=lambda team: team["name"], key="new-team-selected", on_change=_create_save_setting, args=("new-team-selected",))

    bottom_publish = st.button("Publish quiz", key="new-quiz-publish-bottom", type="primary", width="stretch")
    if not (top_publish or bottom_publish):
        return
    title = form.get("new-title", "").strip()
    question_mode = form.get("new-quiz-mode", "Create manually")
    if question_mode == "Upload question bank":
        questions = form.get("new-uploaded-questions", [])
        if not isinstance(questions, list):
            questions = []
    else:
        questions = []
        for index in range(int(form.get("new-manual-count", 1))):
            question_type = form.get(f"new-type-{index}", "Multiple choice")
            if question_type in {"Multiple choice", SELECT_ALL_TYPE}:
                options = [(label, form.get(f"new-option-{index}-{label}", "").strip()) for label in ("A", "B", "C", "D")]
                options = [(label, value) for label, value in options if value]
                correct = form.get(f"new-correct-all-{index}", []) if question_type == SELECT_ALL_TYPE else form.get(f"new-correct-{index}", "A")
            elif question_type == "True / False":
                options = [("A", "True"), ("B", "False")]
                correct = "A" if form.get(f"new-correct-{index}", "True") != "False" else "B"
            else:
                correct = _typed_spec_from(form, f"new-typed-{index}")
                options = []
            questions.append({"question_text": form.get(f"new-text-{index}", "").strip(), "options": options, "correct_label": correct, "question_type": question_type})
    errors = []
    if not title:
        errors.append("Give the quiz a title first.")
    if not questions:
        errors.append("Add at least one question before publishing.")
    errors.extend(_question_errors(questions))
    if errors:
        st.error(" ".join(errors))
        return
    opening = datetime.combine(form.get("new-opening-day", datetime.now().date()), form.get("new-opening-clock", time(8, 0))).astimezone()
    closing = datetime.combine(form.get("new-closing-day", (datetime.now().date() + timedelta(days=1))), form.get("new-closing-clock", time(17, 0))).astimezone()
    opening_enabled = form.get("new-opening-enabled", True)
    closing_enabled = form.get("new-closing-enabled", True)
    now = datetime.now(timezone.utc)
    if opening_enabled and closing_enabled and closing <= opening:
        st.error("Closing time must be after opening time.")
        return
    if closing_enabled and closing <= now:
        st.error("Closing time is in the past; students won't be able to take this quiz. Set a closing time in the future.")
        return
    if opening_enabled and opening > now:
        st.info(f"The quiz opens on {opening.astimezone().strftime('%b %d, %I:%M %p')} and won't be visible to students until then.")
    if form.get("new-audience-mode", "All") == "All":
        assigned_students = {row["id"] for row in students(user["id"])}
    else:
        assigned_students = {row["id"] for row in form.get("new-selected", [])}
        assigned_students.update(student_ids_for_teams(user["id"], [team["id"] for team in form.get("new-team-selected", [])]))
    quiz_id = create_quiz(user["id"], title, form.get("new-duration", 30), form.get("new-passing", 70), form.get("new-retakes", False), form.get("new-average", False), opening.isoformat(), closing.isoformat(), list(assigned_students), opening_enabled, closing_enabled, form.get("new-randomize-questions", True), form.get("new-randomize-answers", True))
    save_question_bank(quiz_id, questions)
    st.session_state.page_override = "Dashboard"
    st.session_state.quiz_created = title
    st.session_state.pop("create_dirty", None)
    st.session_state.pop("manage_quiz", None)
    st.session_state.pop("new-quiz-section", None)
    st.session_state.pop("new_quiz_data", None)
    st.rerun()


def _quiz_downloads(quiz, questions: list | None = None) -> None:
    """Always-visible export controls (CSV / DOCX / PDF / results) at the top of the quiz manager."""
    st.subheader("Download")
    progress = student_progress_for_quiz(quiz["owner_id"], quiz["id"])
    if progress:
        results_frame = pd.DataFrame([{"Student": row["student"], "Email": row["email"], "Status": row["status"], "Score": row["score"], "Result": row["result"], "Last activity": when(row["last_activity"])} for row in progress])
        st.download_button("Download results CSV", results_frame.to_csv(index=False), "student-results.csv", "text/csv", key=f"results-download-{quiz['id']}")
    else:
        st.info("No students are assigned to this exam yet.")
    if questions is None:
        questions = list(questions_for_quiz(quiz["id"]))
    if not questions:
        return
    pdf_format = st.selectbox("PDF contents", ["Questions only", "Questions with correct answers", "Questions with correct answers and quiz settings"], format_func=lambda value: value, key=f"pdf-format-{quiz['id']}")
    format_map = {
        "Questions only": "questions",
        "Questions with correct answers": "questions-answers",
        "Questions with correct answers and quiz settings": "questions-answers-settings",
    }
    pdf_bytes = _render_quiz_pdf(quiz, questions, format_map[pdf_format])
    st.download_button("Download PDF", pdf_bytes, "quiz.pdf", "application/pdf", type="primary", key=f"pdf-{quiz['id']}")
    frame = pd.DataFrame([{"Question": q["question_text"], "Correct": q["correct_label"], **dict(json.loads(q["options_json"]))} for q in questions])
    st.download_button("Download question bank CSV", frame.to_csv(index=False), "question-bank.csv", "text/csv", key=f"bank-csv-{quiz['id']}")
    document = Document(); document.add_heading(quiz["title"], 0)
    for index, q in enumerate(questions, 1):
        document.add_paragraph(f"{index}. {q['question_text']}")
        for label, text in json.loads(q["options_json"]): document.add_paragraph(f"{label}) {text}", style="List Bullet")
    output = io.BytesIO(); document.save(output)
    st.download_button("Download printable DOCX", output.getvalue(), "quiz.docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document", key=f"docx-{quiz['id']}")


@st.fragment
def manage_quiz(user, quiz_id: int) -> None:
    with st.container(border=True):
        quiz = quiz_for_teacher(quiz_id, user["id"])
        if not quiz: return
        st.divider(); st.markdown(f"### Manage: {quiz['title']}")
        _quiz_downloads(quiz)
        questions_button, settings_button = st.columns(2)
        section = st.session_state.get(f"quiz-section-{quiz_id}", "questions")
        if settings_button.button("Quiz settings", key=f"settings-section-{quiz_id}", type="primary" if section == "settings" else "secondary", width="stretch"):
            st.session_state[f"quiz-section-{quiz_id}"] = "settings"
            st.rerun(scope="fragment")
        if questions_button.button("Questions", key=f"questions-section-{quiz_id}", type="primary" if section == "questions" else "secondary", width="stretch"):
            st.session_state[f"quiz-section-{quiz_id}"] = "questions"
            st.rerun(scope="fragment")
        if section == "settings":
            settings_editor(quiz)
        else:
            question_bank(quiz)
        with st.expander("Assign by students or teams · View results"):
            assignment_editor(quiz)
            results(quiz)
        if st.button("Close manager", key=f"close-{quiz_id}"): st.session_state.pop("manage_quiz", None); st.rerun()


def _format_correct(question) -> str:
    if question["question_type"] in TEXT_QUESTION_TYPES:
        spec = grading.answer_spec(question)
        extras = spec.get("alternatives") or []
        answer = spec.get("value", "")
        return f"{answer} (or {', '.join(extras)})" if extras else answer
    options = json.loads(question["options_json"])
    if question["question_type"] == SELECT_ALL_TYPE:
        labels = json.loads(question["correct_label"])
        return ", ".join(next((text for label, text in options if label == lab), lab) for lab in labels)
    correct_label = question["correct_label"]
    if question["question_type"] == "True / False":
        return "True" if correct_label == "A" else "False"
    return next((text for label, text in options if label == correct_label), correct_label)


_PDF_REPLACEMENTS = {
    "‘": "'", "’": "'", "‚": "'", "“": '"', "”": '"',
    "„": '"', "–": "-", "—": "-", "…": "...", "•": "-",
    " ": " ", "−": "-", "′": "'", "″": '"',
}


def _pdf_text(value) -> str:
    """FPDF's core fonts are Latin-1 only; text pasted from Word routinely isn't."""
    text = str(value or "")
    for source, target in _PDF_REPLACEMENTS.items():
        text = text.replace(source, target)
    return text.encode("latin-1", "replace").decode("latin-1")


def _render_quiz_pdf(quiz, questions, format_key: str) -> bytes:
    """Render a quiz as a printable PDF.

    format_key selects which detail is included:
      "questions"                 -> questions + options only
      "questions-answers"         -> also include the answer key
      "questions-answers-settings"-> answer key plus quiz settings
    """
    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=18)
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 18)
    pdf.multi_cell(0, 8, _pdf_text(quiz["title"]), new_x="LMARGIN", new_y="NEXT")
    pdf.ln(2)

    if format_key == "questions-answers-settings":
        pdf.set_font("Helvetica", "I", 11)
        pdf.set_text_color(90, 90, 90)
        opening = datetime.fromisoformat(quiz["opening_time"]).astimezone().strftime("%b %d, %I:%M %p") if quiz.get("opening_enabled") else "Any time"
        closing = datetime.fromisoformat(quiz["closing_time"]).astimezone().strftime("%b %d, %I:%M %p") if quiz.get("closing_enabled") else "Open"
        settings_lines = [
            f"Time allowed: {quiz['duration_minutes']} minutes",
            f"Passing score: {quiz['passing_score']}%",
            f"Opens: {opening}",
            f"Closes: {closing}",
            f"Retakes: {'Allowed' if quiz.get('allow_retake') else 'Not allowed'}",
            f"Show class average: {'Yes' if quiz.get('show_average') else 'No'}",
            f"Randomize question order: {'Yes' if quiz.get('randomize_questions') else 'No'}",
            f"Randomize answer order: {'Yes' if quiz.get('randomize_answers') else 'No'}",
        ]
        for line in settings_lines:
            pdf.multi_cell(0, 6, _pdf_text(line), new_x="LMARGIN", new_y="NEXT")
        pdf.set_text_color(0, 0, 0)
        pdf.ln(3)

    for index, question in enumerate(questions, 1):
        if pdf.get_y() > 250:
            pdf.add_page()
        pdf.set_font("Helvetica", "B", 12)
        pdf.multi_cell(0, 6, _pdf_text(f"{index}. {question['question_text']}"), new_x="LMARGIN", new_y="NEXT")
        pdf.ln(1)
        pdf.set_font("Helvetica", "", 11)
        for label, text in json.loads(question["options_json"]):
            pdf.multi_cell(0, 5.5, _pdf_text(f"{label}) {text}"), new_x="LMARGIN", new_y="NEXT")
        if format_key in ("questions-answers", "questions-answers-settings"):
            pdf.set_font("Helvetica", "I", 10)
            pdf.set_text_color(23, 107, 82)
            pdf.multi_cell(0, 5.5, _pdf_text(f"Correct answer: {_format_correct(question)}"), new_x="LMARGIN", new_y="NEXT")
            pdf.set_text_color(0, 0, 0)
        pdf.ln(4)

    return bytes(pdf.output())


def question_bank(quiz) -> None:
    questions = questions_for_quiz(quiz["id"])
    if questions:
        with st.expander("Reorder questions"):
            question_options = {question["id"]: f"{index}. {question['question_text']}" for index, question in enumerate(questions, 1)}
            selected_id = st.selectbox("Question", list(question_options), format_func=question_options.get, key=f"reorder-question-{quiz['id']}")
            selected_index = next(index for index, question in enumerate(questions) if question["id"] == selected_id)
            move_up, move_down = st.columns(2)
            if move_up.button("Move up", key=f"move-up-{quiz['id']}", disabled=selected_index == 0, width="stretch"):
                move_question(quiz["id"], selected_id, -1)
                st.rerun(scope="fragment")
            if move_down.button("Move down", key=f"move-down-{quiz['id']}", disabled=selected_index == len(questions) - 1, width="stretch"):
                move_question(quiz["id"], selected_id, 1)
                st.rerun(scope="fragment")
    mode = st.radio("How would you like to add questions?", ["Create manually", "Upload question bank"], horizontal=True, key=f"question-mode-{quiz['id']}")
    if mode == "Create manually":
        manual_question_editor(quiz)
        return
    upload = st.file_uploader("Upload question bank (.txt or .docx)", type=["txt", "docx"], key=f"upload-{quiz['id']}")
    if upload and st.button("Parse question bank", type="primary", key=f"parse-{quiz['id']}"):
        try:
            st.session_state[f"draft-{quiz['id']}"] = parse_bank(extract_upload(upload))
        except Exception as exc:
            st.session_state[f"draft-{quiz['id']}"] = []
            st.error(f"Could not read this question bank: {exc}")
    draft = st.session_state.get(f"draft-{quiz['id']}")
    if draft is not None:
        if not draft:
            st.warning("No valid questions found. Include numbered questions, options, and an Answer Key before publishing.")
            return
        st.write("Review extracted questions before publishing")
        table = pd.DataFrame([
            {
                "Question": q["question_text"],
                "Type": q.get("question_type", "Multiple choice"),
                "Options": " | ".join(f"{a}) {b}" for a, b in q["options"]),
                "Correct": q["correct_label"],
            }
            for q in draft
        ])
        edited = st.data_editor(
            table, num_rows="dynamic", width="stretch", key=f"editor-{quiz['id']}",
            column_config={
                "Type": st.column_config.SelectboxColumn("Type", options=QUESTION_TYPES, required=True),
                "Options": st.column_config.TextColumn("Options", help="A) first | B) second — leave blank for typed answers"),
                "Correct": st.column_config.TextColumn("Correct", help="A letter for choice questions, or the answer itself for typed ones"),
            },
        )
        st.caption("Set the Type column to change how a question is answered and marked. Typed questions grade the Correct column as text unless it reads as a number.")
        if st.button("Save question bank and publish", type="primary", key=f"save-{quiz['id']}"):
            questions = _questions_from_table(edited)
            errors = _question_errors(questions)
            if errors:
                st.error(" ".join(errors))
            else:
                save_question_bank(quiz["id"], questions)
                st.session_state.pop(f"draft-{quiz['id']}", None)
                st.success("Question bank published.")
                st.rerun(scope="fragment")
        return
    st.caption(f"Question bank: {len(questions)} questions")
    for index, question in enumerate(questions, 1):
        options = json.loads(question["options_json"])
        st.write(f"**{index}. {question['question_text']}**")
        st.caption("  ·  ".join(f"{label}) {text}" for label, text in options))


def _questions_from_table(frame) -> list[dict]:
    """Turn the reviewed upload table back into saveable questions, type intact."""
    questions = []
    for _, row in frame.iterrows():
        text = str(row.get("Question", "") or "").strip()
        if not text:
            continue
        question_type = str(row.get("Type") or "Multiple choice").strip()
        if question_type not in QUESTION_TYPES:
            question_type = "Multiple choice"
        correct_raw = str(row.get("Correct", "") or "").strip()
        if question_type in TEXT_QUESTION_TYPES:
            answer_format = grading.NUMBER if grading._to_number(correct_raw) is not None else grading.TEXT
            questions.append({
                "question_text": text, "options": [],
                "correct_label": grading.build_spec(correct_raw, answer_format),
                "question_type": question_type,
            })
            continue
        if question_type == "True / False":
            options = [("A", "True"), ("B", "False")]
        else:
            options = [
                (chr(65 + i), part.split(")", 1)[-1].strip())
                for i, part in enumerate(str(row.get("Options", "") or "").split("|"))
                if part.split(")", 1)[-1].strip()
            ]
        if question_type == SELECT_ALL_TYPE:
            correct = [part.strip().upper() for part in correct_raw.replace("|", ",").split(",") if part.strip()]
        else:
            correct = correct_raw.upper()[:1]
        questions.append({
            "question_text": text, "options": options,
            "correct_label": correct, "question_type": question_type,
        })
    return questions


def manual_question_editor(quiz) -> None:
    st.caption("Create the test directly. Each question needs text, at least two options, and one correct answer.")
    count_key = f"manual-count-{quiz['id']}"
    if count_key not in st.session_state:
        existing_questions = list(questions_for_quiz(quiz["id"]))
        st.session_state[count_key] = max(1, len(existing_questions))
        for index, question in enumerate(existing_questions):
            question_type = question["question_type"]
            st.session_state[f"manual-type-{quiz['id']}-{index}"] = question_type
            st.session_state[f"manual-text-{quiz['id']}-{index}"] = question["question_text"]
            for label, value in json.loads(question["options_json"]):
                st.session_state[f"manual-option-{quiz['id']}-{index}-{label}"] = value
            if question_type == SELECT_ALL_TYPE:
                st.session_state[f"manual-correct-all-{quiz['id']}-{index}"] = json.loads(question["correct_label"])
            elif question_type in TEXT_QUESTION_TYPES:
                spec = grading.answer_spec(question)
                prefix = f"manual-typed-{quiz['id']}-{index}"
                st.session_state[f"{prefix}-answer"] = spec.get("value", "")
                st.session_state[f"{prefix}-format"] = "Number" if spec.get("format") == grading.NUMBER else "Text"
                st.session_state[f"{prefix}-tolerance"] = str(spec.get("tolerance") or "")
                st.session_state[f"{prefix}-alternatives"] = ", ".join(spec.get("alternatives") or [])
                st.session_state[f"{prefix}-typos"] = bool(spec.get("allow_typos"))
                st.session_state[f"{prefix}-limit"] = str(spec.get("max_length") or "")
            else:
                st.session_state[f"manual-correct-{quiz['id']}-{index}"] = question["correct_label"]
    def _sync_count() -> None:
        st.session_state[count_key] = int(st.session_state[f"{count_key}-input"])

    count = st.number_input("Number of questions", min_value=1, max_value=200,
                            value=st.session_state[count_key], key=f"{count_key}-input",
                            on_change=_sync_count)
    st.session_state[count_key] = int(count)
    add_col, remove_col = st.columns(2)
    def _set_count(value: int) -> None:
        # Drop the widget's own key so the number_input picks up the new value.
        st.session_state.pop(f"{count_key}-input", None)
        st.session_state[count_key] = value

    if add_col.button("Add another question", key=f"manual-add-{quiz['id']}", width="stretch"):
        _set_count(int(count) + 1)
        st.rerun(scope="fragment")
    if remove_col.button("Remove last question", key=f"manual-remove-{quiz['id']}", width="stretch", disabled=int(count) <= 1):
        _set_count(int(count) - 1)
        st.rerun(scope="fragment")
    questions = []
    for index in range(int(count)):
        with st.container(border=True):
            st.markdown(f"**Question {index + 1}**")
            question_type = st.selectbox("Question type", QUESTION_TYPES, key=f"manual-type-{quiz['id']}-{index}")
            text = st.text_area("Question text", key=f"manual-text-{quiz['id']}-{index}", height=80)
            options = []
            if question_type == "True / False":
                options = [("A", "True"), ("B", "False")]
            elif question_type in {"Multiple choice", SELECT_ALL_TYPE}:
                columns = st.columns(4)
                for option_index, label in enumerate(("A", "B", "C", "D")):
                    with columns[option_index]:
                        value = st.text_input(f"Option {label}", key=f"manual-option-{quiz['id']}-{index}-{label}")
                        if value.strip():
                            options.append((label, value.strip()))
            if question_type == "True / False":
                correct = st.selectbox("Correct answer", ["A", "B"], format_func=lambda value: "True" if value == "A" else "False", key=f"manual-correct-{quiz['id']}-{index}")
            elif question_type == SELECT_ALL_TYPE:
                correct = st.multiselect("Correct answers", ["A", "B", "C", "D"], key=f"manual-correct-all-{quiz['id']}-{index}")
            elif question_type == "Multiple choice":
                available = [label for label, _ in options] or ["A", "B", "C", "D"]
                stored = st.session_state.get(f"manual-correct-{quiz['id']}-{index}")
                correct = st.selectbox("Correct answer", available,
                                       index=available.index(stored) if stored in available else 0,
                                       key=f"manual-correct-mc-{quiz['id']}-{index}")
                st.session_state[f"manual-correct-{quiz['id']}-{index}"] = correct
            else:
                prefix = f"manual-typed-{quiz['id']}-{index}"
                correct = typed_answer_editor(prefix, lambda name, default, p=prefix: st.session_state.get(f"{p}-{name}", default))
                options = []
            correct_label = correct if question_type in TEXT_QUESTION_TYPES or question_type == SELECT_ALL_TYPE else correct.strip().upper()
            questions.append({"question_text": text.strip(), "options": options, "correct_label": correct_label, "question_type": question_type})
    if st.button("Save manually created test", type="primary", key=f"manual-save-{quiz['id']}", width="stretch"):
        errors = _question_errors(questions)
        if errors:
            st.error(" ".join(errors))
        else:
            save_question_bank(quiz["id"], questions)
            st.success("Test saved and published.")
            st.rerun(scope="fragment")


def settings_editor(quiz) -> None:
    has_attempts = quiz_has_attempts(quiz["id"], exclude_student_id=quiz["owner_id"])
    if has_attempts:
        st.info("Settings are read-only after a student starts this assessment. Your own preview attempts don't count.")
    opening = datetime.fromisoformat(quiz["opening_time"]).astimezone()
    closing = datetime.fromisoformat(quiz["closing_time"]).astimezone()
    with st.form(f"settings-{quiz['id']}"):
        first, second, third = st.columns(3)
        with first: duration = st.number_input("Time allowed (minutes)", 1, 480, quiz["duration_minutes"], disabled=has_attempts)
        with second: passing = st.number_input("Passing score (%)", 0, 100, quiz["passing_score"], disabled=has_attempts)
        opening_enabled = st.checkbox("Enable opening date and time", value=bool(quiz["opening_enabled"]), disabled=has_attempts)
        with st.container(border=True):
            opening_date, opening_time = st.columns(2)
            with opening_date: opening_day = st.date_input("Opens on", opening.date(), disabled=has_attempts or not opening_enabled)
            with opening_time: opening_clock = st.time_input("Opening time", opening.time(), disabled=has_attempts or not opening_enabled)
        closing_enabled = st.checkbox("Enable closing date and time", value=bool(quiz["closing_enabled"]), disabled=has_attempts)
        with st.container(border=True):
            closing_date, closing_time = st.columns(2)
            with closing_date: closing_day = st.date_input("Closes on", closing.date(), disabled=has_attempts or not closing_enabled)
            with closing_time: closing_clock = st.time_input("Closing time", closing.time(), disabled=has_attempts or not closing_enabled)
        allow_retake = st.checkbox("Allow retakes", bool(quiz["allow_retake"]), disabled=has_attempts)
        show_average = st.checkbox("Show class average", bool(quiz["show_average"]), disabled=has_attempts)
        randomize_questions = st.checkbox("Randomize question order", bool(quiz["randomize_questions"]), disabled=has_attempts)
        randomize_answers = st.checkbox("Randomize answer order", bool(quiz["randomize_answers"]), disabled=has_attempts)
        saved = st.form_submit_button("Save settings", type="primary", disabled=has_attempts, width="stretch")
    if saved:
        opening_value = datetime.combine(opening_day, opening_clock).astimezone()
        closing_value = datetime.combine(closing_day, closing_clock).astimezone()
        if opening_enabled and closing_enabled and closing_value <= opening_value:
            st.error("Closing time must be after opening time.")
        elif closing_enabled and closing_value <= datetime.now(timezone.utc):
            st.error("Closing time is in the past; students won't be able to take this quiz. Set a closing time in the future.")
        else:
            update_quiz_settings(quiz["id"], duration, passing, allow_retake, show_average, opening_value.isoformat(), closing_value.isoformat(), opening_enabled, closing_enabled, randomize_questions, randomize_answers)
            st.success("Settings saved.")


def assignment_editor(quiz) -> None:
    roster = students(quiz["owner_id"]); current = assigned_student_ids(quiz["id"])
    st.write("Choose who can see this assessment")
    st.caption("Select 'All' to assign every student on your roster, or choose specific students or teams.")
    is_all = len(roster) > 0 and len(current) == len(roster)
    audience_mode = st.radio("Assign to", ["All", "Students", "Teams"], horizontal=True, index=0 if is_all else 1, key=f"assigned-mode-{quiz['id']}")
    if audience_mode == "All":
        selected_ids = [row["id"] for row in roster]
    elif audience_mode == "Students":
        selected = st.multiselect("Assigned students", options=roster, default=[row for row in roster if row["id"] in current], format_func=lambda row: f"{row['name']}  ·  {row['email']}", key=f"assigned-{quiz['id']}")
        selected_ids = [row["id"] for row in selected]
    else:
        selected_teams = st.multiselect("Assigned teams", options=teams_for_teacher(quiz["owner_id"]), format_func=lambda team: team["name"], key=f"assigned-teams-{quiz['id']}")
        selected_ids = list(student_ids_for_teams(quiz["owner_id"], [team["id"] for team in selected_teams]))
    if st.button("Save assignment", type="primary", key=f"assign-save-{quiz['id']}"):
        set_quiz_assignments(quiz["id"], selected_ids); st.success("Assignment updated.")


def results(quiz) -> None:
    progress = student_progress_for_quiz(quiz["owner_id"], quiz["id"])
    if not progress:
        st.info("No students are assigned to this exam yet."); return
    frame = pd.DataFrame([{"Student": row["student"], "Email": row["email"], "Status": row["status"], "Score": row["score"], "Result": row["result"], "Last activity": when(row["last_activity"])} for row in progress])
    st.dataframe(frame, width="stretch", hide_index=True)


def roster_page(user) -> None:
    roster = students(user["id"])
    performance = student_analytics(user["id"])
    teams = teams_for_teacher(user["id"])
    page_header("Teacher workspace", "Student roster", "Students who choose you appear here automatically. You can also add them yourself.")
    team_options = {team["id"]: team["name"] for team in teams}
    with st.container(border=True):
        st.subheader("Add an existing student to roster")
        lookup = st.text_input("Search student", placeholder="Search by name or email", key="existing-student-search")
        matches = [row for row in students() if not lookup.strip() or lookup.lower() in row["name"].lower() or lookup.lower() in row["email"].lower()]
        if matches:
            existing = st.selectbox("Find student", matches, format_func=lambda row: f"{row['name']} · {row['email']}", key="existing-student")
            existing_team_id = st.selectbox("Add to team", [None, *team_options], format_func=lambda value: "No team" if value is None else team_options[value], key="existing-student-team")
            if st.button("Add an existing student to roster", type="primary", width="stretch"):
                add_student_to_roster(user["id"], existing["name"], existing["email"], existing_team_id)
                st.success("Student added to your roster.")
                st.rerun()
        elif lookup.strip():
            st.info("No existing student matches that search.")
    with st.container(border=True):
        st.subheader("Add a new student")
        with st.form("add-student"):
            name = st.text_input("Student name", placeholder="e.g. Jordan Lee")
            email = st.text_input("Student email", placeholder="student@example.com")
            team_id = st.selectbox("Add to team", [None, *team_options], format_func=lambda value: "No team" if value is None else team_options[value], key="new-student-team")
            submitted = st.form_submit_button("Add a new student", type="primary", width="stretch")
        if submitted:
            if not name.strip() or "@" not in email:
                st.error("Enter a student name and a valid email address.")
            else:
                try:
                    add_student_to_roster(user["id"], name, email, team_id)
                except ValueError as exc:
                    st.error(str(exc))
                else:
                    st.success(f"{name.strip()} was added to your roster."); st.rerun()
    with st.container(border=True):
        st.subheader("Teams")
        st.caption("Choose a team to see its members. Use the optional search to add someone without opening the member list.")
        for team in teams:
            with st.expander(f"{team['name']} · {len(team_student_ids(team['id']))} members"):
                members = st.multiselect("Members", roster, default=[row for row in roster if row["id"] in team_student_ids(team["id"])], format_func=lambda row: f"{row['name']} · {row['email']}", key=f"team-members-{team['id']}")
                show_search = st.checkbox("Show search to add a member", key=f"show-team-search-{team['id']}")
                if show_search:
                    member_search = st.text_input("Search roster", placeholder="Search by name or email", key=f"team-search-{team['id']}")
                    matches = [row for row in roster if not member_search.strip() or member_search.lower() in row["name"].lower() or member_search.lower() in row["email"].lower()]
                    if matches:
                        candidate = st.radio("Add member", matches, format_func=lambda row: f"{row['name']} · {row['email']}", key=f"team-candidate-{team['id']}")
                        if st.button("Add member", key=f"add-team-member-{team['id']}"):
                            members = [*members, candidate] if candidate["id"] not in {row["id"] for row in members} else members
                            set_team_members(team["id"], [row["id"] for row in members])
                            st.rerun()
                if st.button("Save members", key=f"save-team-{team['id']}"):
                    set_team_members(team["id"], [row["id"] for row in members])
                    st.success("Team updated.")
    with st.container(border=True):
        st.subheader(f"Your Roster (of students) · {len(roster)}")
        if roster:
            stats = [{"Student": row["name"], "Email": row["email"], "Team": ", ".join(team["name"] for team in teams_for_student(user["id"], row["id"])), "Assigned": row["assigned_quizzes"], "Attempts": row["attempts"], "Completed": row["completed"], "Average": f"{row['average_score']:.1f}%" if row["average_score"] is not None else "-", "Pass rate": f"{row['pass_rate'] * 100:.0f}%" if row["pass_rate"] is not None else "-"} for row in performance]
            st.dataframe(pd.DataFrame(stats), width="stretch", hide_index=True)
            st.caption("Select a student below to view detailed analytics.")
            for row in performance:
                with st.container(border=True):
                    details, action = st.columns([5, 1])
                    details.write(f"**{row['name']}**  ·  {row['email']}")
                    details.caption(f"{row['completed']} completed · {row['average_score']:.1f}% average" if row["average_score"] is not None else "No completed tests yet")
                    if action.button("Details", key=f"details-{row['id']}", type="primary", width="stretch"):
                        st.session_state.detail_student_id = row["id"]
                        st.session_state.show_student_detail = True
            if st.session_state.pop("show_student_detail", False):
                student_detail_dialog(user, st.session_state.detail_student_id)
        else:
            empty_state("Your roster is empty", "Students appear here as soon as they choose you as their teacher. You can also add them by hand above.")


@st.dialog("Student analytics", width="large")
def student_detail_dialog(user, student_id: int) -> None:
    detail = student_detail_analytics(user["id"], student_id)
    if not detail:
        st.error("Student is not in your roster.")
        return
    student = detail["student"]
    st.subheader(student["name"])
    st.caption(student["email"])
    st.write("Teams: " + (", ".join(team["name"] for team in detail["teams"]) or "No team"))
    results_data = [
        {
            "Test": row["title"],
            # A quiz with no attempt at all is "Not started", not "In progress".
            "Status": "Completed" if row["submitted_at"] else ("In progress" if row["started_at"] else "Not started"),
            "Score": f"{row['score_percent']:.0f}%" if row["score_percent"] is not None else "-",
            "Result": "Passed" if row["passed"] else ("Failed" if row["passed"] is not None else "-"),
            "Last activity": when(row["submitted_at"] or row["started_at"]),
        }
        for row in detail["results"]
    ]
    if results_data:
        st.dataframe(pd.DataFrame(results_data), width="stretch", hide_index=True)
    else:
        st.info("No test results yet.")
