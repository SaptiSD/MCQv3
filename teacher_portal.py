"""Teacher-facing assessment management screens."""

from __future__ import annotations

import io
import json
import re
import uuid
from datetime import datetime, time, timedelta, timezone

import pandas as pd
import streamlit as st
from docx import Document
from fpdf import FPDF

import grading
import server_state
from ingestion import extract_upload, parse_report
from ui import (empty_state, flash, metric_row, page_header, percent, pill, require_session,
                show_flash, text, when)
from repository import (add_student_to_roster, assigned_student_ids, create_quiz,
                        delete_quiz, move_question, questions_for_quiz, quiz_attempt_counts,
                        quiz_counts_for_teacher, quiz_for_teacher, quizzes_for_teacher,
                        quiz_has_attempts, regrade_quiz, save_question_bank, set_quiz_assignments, students,
                        student_analytics, student_detail_analytics, student_progress_for_quiz,
                        set_team_members, student_ids_for_teams, team_student_ids, teams_for_student, teams_for_teacher,
                        teacher_analytics, update_quiz_settings)


SELECT_ALL_TYPE = "Multiple choice - select all that apply"
QUESTION_TYPES = ["Multiple choice", SELECT_ALL_TYPE, "True / False", "Fill in the blank", "Short answer"]
ANSWER_FORMATS = {"Text": grading.TEXT, "Number": grading.NUMBER}
TEXT_QUESTION_TYPES = grading.TEXT_QUESTION_TYPES


def _question_errors(questions: list[dict]) -> list[str]:
    """Validate a built question list the same way for both editors."""
    if not questions:
        # Saving nothing used to be treated as valid, which emptied the quiz.
        return ["A quiz needs at least one question."]
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
        # The same comparison marking uses. `.strip().casefold()` was weaker, so
        # "Paris" and "Paris." passed validation and were then indistinguishable
        # to `attempt_sync.rekey_choice`, which kept whichever the shuffle put
        # last -- two students giving the same answer could be marked differently.
        texts = [grading.normalise_text(value) for _, value in question["options"]]
        if len(set(texts)) != len(texts):
            errors.append(f"Question {index} lists the same option text more than once.")
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


DRAFT_NAME = "new_quiz"


def _draft_key() -> str:
    return server_state.account_key(st.session_state.get("user") or {})


def has_unpublished_draft(user) -> bool:
    """Whether this account left a quiz half-built somewhere.

    A refresh throws the browser session away, so the app used to come back on
    the Dashboard and the teacher had no reason to think their work had survived.
    `main()` uses this to land them back on Create quiz instead, where the
    recovery banner is.
    """
    return bool(server_state.load_draft(server_state.account_key(user), DRAFT_NAME))


def _mirror_draft(form: dict) -> None:
    """Keep a copy of the in-progress quiz outside the browser session.

    A refresh or a Back button throws `st.session_state` away; this copy is what
    the teacher gets back instead of an empty form.
    """
    server_state.save_draft(_draft_key(), DRAFT_NAME, dict(form))


# Deliberately does *not* start with "new-": `_clear_new_quiz_state` sweeps
# every "new-" key out of the session, and an epoch that swept itself away would
# reset to zero and rename nothing.
_CREATE_EPOCH = "create-quiz-epoch"


def _create_suffix() -> str:
    """What is appended to every Create-page widget key to name this draft."""
    return f"~{int(st.session_state.get(_CREATE_EPOCH, 0))}"


def _ck(name: str) -> str:
    """The widget key for a Create-page field whose draft key is `name`.

    The draft is keyed by the bare name; the widget gets the name plus the
    current epoch. Discarding bumps the epoch, which renames every box on the
    page -- and renaming is the only thing that actually empties one. Deleting
    the key does not: the browser still holds what was typed, re-sends it on the
    next run, and Streamlit restores it under the same key. That is why Discard
    draft appeared to do nothing -- the draft came straight back, and the
    callback then wrote it into a fresh form.
    """
    return f"{name}{_create_suffix()}"


def _vanished(widget_key: str) -> bool:
    """True when a widget's `on_change` has outlived the widget itself.

    Streamlit runs callbacks for whatever widget values the browser sends at the
    start of a run, and the browser is always one render behind. If the run
    before it cleared those widgets -- closing the question editor, discarding a
    draft, saving and reloading the bank -- the callback still arrives, and the
    key it was told to read is no longer there.

    Reading it anyway raises `KeyError`, and because callbacks run *before* the
    script body, nothing has been drawn yet: the teacher gets a blank white page
    rather than an error, their work appears to have vanished, and only a manual
    refresh gets them out. There is nothing to copy back for a widget that no
    longer exists, so doing nothing is the whole correct behaviour.
    """
    return widget_key not in st.session_state


def _create_save_typed(name: str) -> None:
    """Persist a typed-answer widget into the new-quiz draft."""
    widget_key = _ck(name)
    if _vanished(widget_key):
        return
    form = st.session_state.setdefault("new_quiz_data", {})
    form[name] = st.session_state[widget_key]
    st.session_state["create_dirty"] = True
    _mirror_draft(form)


def typed_answer_editor(prefix: str, read, write=None, key_suffix: str = "") -> dict:
    """Answer controls for a Fill in the blank / Short answer question.

    `read(name, default)` fetches a stored value and `write(name)` is the
    on_change callback; the two question editors keep their state differently,
    so they pass their own accessors in.

    `key_suffix` is appended to the widget keys but never to what `write` is
    told, so a caller can rename these boxes -- which is how the Create page
    empties them -- without moving where the draft is stored.
    """
    fmt_label = read("format", "Text")
    fmt_label = fmt_label if fmt_label in ANSWER_FORMATS else "Text"
    answer_col, format_col = st.columns([3, 2])
    with answer_col:
        value = st.text_input(
            "Correct answer", value=read("answer", ""), key=f"{prefix}-answer{key_suffix}",
            placeholder="e.g. 6  ·  33.33  ·  Paris",
            **({"on_change": write, "args": (f"{prefix}-answer",)} if write else {}),
        )
    with format_col:
        fmt_label = st.selectbox(
            "Answer type", list(ANSWER_FORMATS), index=list(ANSWER_FORMATS).index(fmt_label),
            key=f"{prefix}-format{key_suffix}",
            help="Number grades 6, 6.0 and 6.00 as the same answer. Text ignores capitals and extra spaces.",
            **({"on_change": write, "args": (f"{prefix}-format",)} if write else {}),
        )
    answer_format = ANSWER_FORMATS[fmt_label]
    alternatives, allow_typos, tolerance, max_length = [], False, "", None
    with st.expander("Marking options"):
        if answer_format == grading.NUMBER:
            tolerance = st.text_input(
                "Accept answers within ±", value=read("tolerance", ""), key=f"{prefix}-tolerance{key_suffix}",
                placeholder="leave blank to use the answer's own precision",
                **({"on_change": write, "args": (f"{prefix}-tolerance",)} if write else {}),
            )
        else:
            alternatives = [
                item for item in st.text_input(
                    "Also accept (comma separated)", value=read("alternatives", ""),
                    key=f"{prefix}-alternatives{key_suffix}", placeholder="e.g. USA, US, America",
                    **({"on_change": write, "args": (f"{prefix}-alternatives",)} if write else {}),
                ).split(",")
            ]
            allow_typos = st.checkbox(
                "Forgive single-letter spelling slips", value=bool(read("typos", False)),
                key=f"{prefix}-typos{key_suffix}",
                **({"on_change": write, "args": (f"{prefix}-typos",)} if write else {}),
            )
        limit_raw = st.text_input(
            "Limit the answer box to (characters)", value=read("limit", ""), key=f"{prefix}-limit{key_suffix}",
            placeholder="leave blank to size it automatically",
            **({"on_change": write, "args": (f"{prefix}-limit",)} if write else {}),
        )
        max_length = int(limit_raw) if limit_raw.strip().isdigit() and int(limit_raw) > 0 else None
    spec = grading.build_spec(value, answer_format, tolerance or None, max_length, alternatives, allow_typos)
    st.caption(grading.teacher_summary(spec))
    return spec


