"""Student dashboard and assessment-taking experience."""

from __future__ import annotations

import json
import random
from datetime import datetime, timedelta, timezone

import streamlit as st

import grading
from repository import (all_teachers, attempt_with_quiz, attempts_for_student, available_quizzes,
                        complete_attempt, create_attempt, join_teacher, leave_teacher,
                        open_attempt, questions_for_quiz, quiz_average_score,
                        save_attempt_answers, search_teachers, teachers_for_student)
from ui import empty_state, metric_row, page_header, pill, sign_out


SELECT_ALL_TYPE = "Multiple choice - select all that apply"
TEXT_ANSWER_TYPES = {"Fill in the blank", "Short answer"}


def current_time() -> datetime:
    return datetime.now(timezone.utc)


def needs_a_teacher(user) -> bool:
    """Students must be attached to at least one teacher before they can do anything."""
    return not teachers_for_student(user["id"])


def _teacher_card(teacher, action_label: str, key: str, primary: bool = False) -> bool:
    with st.container(border=True):
        details, action = st.columns([5, 1.4], vertical_alignment="center")
        with details:
            st.markdown(f"**{teacher['name']}**")
            st.caption(teacher["email"])
        with action:
            return action.button(action_label, key=key, type="primary" if primary else "secondary", width="stretch")


def choose_teacher_page(user) -> None:
    """Blocking first-run step: a student picks the teacher whose class they are in."""
    first = (user.get("name") or "").split()[0] if user.get("name") else "there"
    top, out = st.columns([6, 1], vertical_alignment="center")
    with top:
        page_header("Step 1 of 1", f"Welcome, {first}.", "Choose the teacher whose assessments you'll be taking. You can add more teachers or change this later.")
    with out:
        if st.button("Sign out", key="choose-teacher-sign-out", width="stretch"):
            sign_out()
    st.divider()

    search = st.text_input("Find your teacher", placeholder="Search by name or email", key="choose-teacher-search")
    everyone = all_teachers(user["id"])
    if search.strip():
        needle = search.strip().lower()
        matches = [row for row in everyone if needle in row["name"].lower() or needle in row["email"].lower()]
    else:
        matches = everyone

    if not everyone:
        empty_state("No teachers have signed up yet", "Ask your teacher to create their account first, then come back and refresh this page.")
        return
    if not matches:
        empty_state("No teacher matches that search", "Check the spelling, or try searching by their email address instead.")
        return

    st.caption(f"{len(matches)} teacher{'s' if len(matches) != 1 else ''} to choose from")
    for teacher in matches[:40]:
        if _teacher_card(teacher, "Choose", f"choose-teacher-{teacher['id']}", primary=True):
            join_teacher(user["id"], teacher["id"])
            st.session_state.joined_teacher = teacher["name"]
            st.rerun()
    if len(matches) > 40:
        st.caption("Showing the first 40 — search to narrow the list.")


def dashboard(user) -> None:
    previewing = user["role"] == "teacher"
    if previewing:
        page_header("Student view", "What your students see", "A preview of the student workspace. Attempts you make here are excluded from your analytics.")
    else:
        page_header("Your workspace", "Ready when you are.", "Everything your teachers have assigned, and how you did.")
    joined = st.session_state.pop("joined_teacher", None)
    if joined:
        st.success(f"You've joined **{joined}**. Their assessments will appear here.")
    timed_out = st.session_state.pop("time_up_title", None)
    if timed_out:
        st.warning(f"Time ran out on **{timed_out}**. Your answers were submitted automatically.")

    quizzes = available_quizzes(user["id"], user["id"] if previewing else None)
    attempts = attempts_for_student(user["id"])
    latest = {}
    for attempt in attempts:
        latest.setdefault(attempt["quiz_id"], attempt)

    if not previewing:
        done = [a for a in attempts if a["submitted_at"] and a["score_percent"] is not None]
        scores = [a["score_percent"] for a in done]
        to_do = sum(1 for quiz in quizzes if not (latest.get(quiz["id"]) or {}).get("submitted_at"))
        metric_row([
            (to_do, "To do"),
            (len(done), "Completed"),
            (f"{sum(scores) / len(scores):.0f}%" if scores else "—", "Average score"),
            (sum(1 for a in done if a["passed"]), "Passed"),
        ])
        st.divider()

    if not quizzes:
        empty_state(
            "Nothing to take right now",
            "When one of your teachers assigns an assessment it will appear here. "
            "Check My teachers to make sure you're connected to the right people.",
        )
    for quiz in quizzes:
        attempt = latest.get(quiz["id"])
        submitted = bool(attempt and attempt["submitted_at"])
        with st.container(border=True):
            details, action = st.columns([4, 1.2], vertical_alignment="center")
            with details:
                if submitted:
                    tone, label = ("green", "Passed") if attempt["passed"] else ("amber", "Try again")
                elif attempt:
                    tone, label = "amber", "In progress"
                else:
                    tone, label = "grey", "Not started"
                st.markdown(f"### {quiz['title']} &nbsp;{pill(label, tone)}", unsafe_allow_html=True)
                closes = (
                    f"closes {datetime.fromisoformat(quiz['closing_time']).astimezone().strftime('%b %d, %I:%M %p')}"
                    if quiz["closing_enabled"] else "no closing date"
                )
                bits = [f"{quiz['duration_minutes']} minutes", f"pass at {quiz['passing_score']}%", closes]
                if quiz["allow_retake"]:
                    bits.append("retakes allowed")
                st.caption("  ·  ".join(bits))
                if submitted:
                    average = ""
                    if quiz["show_average"]:
                        class_average = quiz_average_score(quiz["id"])
                        if class_average is not None:
                            average = f"  ·  class average {class_average:.0f}%"
                    st.markdown(f"Your score: **{attempt['score_percent']:.0f}%**{average}")
            with action:
                if submitted and not quiz["allow_retake"]:
                    st.caption("Completed — no retakes")
                else:
                    label = "Resume" if attempt and not submitted else ("Retake" if submitted else "Start quiz")
                    if st.button(label, key=f"start-{quiz['id']}", type="primary", width="stretch"):
                        start_attempt(user, quiz)
                        st.rerun()
    if st.session_state.get("attempt_id"):
        take_attempt(user, st.session_state.attempt_id)