@st.dialog("Delete assessment?")
def delete_quiz_dialog(user, quiz_id: int, title: str) -> None:
    # A dialog body is a fragment, so `main()`'s session check never re-runs for
    # the clicks inside it -- and the click inside this one destroys a quiz.
    if not require_session(user):
        return
    st.write(f"Delete **{title}** and all its questions, assignments, and results?")
    # Deleting takes every attempt with it, papers being written right now
    # included. The manager warns about those; the button that actually destroys
    # them said nothing at all.
    attempts = quiz_attempt_counts(quiz_id, exclude_student_id=user["id"])
    losses = []
    if attempts["open"]:
        losses.append(f"{attempts['open']} student{'s are' if attempts['open'] != 1 else ' is'} taking it right now")
    if attempts["submitted"]:
        losses.append(f"{attempts['submitted']} recorded result{'s' if attempts['submitted'] != 1 else ''} will be lost")
    if losses:
        st.warning(" and ".join(losses).capitalize() + ". This cannot be undone.")
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
                st.markdown(f"### {text(quiz['title'])} &nbsp;{badge}", unsafe_allow_html=True)
                audience = f"{assigned} assigned student{'s' if assigned != 1 else ''}" if assigned else "Not assigned"
                question_count = summary["questions"]
                minutes = quiz["duration_minutes"]
                st.caption(f"{question_count} question{'s' if question_count != 1 else ''}  ·  {minutes} minute{'s' if minutes != 1 else ''}  ·  pass at {quiz['passing_score']}%  ·  {audience}")
            with action:
                if st.button("Manage", key=f"manage-{quiz['id']}", width="stretch"):
                    # Opening the manager always shows what is actually stored.
                    reset_editor_state(quiz["id"])
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
            st.dataframe(pd.DataFrame([{"Student": row["student"], "Email": row["email"], "Status": row["status"], "Score": percent(row["score"]), "Result": row["result"], "Last activity": when(row["last_activity"])} for row in progress]), width="stretch", hide_index=True)
        else:
            st.info("No students are assigned to this exam yet.")


def _create_save_setting(name: str) -> None:
    widget_key = _ck(name)
    if _vanished(widget_key):
        return
    if "new_quiz_data" not in st.session_state:
        st.session_state["new_quiz_data"] = {}
    st.session_state["new_quiz_data"][name] = st.session_state[widget_key]
    st.session_state["create_dirty"] = True
    _mirror_draft(st.session_state["new_quiz_data"])


def _forget_question(form: dict, index: int) -> None:
    """Drop every field belonging to question `index` from the draft.

    Removing a question only shrank the count. Streamlit collects the widget
    state for the rows that stop rendering, but this mirror kept them, so
    re-adding a question brought the deleted one's text, options and answer
    back -- and published them.
    """
    exact = {f"new-type-{index}", f"new-text-{index}", f"new-correct-{index}",
             f"new-correct-mc-{index}", f"new-correct-tf-{index}", f"new-correct-all-{index}"}
    prefixes = (f"new-option-{index}-", f"new-typed-{index}-")
    for key in [key for key in form if key in exact or key.startswith(prefixes)]:
        form.pop(key, None)


# A publish that never finishes -- a dropped socket, a reload mid-write -- used
# to leave the guard set and both Publish buttons permanently disabled, with
# nothing on screen to explain it.
PUBLISH_GUARD_SECONDS = 30


def _publish_in_flight() -> bool:
    started = st.session_state.get("publish_in_flight")
    if not started:
        return False
    if datetime.now(timezone.utc).timestamp() - float(started) > PUBLISH_GUARD_SECONDS:
        st.session_state.pop("publish_in_flight", None)
        return False
    return True


def _clear_new_quiz_state() -> None:
    """Forget a draft completely, widgets included.

    Popping only `new_quiz_data` left every box on screen still filled in,
    because a Streamlit widget key outranks the `value=` it is re-rendered with
    -- so the next keystroke rebuilt the draft that was just discarded.
    """
    st.session_state.pop("new_quiz_data", None)
    st.session_state.pop("create_dirty", None)
    st.session_state.pop("new-quiz-section", None)
    st.session_state.pop("publish_in_flight", None)
    # Rename every box rather than deleting it. Deleting looked like the obvious
    # way to empty the form and could not work: the browser still holds what was
    # typed and re-sends it on the very next run, so Streamlit put it back under
    # the same key and the "discarded" draft was on screen again immediately.
    st.session_state[_CREATE_EPOCH] = int(st.session_state.get(_CREATE_EPOCH, 0)) + 1
    server_state.clear_draft(_draft_key(), DRAFT_NAME)


@st.fragment
def _create_questions_section(form: dict) -> None:
    """The question editor, in its own fragment.

    Every field commits on blur, and each commit used to re-run the whole
    script: the page scrolled back to the top, the navigation redrew and the
    settings tab's roster query went back to Supabase. That is what teachers
    described as the editor "reloading" while they were adding a question. A
    fragment redraws only this block instead, and because `form` is the same
    dict the rest of the page reads, publishing still sees every value.
    """
    with st.container(border=True):
        st.subheader("Questions")
        question_modes = ["Create manually", "Upload question bank"]
        stored_mode = form.get("new-quiz-mode", question_modes[0])
        # Without an explicit index a recovered draft always came back on
        # "Create manually", and the next interaction wrote that choice over
        # the real one -- so an uploaded bank published as an empty quiz.
        question_mode = st.radio("Add questions", question_modes, horizontal=True,
                                 index=question_modes.index(stored_mode) if stored_mode in question_modes else 0,
                                 key=_ck("new-quiz-mode"), on_change=_create_save_setting, args=("new-quiz-mode",))
        if question_mode == "Upload question bank":
            upload = st.file_uploader("Question bank (.txt or .docx)", type=["txt", "docx"], key=_ck("new-quiz-upload"))
            if upload and st.button("Read question bank", key="new-quiz-parse"):
                try:
                    questions, skipped = parse_report(extract_upload(upload))
                    form["new-uploaded-questions"] = questions
                    form["new-skipped-questions"] = skipped
                except Exception as exc:
                    form["new-uploaded-questions"] = []
                    form["new-skipped-questions"] = []
                    st.error(f"Could not read this question bank: {exc}")
                st.session_state["create_dirty"] = True
                # Everything else reaches the mirror through an on_change
                # hook; this is the one write that has to ask for itself.
                _mirror_draft(form)
            uploaded_questions = form.get("new-uploaded-questions", [])
            if not isinstance(uploaded_questions, list):
                uploaded_questions = []
            st.write(f"Questions ready: **{len(uploaded_questions)}**")
            skipped = form.get("new-skipped-questions") or []
            if skipped:
                with st.expander(f"{len(skipped)} question{'s were' if len(skipped) != 1 else ' was'} skipped"):
                    for note in skipped:
                        st.write(f"- {note}")
            if upload and not uploaded_questions:
                st.warning("No valid questions found. Include numbered questions, options, and an Answer Key before publishing.")
        else:
            count_key = "new-manual-count"
            if count_key not in form:
                form[count_key] = 1
            count = form[count_key]
            add_col, remove_col = st.columns(2)
            st.write(f"**{int(count)}** question{'s' if int(count) != 1 else ''} in this quiz")

            # `on_click` rather than an explicit `st.rerun()`: a button click
            # already causes a rerun, so calling it again ran the page twice and
            # threw the scroll position away a second time. Callbacks also run
            # before the body, so the loop below already sees the new count.
            def _add_question() -> None:
                form[count_key] = int(form.get(count_key, 1)) + 1
                st.session_state["create_dirty"] = True
                _mirror_draft(form)

            def _remove_question() -> None:
                current = int(form.get(count_key, 1))
                if current <= 1:
                    return
                form[count_key] = current - 1
                _forget_question(form, current - 1)
                st.session_state["create_dirty"] = True
                _mirror_draft(form)

            add_col.button("Add another question", key="new-manual-add", width="stretch",
                           on_click=_add_question)
            remove_col.button("Remove last question", key="new-manual-remove", width="stretch",
                              disabled=int(count) <= 1, on_click=_remove_question)
            for index in range(int(count)):
                with st.container(border=True):
                    st.markdown(f"**Question {index + 1}**")
                    current_type = form.get(f"new-type-{index}", "Multiple choice")
                    question_type = st.selectbox("Question type", QUESTION_TYPES, index=QUESTION_TYPES.index(current_type) if current_type in QUESTION_TYPES else 0, key=_ck(f"new-type-{index}"), on_change=_create_save_setting, args=(f"new-type-{index}",))
                    st.text_area("Question text", key=_ck(f"new-text-{index}"), height=80, value=form.get(f"new-text-{index}", ""), on_change=_create_save_setting, args=(f"new-text-{index}",))
                    if question_type in {"Multiple choice", SELECT_ALL_TYPE}:
                        option_cols = st.columns(4)
                        for option_index, label in enumerate(("A", "B", "C", "D")):
                            with option_cols[option_index]:
                                st.text_input(f"Option {label}", key=_ck(f"new-option-{index}-{label}"), value=form.get(f"new-option-{index}-{label}", ""), on_change=_create_save_setting, args=(f"new-option-{index}-{label}",))
                        if question_type == SELECT_ALL_TYPE:
                            st.multiselect("Correct answers", ["A", "B", "C", "D"], key=_ck(f"new-correct-all-{index}"), default=form.get(f"new-correct-all-{index}", []), on_change=_create_save_setting, args=(f"new-correct-all-{index}",))
                        else:
                            # One key per question type. Sharing a single key meant a
                            # multiple-choice "C" survived a switch to True / False,
                            # where it displayed as "True" but published as the stale
                            # letter -- a silently wrong answer key.
                            correct_cfg = ["A", "B", "C", "D"]
                            correct_val = form.get(f"new-correct-mc-{index}")
                            st.selectbox("Correct answer", correct_cfg, index=(correct_cfg.index(correct_val) if correct_val in correct_cfg else 0), key=_ck(f"new-correct-mc-{index}"), on_change=_create_save_setting, args=(f"new-correct-mc-{index}",))
                    elif question_type == "True / False":
                        tf_val = form.get(f"new-correct-tf-{index}")
                        st.selectbox("Correct answer", ["True", "False"], index=(0 if tf_val != "False" else 1), key=_ck(f"new-correct-tf-{index}"), on_change=_create_save_setting, args=(f"new-correct-tf-{index}",))
                    else:
                        typed_answer_editor(
                            f"new-typed-{index}",
                            lambda name, default, i=index: form.get(f"new-typed-{i}-{name}", default),
                            _create_save_typed,
                            key_suffix=_create_suffix(),
                        )


@st.fragment
def _create_settings_section(user, form: dict) -> None:
    """Quiz settings, in their own fragment for the same reason."""
    with st.container(border=True):
        st.subheader("Quiz settings")
        st.text_input("Quiz title", placeholder="e.g. Foundations of Computing", key=_ck("new-title"),
                      value=form.get("new-title", ""), on_change=_create_save_setting, args=("new-title",))
        first, second = st.columns(2)
        with first: st.number_input("Time allowed (minutes)", 1, 480, form.get("new-duration", 30), key=_ck("new-duration"), on_change=_create_save_setting, args=("new-duration",))
        with second: st.number_input("Passing score (%)", 0, 100, form.get("new-passing", 70), key=_ck("new-passing"), on_change=_create_save_setting, args=("new-passing",))
        st.checkbox("Allow retakes", form.get("new-retakes", False), key=_ck("new-retakes"), on_change=_create_save_setting, args=("new-retakes",))
        st.checkbox("Show class average to students", form.get("new-average", False), key=_ck("new-average"), on_change=_create_save_setting, args=("new-average",))
        st.checkbox("Randomize question order", form.get("new-randomize-questions", True), key=_ck("new-randomize-questions"), on_change=_create_save_setting, args=("new-randomize-questions",))
        st.checkbox("Randomize answer order", form.get("new-randomize-answers", True), key=_ck("new-randomize-answers"), on_change=_create_save_setting, args=("new-randomize-answers",))
        today = datetime.now().date()
        tomorrow = today + timedelta(days=1)
        opening_enabled = form.get("new-opening-enabled", True)
        st.checkbox("Enable opening date and time", opening_enabled, key=_ck("new-opening-enabled"), on_change=_create_save_setting, args=("new-opening-enabled",))
        opening_date, opening_time = st.columns(2)
        with opening_date: st.date_input("Opens on", form.get("new-opening-day", today), key=_ck("new-opening-day"), disabled=not opening_enabled, on_change=_create_save_setting, args=("new-opening-day",))
        with opening_time: st.time_input("Opening time", form.get("new-opening-clock", time(8, 0)), key=_ck("new-opening-clock"), disabled=not opening_enabled, on_change=_create_save_setting, args=("new-opening-clock",))
        closing_enabled = form.get("new-closing-enabled", True)
        st.checkbox("Enable closing date and time", closing_enabled, key=_ck("new-closing-enabled"), on_change=_create_save_setting, args=("new-closing-enabled",))
        closing_date, closing_time = st.columns(2)
        with closing_date: st.date_input("Closes on", form.get("new-closing-day", tomorrow), key=_ck("new-closing-day"), disabled=not closing_enabled, on_change=_create_save_setting, args=("new-closing-day",))
        with closing_time: st.time_input("Closing time", form.get("new-closing-clock", time(17, 0)), key=_ck("new-closing-clock"), disabled=not closing_enabled, on_change=_create_save_setting, args=("new-closing-clock",))
        st.divider(); st.subheader("Audience")
        audience_mode = st.radio("Assign to", ["All", "Students", "Teams"], horizontal=True, key=_ck("new-audience-mode"),
                                 index=0 if form.get("new-audience-mode") != "Students" and form.get("new-audience-mode") != "Teams" else 1 if form.get("new-audience-mode") == "Students" else 2,
                                 on_change=_create_save_setting, args=("new-audience-mode",))
        if audience_mode == "Students":
            st.multiselect("Assign to students", options=students(user["id"]), default=form.get("new-selected", []), format_func=lambda row: f"{row['name']}  ·  {row['email']}", key=_ck("new-selected"), on_change=_create_save_setting, args=("new-selected",))
        elif audience_mode == "Teams":
            st.multiselect("Assign to teams", options=teams_for_teacher(user["id"]), default=form.get("new-team-selected", []), format_func=lambda team: team["name"], key=_ck("new-team-selected"), on_change=_create_save_setting, args=("new-team-selected",))