@st.fragment
def my_teachers_page(user) -> None:
    page_header("Your workspace", "My teachers", "The teachers whose assessments you receive. Add another at any time.")
    current = teachers_for_student(user["id"])
    st.subheader("Your teachers")
    if current:
        for teacher in current:
            if _teacher_card(teacher, "Leave", f"leave-{teacher['id']}"):
                if len(current) == 1:
                    st.warning("You need at least one teacher. Add another before leaving this one.")
                else:
                    leave_teacher(user["id"], teacher["id"])
                    st.rerun(scope="fragment")
    else:
        empty_state("No teachers yet", "Search below to connect with the teacher running your class.")
    st.divider()
    st.subheader("Add another teacher")
    search = st.text_input("Search teachers", placeholder="Search by name or email", key="join-teacher-search")
    matches = search_teachers(user["id"], search)
    if not search.strip():
        st.caption("Start typing a name or email address to find a teacher.")
    elif not matches:
        st.info("No teachers match that search.")
    else:
        for teacher in matches[:20]:
            if _teacher_card(teacher, "Join", f"join-{teacher['id']}", primary=True):
                join_teacher(user["id"], teacher["id"])
                st.rerun(scope="fragment")


def start_attempt(user, quiz) -> None:
    existing = open_attempt(quiz["id"], user["id"])
    if existing:
        st.session_state.attempt_id = existing["id"]; return
    questions = list(questions_for_quiz(quiz["id"]))
    if quiz["randomize_questions"]: random.shuffle(questions)
    frozen = []
    for question in questions:
        options = json.loads(question["options_json"])
        question_type = question["question_type"]
        if question_type in TEXT_ANSWER_TYPES:
            # The answer specification never leaves the server: the frozen copy
            # keeps only what the student's answer box needs to render.
            spec = grading.answer_spec(question)
            correct = spec
            options = []
        elif question_type == SELECT_ALL_TYPE:
            correct = json.loads(question["correct_label"])
            if quiz["randomize_answers"]: random.shuffle(options)
        else:
            correct = question["correct_label"]
            # True/False keeps its natural order; shuffling it just reads oddly.
            if quiz["randomize_answers"] and question_type != "True / False":
                random.shuffle(options)
        entry = {"text": question["question_text"], "options": options,
                 "correct": correct, "question_type": question_type}
        if question_type in TEXT_ANSWER_TYPES:
            entry["hint"] = grading.student_hint(correct)
            entry["limit"] = grading.input_limit(correct)
        frozen.append(entry)
    started = current_time()
    deadline = started + timedelta(minutes=quiz["duration_minutes"])
    if quiz["closing_enabled"]:
        deadline = min(deadline, datetime.fromisoformat(quiz["closing_time"]))
    st.session_state.attempt_id = create_attempt(
        quiz["id"], user["id"], started.isoformat(), deadline.isoformat(),
        json.dumps({"questions": frozen, "answers": {}})
    )