def create(user) -> None:
    if "new_quiz_data" not in st.session_state:
        recovered = server_state.load_draft(_draft_key(), DRAFT_NAME)
        if recovered:
            st.session_state["new_quiz_data"] = dict(recovered)
            st.session_state["create_dirty"] = True
            st.session_state["draft_recovered"] = True
        else:
            st.session_state["new_quiz_data"] = {}
    form = st.session_state["new_quiz_data"]
    # One token per draft, minted here and carried in the mirror. Two Publish
    # clicks from the same draft -- in this tab, another tab, or after a
    # reconnect -- present the same token, and only the first one wins.
    if not form.get("new-publish-token"):
        form["new-publish-token"] = uuid.uuid4().hex
    # A publish that wrote the quiz but never got to clear up after itself --
    # the run was cut short, or the connection went between the write and the
    # rerun -- used to leave the teacher looking at "Publishing this quiz..."
    # with the buttons disabled and the finished draft still on screen, offering
    # to make the whole thing a second time. Nothing on the page could clear it
    # because nothing on the page could run. The claim knows better: if this
    # draft's token already produced a quiz, the work is done, and saying so is
    # the first thing this page does.
    if server_state.publish_result(_draft_key(), form.get("new-publish-token")) is not None:
        published_title = form.get("new-title", "").strip()
        _clear_new_quiz_state()
        st.session_state.pop("manage_quiz", None)
        st.session_state.page_override = "Dashboard"
        st.session_state.quiz_created = published_title
        st.rerun()
    page_header("New assessment", "Build an assessment", "Set it up first, then write the questions, and publish when everything is ready.")
    if st.session_state.pop("draft_recovered", False):
        st.info("Picked up where you left off — this quiz was still unpublished. Use **Discard draft** below if you'd rather start fresh.")
    publishing = _publish_in_flight()
    top_publish = st.button("Publish quiz", key="new-quiz-publish-top", type="primary", width="stretch",
                            disabled=publishing)
    if publishing:
        st.caption("Publishing this quiz...")
    section = st.session_state.get("new-quiz-section", "settings")
    settings_button, questions_button = st.columns(2)
    if settings_button.button("Quiz settings", key="new-quiz-settings", type="primary" if section == "settings" else "secondary", width="stretch"):
        st.session_state["new-quiz-section"] = "settings"
        st.rerun()
    if questions_button.button("Questions", key="new-quiz-questions", type="primary" if section == "questions" else "secondary", width="stretch"):
        st.session_state["new-quiz-section"] = "questions"
        st.rerun()

    if section == "questions":
        _create_questions_section(form)
    else:
        _create_settings_section(user, form)

    bottom_publish = st.button("Publish quiz", key="new-quiz-publish-bottom", type="primary",
                               width="stretch", disabled=publishing)
    # Always drawn, deliberately. This used to appear only once the draft held
    # something, which sounds right and did not work: the draft is filled in by
    # the settings and questions *fragments*, and a fragment re-run never
    # re-executes this body -- so the button stayed hidden however much the
    # teacher typed, until some unrelated click forced a full run. It looked
    # broken because it was missing. Making the fragment force a full run instead
    # would scroll the page back to the top on the first keystroke, which is the
    # very thing the fragments were introduced to stop. Pressing this with
    # nothing to discard costs nothing.
    if st.button("Discard draft", key="new-quiz-discard", width="stretch"):
        _clear_new_quiz_state()
        st.rerun()
    # A second click that arrives while the first is still being processed is a
    # double-click, not a second quiz.
    if publishing or not (top_publish or bottom_publish):
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
                correct = form.get(f"new-correct-all-{index}", []) if question_type == SELECT_ALL_TYPE else form.get(f"new-correct-mc-{index}", "A")
            elif question_type == "True / False":
                options = [("A", "True"), ("B", "False")]
                correct = "A" if form.get(f"new-correct-tf-{index}", "True") != "False" else "B"
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
    token = form.get("new-publish-token")
    claim = server_state.claim_publish(_draft_key(), token)
    if claim == "done":
        # Already published from another tab, or by a click that outlived its
        # session. Finish the navigation rather than making a second quiz.
        _clear_new_quiz_state()
        st.session_state.pop("manage_quiz", None)
        st.session_state.page_override = "Dashboard"
        st.session_state.quiz_created = title
        st.rerun()
    if claim == "in_flight":
        st.info("This quiz is already being published. Give it a moment.")
        return
    st.session_state["publish_in_flight"] = datetime.now(timezone.utc).timestamp()
    quiz_id = None
    try:
        quiz_id = create_quiz(user["id"], title, form.get("new-duration", 30), form.get("new-passing", 70), form.get("new-retakes", False), form.get("new-average", False), opening.isoformat(), closing.isoformat(), list(assigned_students), opening_enabled, closing_enabled, form.get("new-randomize-questions", True), form.get("new-randomize-answers", True))
        # Record the id the moment the row exists. Held back until the end, a run
        # that died in between left the claim holding `None`, which expires after
        # two minutes -- and the next click then published the whole quiz again.
        server_state.finish_publish(_draft_key(), token, quiz_id)
        save_question_bank(quiz_id, questions)
    except Exception as exc:
        # Publishing failed, so let the teacher try again with their work intact.
        # The quiz row goes in first and only becomes "active" once its questions
        # land, so a failure in between used to leave a questionless Draft on the
        # dashboard that nothing could finish. Take it back out again.
        if quiz_id is not None:
            try:
                delete_quiz(quiz_id, user["id"])
            except Exception:
                pass
        server_state.release_publish(_draft_key(), token)
        st.session_state.pop("publish_in_flight", None)
        st.error(f"The quiz could not be published: {exc}")
        return
    server_state.finish_publish(_draft_key(), token, quiz_id)
    _clear_new_quiz_state()
    st.session_state.pop("manage_quiz", None)
    st.session_state.page_override = "Dashboard"
    st.session_state.quiz_created = title
    st.rerun()


def _quiz_downloads(quiz, questions: list | None = None) -> None:
    """Export controls (CSV / DOCX / PDF / results) for the quiz manager.

    Every export is handed to `st.download_button` as a *callable*, so the file
    is built when someone clicks rather than on every rerun. That is not only
    cheaper — an expander's body still runs while collapsed, so each keystroke
    in the question editor used to re-render all three files — it is what makes
    the buttons work at all. A PDF and a DOCX both embed their creation time, so
    rebuilding them produced different bytes every rerun, and Streamlit derives
    a download's URL from its bytes. The link therefore moved on every rerun and
    Streamlit garbage-collected the one it replaced, so the URL the browser was
    still holding had usually 404'd by the time anyone clicked it: the first
    click on a freshly chosen format did nothing, and only the second worked.

    `on_click="ignore"` keeps a download from re-running the fragment, which
    would otherwise start that churn all over again on every click.
    """
    ready_key = f"downloads-ready-{quiz['id']}"
    if not st.session_state.get(ready_key):
        st.caption("Exports are built on request so they don't slow down editing.")
        if st.button("Prepare downloads", key=f"prepare-downloads-{quiz['id']}", width="stretch"):
            st.session_state[ready_key] = True
            st.rerun(scope="fragment")
        return
    progress = student_progress_for_quiz(quiz["owner_id"], quiz["id"])
    if progress:
        st.download_button("Download results CSV", lambda: _results_csv(progress), "student-results.csv", "text/csv", on_click="ignore", key=f"results-download-{quiz['id']}")
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
    format_key = format_map[pdf_format]
    # The chosen format belongs in the key. A deferred download registers its
    # builder afresh on every render and gets a new id each time, but the
    # *element* id comes from this key -- so with a fixed key the browser went on
    # holding the id it was given for the previous format, and the first click
    # after switching served the old file. Answers appeared only on a second
    # click, by which time a rerun had caught the button up. Putting the format
    # in the key makes it a different widget, which is the same cure as
    # everywhere else here: rename, don't reuse.
    st.download_button("Download PDF", lambda: _render_quiz_pdf(quiz, questions, format_key), "quiz.pdf", "application/pdf", type="primary", on_click="ignore", key=f"pdf-{quiz['id']}-{format_key}")
    st.download_button("Download question bank CSV", lambda: _question_bank_csv(questions), "question-bank.csv", "text/csv", on_click="ignore", key=f"bank-csv-{quiz['id']}")
    st.download_button("Download printable DOCX", lambda: _render_quiz_docx(quiz, questions), "quiz.docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document", on_click="ignore", key=f"docx-{quiz['id']}")


def _results_csv(progress) -> bytes:
    # One decimal, the same as the screen: a raw 33.33333333333333 in a
    # spreadsheet column helps nobody and still sorts correctly rounded.
    frame = pd.DataFrame([{"Student": row["student"], "Email": row["email"],
                           "Status": row["status"] if row.get("assigned", True) else f"{row['status']} · unassigned",
                           "Score": round(row["score"], 1) if row["score"] is not None else None,
                           "Result": row["result"], "Last activity": when(row["last_activity"])} for row in progress])
    return frame.to_csv(index=False).encode("utf-8")


def _question_bank_csv(questions) -> bytes:
    frame = pd.DataFrame([{"Question": q["question_text"], "Correct": q["correct_label"], **dict(json.loads(q["options_json"]))} for q in questions])
    return frame.to_csv(index=False).encode("utf-8")


def _render_quiz_docx(quiz, questions) -> bytes:
    document = Document(); document.add_heading(quiz["title"], 0)
    for index, q in enumerate(questions, 1):
        document.add_paragraph(f"{index}. {q['question_text']}")
        for label, option_text in json.loads(q["options_json"]): document.add_paragraph(f"{label}) {option_text}", style="List Bullet")
    output = io.BytesIO(); document.save(output)
    return output.getvalue()


@st.fragment
def manage_quiz(user, quiz_id: int) -> None:
    # Everything in here -- editing questions, publishing, changing settings,
    # reassigning students, regrading -- happens on fragment reruns, which never
    # re-execute the main script body. Without this check a tab signed out in
    # another window kept full write access to the quiz until someone refreshed.
    if not require_session(user):
        return
    with st.container(border=True):
        quiz = quiz_for_teacher(quiz_id, user["id"])
        if not quiz: return
        st.divider(); st.markdown(f"### Manage: {quiz['title']}")
        just_saved = st.session_state.pop(f"saved-questions-{quiz_id}", False)
        if just_saved == "key":
            st.success("Answer key saved. The questions are unchanged — anyone still working is marked "
                       "against the corrected key when they hand in.")
        elif just_saved:
            st.success("Test saved and published. The questions below are what students will now see.")
        attempts = quiz_attempt_counts(quiz_id, exclude_student_id=user["id"])
        if just_saved and attempts["submitted"]:
            # An attempt is marked against the key that was live when it was
            # handed in, so anyone who has already finished keeps the old marks
            # unless the teacher is told the earlier results are now out of date.
            st.warning(
                f"{attempts['submitted']} attempt{'s were' if attempts['submitted'] != 1 else ' was'} "
                "handed in under the previous answer key. Use **Regrade submitted attempts** below to re-mark "
                "them against this version."
            )
            st.session_state[f"regrade-prompt-{quiz_id}"] = True
        if attempts["open"]:
            st.info(
                f"{attempts['open']} student{'s have' if attempts['open'] != 1 else ' has'} this assessment open right now. "
                "Its questions are fixed while anyone is taking it; an answer key you correct is applied when they hand in."
            )
        with st.expander("Download"):
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
        regrade_controls(quiz, user, attempts["submitted"])
        if st.button("Close manager", key=f"close-{quiz_id}"):
            reset_editor_state(quiz_id)
            st.session_state.pop("manage_quiz", None)
            st.rerun()


def regrade_controls(quiz, user, submitted_count: int) -> None:
    """Re-mark already-submitted attempts against the quiz's current answer key.

    An attempt is marked against the key that was live when it was handed in, so
    fixing a wrong answer afterwards leaves everyone who has already finished
    scored under the mistake. This is the deliberate way to apply the correction
    backwards to them.
    """
    quiz_id = quiz["id"]
    result_key = f"regrade-result-{quiz_id}"
    # Clicking a button inside an expander re-runs the fragment, which closes the
    # expander again — so hold the outcome across that rerun and force it open.
    pending = st.session_state.get(result_key)
    prompted = st.session_state.pop(f"regrade-prompt-{quiz_id}", False)
    confirming = bool(st.session_state.get(f"regrade-confirm-{quiz_id}"))
    with st.expander("Regrade submitted attempts", expanded=bool(pending) or prompted or confirming):
        if pending:
            st.success(pending)
            st.session_state.pop(result_key, None)
        if not submitted_count:
            st.caption("Nobody has submitted this assessment yet, so there is nothing to regrade.")
            return
        st.caption(
            f"{submitted_count} submitted attempt{'s' if submitted_count != 1 else ''}. "
            "Attempts are marked against the answer key that was in place when they were handed in, so if you "
            "have corrected a wrong answer since, use this to apply the correction to results already recorded."
        )
        st.caption("Questions you have reworded or deleted since keep the marking they were graded under.")
        confirm_key = f"regrade-confirm-{quiz_id}"
        if not st.session_state.get(confirm_key):
            if st.button("Regrade now", key=f"regrade-{quiz_id}", type="primary", width="stretch"):
                st.session_state[confirm_key] = True
                st.rerun(scope="fragment")
            return
        # Regrading rewrites every recorded score and cannot be undone, so the
        # button that does it is not the same button the teacher first clicked.
        st.warning(
            "This rewrites the score and pass/fail result of "
            + (f"all {submitted_count} submitted attempts" if submitted_count != 1 else "the one submitted attempt")
            + ", and cannot be undone."
        )
        go, cancel = st.columns(2)
        if cancel.button("Cancel", key=f"regrade-cancel-{quiz_id}", width="stretch"):
            st.session_state.pop(confirm_key, None)
            st.rerun(scope="fragment")
        if go.button("Yes, regrade them", key=f"regrade-go-{quiz_id}", type="primary", width="stretch"):
            st.session_state.pop(confirm_key, None)
            summary = regrade_quiz(quiz_id, exclude_student_id=user["id"])
            message = (
                f"Regraded {summary['attempts']} attempt{'s' if summary['attempts'] != 1 else ''}; "
                f"{summary['changed']} score{'s' if summary['changed'] != 1 else ''} changed."
            )
            if summary["unmatched"]:
                message += (
                    f" {summary['unmatched']} question{'s' if summary['unmatched'] != 1 else ''} could not be matched "
                    "to the current quiz and kept the answer key they were marked against."
                )
            st.session_state[result_key] = message
            st.rerun(scope="fragment")


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
            f"Time allowed: {quiz['duration_minutes']} minute{'s' if quiz['duration_minutes'] != 1 else ''}",
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
        for label, option_text in json.loads(question["options_json"]):
            pdf.multi_cell(0, 5.5, _pdf_text(f"{label}) {option_text}"), new_x="LMARGIN", new_y="NEXT")
        if format_key in ("questions-answers", "questions-answers-settings"):
            pdf.set_font("Helvetica", "I", 10)
            pdf.set_text_color(23, 107, 82)
            pdf.multi_cell(0, 5.5, _pdf_text(f"Correct answer: {_format_correct(question)}"), new_x="LMARGIN", new_y="NEXT")
            pdf.set_text_color(0, 0, 0)
        pdf.ln(4)

    return bytes(pdf.output())