def _answer_from_widget(question: dict, value):
    """Normalise one answer widget's value into what the payload stores.

    Returns `None` when the question should count as unanswered.
    """
    question_type = question.get("question_type")
    if question_type in TEXT_ANSWER_TYPES:
        typed = str(value or "").strip()
        return typed or None
    if question_type == SELECT_ALL_TYPE:
        labels = [str(choice).split(")", 1)[0] for choice in (value or [])]
        return labels or None
    if not value:
        return None
    return str(value).split(")", 1)[0]


# Re-runs on a timer so the countdown moves and time-up submits without the
# student having to click anything.
@st.fragment(run_every=5)
def take_attempt(user, attempt_id: int) -> None:
    attempt = attempt_with_quiz(attempt_id, user["id"])
    if not attempt:
        st.session_state.pop("attempt_id", None)
        return
    payload = json.loads(attempt["answers_json"]); questions = payload["questions"]; answers = payload["answers"]
    if attempt["submitted_at"]:
        # Already scored (time ran out, or another tab submitted it).
        st.session_state.pop("attempt_id", None)
        st.rerun()
    remaining = datetime.fromisoformat(attempt["deadline_at"]) - current_time()
    if remaining.total_seconds() <= 0:
        submit_attempt(attempt, payload, True)
        st.session_state.pop("attempt_id", None)
        st.session_state.time_up_title = attempt["title"]
        st.rerun()

    def _record(index: int, widget_key: str) -> None:
        """Store one answer the moment it changes.

        Answers used to live in an `st.form`, which meant nothing reached the
        server until the student pressed a button. If the clock ran out first,
        the automatic submission scored an empty attempt — a student could
        answer every question, run out of time, and be marked zero. Saving on
        each change means the stored attempt is always what is on screen.
        """
        stored = _answer_from_widget(questions[index], st.session_state.get(widget_key))
        if stored is None:
            answers.pop(str(index), None)
        else:
            answers[str(index)] = stored
        payload["answers"] = answers
        save_attempt_answers(attempt_id, json.dumps(payload))

    st.divider()
    heading, clock = st.columns([3, 1.4], vertical_alignment="center")
    with heading:
        st.markdown(f"### {attempt['title']}")
        st.progress(min(1.0, len(answers) / len(questions)) if questions else 0,
                    text=f"{len(answers)} of {len(questions)} answered")
    with clock:
        total_seconds = int(remaining.total_seconds())
        low = " low" if total_seconds <= 120 else ""
        st.markdown(
            f'<div class="timer{low}"><span>Time left</span>'
            f'<span class="clock">{total_seconds // 60}:{total_seconds % 60:02d}</span></div>',
            unsafe_allow_html=True,
        )
    for index, question in enumerate(questions):
        widget_key = f"q-{attempt_id}-{index}"
        labels = [f"{label}) {text}" for label, text in question["options"]]
        if question.get("question_type") in TEXT_ANSWER_TYPES:
            st.text_input(
                f"{index + 1}. {question['text']}",
                value=answers.get(str(index), ""),
                max_chars=question.get("limit") or grading.MAX_TEXT_LIMIT,
                help=question.get("hint"),
                placeholder=question.get("hint", "Your answer"),
                key=widget_key, on_change=_record, args=(index, widget_key),
            )
        elif question.get("question_type") == SELECT_ALL_TYPE:
            current = [f"{label}) {text}" for label, text in question["options"] if label in answers.get(str(index), [])]
            st.multiselect(f"{index + 1}. {question['text']}", labels, default=current,
                           key=widget_key, on_change=_record, args=(index, widget_key))
        else:
            current = next((label for label in labels if label.split(")", 1)[0] == answers.get(str(index))), None)
            st.radio(f"{index + 1}. {question['text']}", labels,
                     index=labels.index(current) if current in labels else None,
                     key=widget_key, on_change=_record, args=(index, widget_key))
    st.divider()
    st.caption("Your answers save as you go, so you can come back later or run out of time without losing them. "
               "Submitting is final unless your teacher allowed retakes.")
    if st.button("Submit quiz", type="primary", width="stretch", key=f"submit-{attempt_id}"):
        payload["answers"] = answers
        submit_attempt(attempt, payload, False)
        st.session_state.pop("attempt_id", None)
        st.rerun()


def submit_attempt(attempt, payload: dict, automatic: bool) -> None:
    # `grading.score_payload` is the same marking the teacher-side regrade uses,
    # so a rescored attempt can never disagree with its original submission.
    score = grading.score_payload(payload)
    complete_attempt(attempt["id"], json.dumps(payload), score, int(score >= attempt["passing_score"]), int(automatic))