def question_bank(quiz) -> None:
    questions = questions_for_quiz(quiz["id"])
    # The same rule the settings follow, for the same reason. `save_question_bank`
    # is what enforces it; this only keeps the teacher from typing out an edit
    # that would be refused, and says why.
    locked = bool(questions) and quiz_has_attempts(quiz["id"], exclude_student_id=quiz["owner_id"])
    if locked:
        st.info(
            "A student has started this assessment, so its **questions are now fixed** \u2014 the same way its "
            "settings are. You can still correct an answer key below, and **Regrade submitted attempts** applies "
            "the correction to results already recorded. To change the questions themselves, delete this "
            "assessment and publish a new one. Your own preview attempts from Student view don't count."
        )
    if questions and not locked:
        with st.expander("Reorder questions"):
            question_options = {question["id"]: f"{index}. {question['question_text']}" for index, question in enumerate(questions, 1)}
            selected_id = st.selectbox("Question", list(question_options), format_func=question_options.get, key=f"reorder-question-{quiz['id']}-{editor_epoch(quiz['id'])}")
            # A saved quiz replaces every question row, so a remembered id can be
            # gone. Falling back to the first question beats a StopIteration that
            # would take the whole manager down with it.
            selected_index = next((index for index, question in enumerate(questions) if question["id"] == selected_id), 0)
            move_up, move_down = st.columns(2)
            def _move(direction: int) -> None:
                try:
                    move_question(quiz["id"], selected_id, direction)
                except ValueError as exc:
                    st.error(str(exc))
                    return
                reset_editor_state(quiz["id"])
                st.rerun(scope="fragment")

            if move_up.button("Move up", key=f"move-up-{quiz['id']}", disabled=selected_index == 0, width="stretch"):
                _move(-1)
            if move_down.button("Move down", key=f"move-down-{quiz['id']}", disabled=selected_index == len(questions) - 1, width="stretch"):
                _move(1)
    if locked:
        # An upload replaces the whole paper, so there is nothing it could do here
        # that the lock would allow.
        manual_question_editor(quiz, locked=True)
        return
    mode = st.radio("How would you like to add questions?", ["Create manually", "Upload question bank"], horizontal=True, key=f"question-mode-{quiz['id']}")
    if mode == "Create manually":
        manual_question_editor(quiz)
        return
    upload = st.file_uploader("Upload question bank (.txt or .docx)", type=["txt", "docx"], key=f"upload-{quiz['id']}")
    if upload and st.button("Parse question bank", type="primary", key=f"parse-{quiz['id']}"):
        try:
            parsed, skipped = parse_report(extract_upload(upload))
            st.session_state[f"draft-{quiz['id']}"] = parsed
            st.session_state[f"draft-skipped-{quiz['id']}"] = skipped
        except Exception as exc:
            st.session_state[f"draft-{quiz['id']}"] = []
            st.session_state[f"draft-skipped-{quiz['id']}"] = []
            st.error(f"Could not read this question bank: {exc}")
    draft = st.session_state.get(f"draft-{quiz['id']}")
    if draft is not None:
        if not draft:
            st.warning("No valid questions found. Include numbered questions, options, and an Answer Key before publishing.")
            return
        skipped = st.session_state.get(f"draft-skipped-{quiz['id']}") or []
        if skipped:
            with st.expander(f"{len(skipped)} question{'s were' if len(skipped) != 1 else ' was'} skipped"):
                for note in skipped:
                    st.write(f"- {note}")
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
                try:
                    save_question_bank(quiz["id"], questions)
                except ValueError as exc:
                    st.error(str(exc))
                else:
                    st.session_state.pop(f"draft-{quiz['id']}", None)
                    st.session_state.pop(f"draft-skipped-{quiz['id']}", None)
                    reset_editor_state(quiz["id"])
                    st.session_state[f"saved-questions-{quiz['id']}"] = "paper"
                    # A full rerun, not a fragment one: the quiz card above the
                    # manager shows the question count and the Draft/Published
                    # badge, and neither is redrawn by a fragment rerun -- which
                    # is why a save looked like it had not taken until a refresh.
                    st.rerun()
        return
    st.caption(f"Question bank: {len(questions)} questions")
    for index, question in enumerate(questions, 1):
        options = json.loads(question["options_json"])
        st.write(f"**{index}. {question['question_text']}**")
        st.caption("  ·  ".join(f"{label}) {text}" for label, text in options))


_UPLOAD_OPTION = re.compile(r"^\s*([A-F])\s*\)\s*(.*)$", re.I)


def _options_from_cell(cell: str) -> list[tuple[str, str]]:
    """Read the review table's "A) one | B) two" cell back into labelled options.

    The letters in that cell are the ones the `Correct` column points at, so they
    have to survive the round trip. Re-lettering by position instead moved the
    answer key on to whichever option happened to land in that slot: a bank
    listing its options B, A, C silently marked the wrong one correct, and an
    option whose own text contained a "|" split in two and did the same. Neither
    could be caught by validation, because the relabelled set always contains the
    key. A cell with no letters at all is still lettered by position -- that is a
    teacher typing plain alternatives, and position is all there is to go on.
    """
    parts = str(cell or "").split("|")
    if not any(_UPLOAD_OPTION.match(part) for part in parts):
        return [(chr(65 + index), part.strip()) for index, part in enumerate(parts) if part.strip()]
    options: list[list[str]] = []
    for part in parts:
        match = _UPLOAD_OPTION.match(part)
        if match:
            options.append([match.group(1).upper(), match.group(2).strip()])
        elif options:
            # No letter of its own: the tail of an option whose text held a "|".
            options[-1][1] = f"{options[-1][1]} | {part.strip()}".strip(" |")
        elif part.strip():
            options.append(["A", part.strip()])
    taken: set[str] = set()
    labelled = []
    for label, option_text in options:
        if not option_text:
            continue
        if label in taken:
            label = next((letter for letter in "ABCDEF" if letter not in taken), label)
        taken.add(label)
        labelled.append((label, option_text))
    return labelled


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
            options = _options_from_cell(row.get("Options", ""))
        if question_type == SELECT_ALL_TYPE:
            correct = [part.strip().upper() for part in correct_raw.replace("|", ",").split(",") if part.strip()]
        else:
            correct = correct_raw.upper()[:1]
        questions.append({
            "question_text": text, "options": options,
            "correct_label": correct, "question_type": question_type,
        })
    return questions


def _blank_question() -> dict:
    return {
        "type": "Multiple choice",
        "text": "",
        "options": {"A": "", "B": "", "C": "", "D": ""},
        "correct": "A",
        "correct_all": [],
        "typed": {"answer": "", "format": "Text", "tolerance": "", "alternatives": "", "typos": False, "limit": ""},
    }


def _question_to_draft(question) -> dict:
    """Turn a stored question row into the editor's working shape."""
    entry = _blank_question()
    question_type = question.get("question_type", "Multiple choice")
    entry["type"] = question_type if question_type in QUESTION_TYPES else "Multiple choice"
    entry["text"] = question["question_text"]
    try:
        stored_options = json.loads(question["options_json"])
    except (TypeError, ValueError):
        stored_options = []
    for label, value in stored_options:
        if label in entry["options"]:
            entry["options"][label] = value
    if entry["type"] == SELECT_ALL_TYPE:
        try:
            entry["correct_all"] = json.loads(question["correct_label"])
        except (TypeError, ValueError):
            entry["correct_all"] = []
    elif entry["type"] in TEXT_QUESTION_TYPES:
        spec = grading.answer_spec(question)
        entry["typed"] = {
            "answer": spec.get("value", ""),
            "format": "Number" if spec.get("format") == grading.NUMBER else "Text",
            "tolerance": str(spec.get("tolerance") or ""),
            "alternatives": ", ".join(spec.get("alternatives") or []),
            "typos": bool(spec.get("allow_typos")),
            "limit": str(spec.get("max_length") or ""),
        }
    else:
        entry["correct"] = question["correct_label"]
    return entry


def editor_state_key(quiz_id: int) -> str:
    return f"question-editor-{quiz_id}"


def editor_epoch(quiz_id: int) -> int:
    """A number folded into every editor widget key, bumped on every reset.

    Deleting a widget's key from `st.session_state` does not clear the value:
    the browser still holds it and sends it back with the next rerun, and
    Streamlit restores it. Changing the *key* is the only thing that gives a
    widget a genuinely fresh start -- which is what a reordered or reloaded
    question bank needs, or the boxes keep showing the pre-edit version and the
    next save writes that back to the database.
    """
    return int(st.session_state.get(f"editor-epoch-{quiz_id}", 0))


def reset_editor_state(quiz_id: int) -> None:
    """Forget the working copy so the editor reloads from the database.

    Called whenever the manager is opened and after every save. Without this the
    editor kept showing whatever the browser session happened to hold the first
    time it ran, which is how saved edits appeared to vanish until a refresh.

    The working copy is only half of what the editor remembers. Every field also
    has a Streamlit widget, whose value beats the `value=`/`index=` the reloaded
    draft passes in -- so a reordered question came straight back on the next
    render, and the next save wrote that stale order back to the database.
    Bumping `editor_epoch` renames every widget, which is the only thing that
    really gives them a fresh start: the browser still holds the old values and
    re-sends them, so nothing short of a new key escapes them.

    The old keys are deliberately *not* deleted. Dropping them looked like tidying
    up after a rename that had already done the work, and it was the cause of the
    blank white page after a save: the browser is a render behind, so it sends the
    old widgets' values into the next run, Streamlit calls their `on_change`
    callbacks, and those callbacks then read keys that are no longer there. They
    guard against that too -- see `_vanished` -- but the fix worth having is not
    creating the situation.

    Prepared exports are dropped at the same time: they were built from the old
    questions, and rebuilding them on every keystroke is what made the editor
    feel like it was reloading.
    """
    st.session_state.pop(editor_state_key(quiz_id), None)
    st.session_state.pop(f"downloads-ready-{quiz_id}", None)
    st.session_state[f"editor-epoch-{quiz_id}"] = editor_epoch(quiz_id) + 1


def _editor_draft(quiz) -> list[dict]:
    key = editor_state_key(quiz["id"])
    if key not in st.session_state:
        stored = [_question_to_draft(question) for question in questions_for_quiz(quiz["id"])]
        st.session_state[key] = stored or [_blank_question()]
    return st.session_state[key]


def _draft_to_questions(draft: list[dict]) -> list[dict]:
    """Turn the editor's working copy into the shape `save_question_bank` wants."""
    questions = []
    for entry in draft:
        question_type = entry["type"]
        if question_type == "True / False":
            options = [("A", "True"), ("B", "False")]
            correct = entry.get("correct") if entry.get("correct") in ("A", "B") else "A"
        elif question_type in TEXT_QUESTION_TYPES:
            options = []
            typed = entry["typed"]
            limit = str(typed.get("limit", "")).strip()
            correct = grading.build_spec(
                typed.get("answer", ""),
                ANSWER_FORMATS.get(typed.get("format", "Text"), grading.TEXT),
                (typed.get("tolerance") or "").strip() or None,
                int(limit) if limit.isdigit() and int(limit) > 0 else None,
                str(typed.get("alternatives", "")).split(","),
                bool(typed.get("typos")),
            )
        else:
            options = [(label, entry["options"].get(label, "").strip()) for label in ("A", "B", "C", "D")]
            options = [(label, value) for label, value in options if value]
            if question_type == SELECT_ALL_TYPE:
                correct = list(entry.get("correct_all") or [])
            else:
                available = [label for label, _ in options]
                correct = entry.get("correct") if entry.get("correct") in available else (available[0] if available else "")
        questions.append({
            "question_text": entry["text"].strip(),
            "options": options,
            "correct_label": correct,
            "question_type": question_type,
        })
    return questions


def manual_question_editor(quiz, locked: bool = False) -> None:
    if locked:
        st.caption("Correct an answer below and save. The questions and their options are fixed.")
        if any(entry["type"] in TEXT_QUESTION_TYPES for entry in _editor_draft(quiz)):
            # The note under a typed answer box is derived from the answer, so
            # some answer corrections move it -- and moving it changes the
            # question the student is sitting. Say so before they type, rather
            # than only when the save is refused.
            st.caption("For typed questions, keep the same precision and length: the note under the "
                       "student's answer box is built from your answer, and changing it changes what "
                       "they were asked.")
    else:
        st.caption("Create the test directly. Each question needs text, at least two options, and one correct answer.")
    draft = _editor_draft(quiz)
    quiz_id = quiz["id"]
    epoch = editor_epoch(quiz_id)

    def _write(field: str, index: int, widget_key: str, sub: str | None = None) -> None:
        """Copy a widget's value back into the working copy it was rendered from."""
        if _vanished(widget_key) or index >= len(draft):
            return
        value = st.session_state[widget_key]
        if sub is None:
            draft[index][field] = value
        else:
            draft[index][field][sub] = value

    def _add_question() -> None:
        draft.append(_blank_question())

    def _remove_question() -> None:
        if len(draft) > 1:
            draft.pop()

    st.write(f"**{len(draft)}** question{'s' if len(draft) != 1 else ''} in this quiz")
    add_col, remove_col = st.columns(2)
    add_col.button("Add another question", key=f"manual-add-{quiz_id}", width="stretch",
                   on_click=_add_question, disabled=locked)
    remove_col.button("Remove last question", key=f"manual-remove-{quiz_id}", width="stretch",
                      on_click=_remove_question, disabled=locked or len(draft) <= 1)

    for index, entry in enumerate(draft):
        with st.container(border=True):
            st.markdown(f"**Question {index + 1}**")
            type_key = f"manual-type-{quiz_id}-{epoch}-{index}"
            question_type = st.selectbox(
                "Question type", QUESTION_TYPES,
                index=QUESTION_TYPES.index(entry["type"]) if entry["type"] in QUESTION_TYPES else 0,
                key=type_key, on_change=_write, args=("type", index, type_key), disabled=locked,
            )
            entry["type"] = question_type
            text_key = f"manual-text-{quiz_id}-{epoch}-{index}"
            entry["text"] = st.text_area("Question text", value=entry["text"], height=80,
                                         key=text_key, on_change=_write, args=("text", index, text_key),
                                         disabled=locked)
            if question_type in {"Multiple choice", SELECT_ALL_TYPE}:
                columns = st.columns(4)
                for option_index, label in enumerate(("A", "B", "C", "D")):
                    with columns[option_index]:
                        option_key = f"manual-option-{quiz_id}-{epoch}-{index}-{label}"
                        entry["options"][label] = st.text_input(
                            f"Option {label}", value=entry["options"].get(label, ""),
                            key=option_key, on_change=_write, args=("options", index, option_key, label),
                            disabled=locked,
                        )
            if question_type == "True / False":
                tf_key = f"manual-correct-tf-{quiz_id}-{epoch}-{index}"
                entry["correct"] = st.selectbox(
                    "Correct answer", ["A", "B"], format_func=lambda value: "True" if value == "A" else "False",
                    index=1 if entry.get("correct") == "B" else 0,
                    key=tf_key, on_change=_write, args=("correct", index, tf_key),
                )
            elif question_type == SELECT_ALL_TYPE:
                all_key = f"manual-correct-all-{quiz_id}-{epoch}-{index}"
                entry["correct_all"] = st.multiselect(
                    "Correct answers", ["A", "B", "C", "D"],
                    default=[label for label in (entry.get("correct_all") or []) if label in ("A", "B", "C", "D")],
                    key=all_key, on_change=_write, args=("correct_all", index, all_key),
                )
            elif question_type == "Multiple choice":
                available = [label for label in ("A", "B", "C", "D") if entry["options"].get(label, "").strip()] or ["A", "B", "C", "D"]
                stored = entry.get("correct")
                mc_key = f"manual-correct-mc-{quiz_id}-{epoch}-{index}"
                entry["correct"] = st.selectbox(
                    "Correct answer", available,
                    index=available.index(stored) if stored in available else 0,
                    key=mc_key, on_change=_write, args=("correct", index, mc_key),
                )
            else:
                prefix = f"manual-typed-{quiz_id}-{epoch}-{index}"

                def _write_typed(widget_key: str, i=index, p=prefix) -> None:
                    if _vanished(widget_key) or i >= len(draft):
                        return
                    draft[i]["typed"][widget_key[len(p) + 1:]] = st.session_state[widget_key]

                typed_answer_editor(
                    prefix,
                    lambda name, default, store=entry["typed"]: store.get(name, default),
                    _write_typed,
                )
                # Marking options only render for one answer format at a time, so
                # copy across whichever controls actually appeared this run.
                for field in ("answer", "format", "tolerance", "alternatives", "typos", "limit"):
                    if f"{prefix}-{field}" in st.session_state:
                        entry["typed"][field] = st.session_state[f"{prefix}-{field}"]

    questions = _draft_to_questions(draft)
    save_label = "Save answer key" if locked else "Save manually created test"
    if st.button(save_label, type="primary", key=f"manual-save-{quiz_id}", width="stretch"):
        errors = _question_errors(questions)
        if errors:
            st.error(" ".join(errors))
            return
        try:
            save_question_bank(quiz_id, questions)
        except ValueError as exc:
            st.error(str(exc))
            return
        reset_editor_state(quiz_id)
        st.session_state[f"saved-questions-{quiz_id}"] = "key" if locked else "paper"
        # Full rerun: the card above the manager carries the question count and
        # the Draft/Published badge, and a fragment rerun leaves both stale.
        st.rerun()


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
        all_teams = teams_for_teacher(quiz["owner_id"])
        # Pre-tick the teams already covered by the assignment, the way the
        # Students tab pre-ticks assigned students. Starting empty made it easy
        # to hit Save and unassign everyone.
        already = [team for team in all_teams
                   if (members := team_student_ids(team["id"])) and members <= current]
        selected_teams = st.multiselect("Assigned teams", options=all_teams, default=already,
                                        format_func=lambda team: team["name"], key=f"assigned-teams-{quiz['id']}")
        selected_ids = list(student_ids_for_teams(quiz["owner_id"], [team["id"] for team in selected_teams]))
    if st.button("Save assignment", type="primary", key=f"assign-save-{quiz['id']}"):
        set_quiz_assignments(quiz["id"], selected_ids)
        count = len(set(selected_ids))
        if count:
            # Say the number out loud: picking "Teams" and saving without
            # choosing one silently unassigned the whole class.
            st.success(f"Assignment updated — {count} student{'s' if count != 1 else ''} can see this assessment.")
        else:
            st.warning("Assignment updated — no students are assigned now, so nobody can see this assessment.")


def results(quiz) -> None:
    progress = student_progress_for_quiz(quiz["owner_id"], quiz["id"])
    if not progress:
        st.info("No students are assigned to this exam yet."); return
    frame = pd.DataFrame([{"Student": row["student"], "Email": row["email"],
                           "Status": row["status"] if row.get("assigned", True) else f"{row['status']} · unassigned",
                           "Score": percent(row["score"]), "Result": row["result"],
                           "Last activity": when(row["last_activity"])} for row in progress])
    st.dataframe(frame, width="stretch", hide_index=True)


def roster_page(user) -> None:
    roster = students(user["id"])
    performance = student_analytics(user["id"])
    teams = teams_for_teacher(user["id"])
    page_header("Teacher workspace", "Student roster", "Students who choose you appear here automatically. You can also add them yourself.")
    team_options = {team["id"]: team["name"] for team in teams}
    with st.container(border=True):
        st.subheader("Add an existing student to roster")
        show_flash("roster-existing")
        lookup = st.text_input("Search student", placeholder="Search by name or email", key="existing-student-search")
        matches = [row for row in students() if not lookup.strip() or lookup.lower() in row["name"].lower() or lookup.lower() in row["email"].lower()]
        if matches:
            existing = st.selectbox("Find student", matches, format_func=lambda row: f"{row['name']} · {row['email']}", key="existing-student")
            existing_team_id = st.selectbox("Add to team", [None, *team_options], format_func=lambda value: "No team" if value is None else team_options[value], key="existing-student-team")
            if st.button("Add an existing student to roster", type="primary", width="stretch"):
                add_student_to_roster(user["id"], existing["name"], existing["email"], existing_team_id)
                flash("roster-existing", f"{existing['name']} was added to your roster.")
                st.rerun()
        elif lookup.strip():
            st.info("No existing student matches that search.")
    with st.container(border=True):
        st.subheader("Add a new student")
        show_flash("roster-new")
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
                    flash("roster-new", f"{name.strip()} was added to your roster."); st.rerun()
    with st.container(border=True):
        st.subheader("Teams")
        st.caption("Choose a team to see its members. Use the optional search to add someone without opening the member list.")
        for team in teams:
            member_ids = team_student_ids(team["id"])
            # The epoch renames the box after every write. A Streamlit widget key
            # outranks the `default=` it is re-rendered with, so after "Add
            # member" the list still showed the membership from before the click
            # -- contradicting the header right above it -- and "Save members"
            # then wrote that stale list back, silently deleting the person who
            # had just been added while reporting "Team updated."
            epoch = int(st.session_state.get(f"team-epoch-{team['id']}", 0))
            with st.expander(f"{team['name']} · {len(member_ids)} member{'s' if len(member_ids) != 1 else ''}"):
                members = st.multiselect("Members", roster, default=[row for row in roster if row["id"] in member_ids], format_func=lambda row: f"{row['name']} · {row['email']}", key=f"team-members-{team['id']}-{epoch}")
                show_search = st.checkbox("Show search to add a member", key=f"show-team-search-{team['id']}")
                if show_search:
                    member_search = st.text_input("Search roster", placeholder="Search by name or email", key=f"team-search-{team['id']}")
                    matches = [row for row in roster if not member_search.strip() or member_search.lower() in row["name"].lower() or member_search.lower() in row["email"].lower()]
                    if matches:
                        candidate = st.radio("Add member", matches, format_func=lambda row: f"{row['name']} · {row['email']}", key=f"team-candidate-{team['id']}")
                        if st.button("Add member", key=f"add-team-member-{team['id']}"):
                            members = [*members, candidate] if candidate["id"] not in {row["id"] for row in members} else members
                            set_team_members(team["id"], [row["id"] for row in members])
                            st.session_state[f"team-epoch-{team['id']}"] = epoch + 1
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
    if not require_session(user):
        return
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
            "Score": percent(row["score_percent"]),
            "Result": "Passed" if row["passed"] else ("Failed" if row["passed"] is not None else "-"),
            "Last activity": when(row["submitted_at"] or row["started_at"]),
        }
        for row in detail["results"]
    ]
    if results_data:
        st.dataframe(pd.DataFrame(results_data), width="stretch", hide_index=True)
    else:
        st.info("No test results yet.")
