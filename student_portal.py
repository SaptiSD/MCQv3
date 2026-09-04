"""Student dashboard and assessment-taking experience."""

from __future__ import annotations

import json
import random
from datetime import datetime, timedelta, timezone

import streamlit as st

from db import connect, utc_now
from repository import available_quizzes, attempts_for_student, questions_for_quiz


def current_time() -> datetime:
    return datetime.now(timezone.utc)


def dashboard(user) -> None:
    st.markdown('<div class="eyebrow">Student workspace</div><h1>Ready when you are.</h1>', unsafe_allow_html=True)
    st.caption("Your assigned assessments and latest results, all in one place.")
    quizzes = available_quizzes(user["id"])
    attempts = attempts_for_student(user["id"])
    latest = {}
    for attempt in attempts: latest.setdefault(attempt["quiz_id"], attempt)
    if not quizzes: st.info("No assessments are assigned and open right now.")
    for quiz in quizzes:
        attempt = latest.get(quiz["id"])
        with st.container(border=True):
            details, action = st.columns([4, 1])
            with details:
                st.subheader(quiz["title"])
                st.caption(f"{quiz['duration_minutes']} minutes  ·  closes {datetime.fromisoformat(quiz['closing_time']).astimezone().strftime('%b %d, %I:%M %p')}")
                if attempt and attempt["submitted_at"]:
                    average = "" if not quiz["show_average"] else "  ·  class average available"
                    st.write(f"Latest result: **{attempt['score_percent']:.0f}%**  ·  {'Passed' if attempt['passed'] else 'Needs another try'}{average}")
            with action:
                done = attempt and attempt["submitted_at"]
                if done and not quiz["allow_retake"]:
                    st.write("Completed · no retakes")
                else:
                    label = "Resume" if attempt and not attempt["submitted_at"] else ("Retake" if attempt and quiz["allow_retake"] else "Start quiz")
                    if st.button(label, key=f"start-{quiz['id']}", type="primary", width="stretch"):
                        start_attempt(user, quiz); st.rerun()
    if st.session_state.get("attempt_id"):
        take_attempt(user, st.session_state.attempt_id)


def start_attempt(user, quiz) -> None:
    with connect() as db:
        open_attempt = db.execute("SELECT * FROM attempts WHERE quiz_id=? AND student_id=? AND submitted_at IS NULL", (quiz["id"], user["id"])).fetchone()
        if open_attempt:
            st.session_state.attempt_id = open_attempt["id"]; return
        questions = list(questions_for_quiz(quiz["id"])); random.shuffle(questions)
        frozen = []
        for question in questions:
            options = json.loads(question["options_json"]); random.shuffle(options)
            correct = json.loads(question["correct_label"]) if question["question_type"] == "Multiple choice - select all that apply" else question["correct_label"]
            frozen.append({"text": question["question_text"], "options": options, "correct": correct, "question_type": question["question_type"]})
        started = current_time(); deadline = min(started + timedelta(minutes=quiz["duration_minutes"]), datetime.fromisoformat(quiz["closing_time"]))
        cursor = db.execute("INSERT INTO attempts(quiz_id,student_id,started_at,deadline_at,answers_json) VALUES (?,?,?,?,?)", (quiz["id"], user["id"], started.isoformat(), deadline.isoformat(), json.dumps({"questions": frozen, "answers": {}})))
        st.session_state.attempt_id = cursor.lastrowid


def take_attempt(user, attempt_id: int) -> None:
    with connect() as db:
        attempt = db.execute("SELECT a.*, q.title, q.passing_score FROM attempts a JOIN quizzes q ON q.id=a.quiz_id WHERE a.id=? AND a.student_id=?", (attempt_id, user["id"])).fetchone()
    if not attempt: return
    payload = json.loads(attempt["answers_json"]); questions = payload["questions"]; answers = payload["answers"]
    remaining = datetime.fromisoformat(attempt["deadline_at"]) - current_time()
    st.divider(); st.markdown(f"### {attempt['title']}")
    st.progress(min(1.0, len(answers) / len(questions)) if questions else 0, text=f"{len(answers)} of {len(questions)} answered")
    if remaining.total_seconds() <= 0:
        submit_attempt(attempt, payload, True); st.rerun()
    st.warning(f"Time remaining: **{max(0, int(remaining.total_seconds()) // 60)} min {max(0, int(remaining.total_seconds()) % 60):02d} sec**")
    with st.form(f"attempt-{attempt_id}"):
        for index, question in enumerate(questions):
            labels = [f"{label}) {text}" for label, text in question["options"]]
            if question.get("question_type") == "Multiple choice - select all that apply":
                current = [f"{label}) {text}" for label, text in question["options"] if label in answers.get(str(index), [])]
                choices = st.multiselect(f"{index + 1}. {question['text']}", labels, default=current, key=f"q-{attempt_id}-{index}")
                if choices:
                    answers[str(index)] = [choice.split(")", 1)[0] for choice in choices]
                else:
                    answers.pop(str(index), None)
            else:
                current = next((label for label in labels if label.startswith(f"{answers.get(str(index), '')})")), None)
                choice = st.radio(f"{index + 1}. {question['text']}", labels, index=labels.index(current) if current in labels else None, key=f"q-{attempt_id}-{index}")
                if choice: answers[str(index)] = choice.split(")", 1)[0]
        save, submit = st.columns(2)
        save_clicked = save.form_submit_button("Save progress", width="stretch")
        submit_clicked = submit.form_submit_button("Submit quiz", type="primary", width="stretch")
    if save_clicked:
        payload["answers"] = answers; update_answers(attempt_id, payload); st.success("Progress saved."); st.rerun()
    if submit_clicked:
        payload["answers"] = answers; submit_attempt(attempt, payload, False); st.session_state.pop("attempt_id", None); st.rerun()


def update_answers(attempt_id: int, payload: dict) -> None:
    with connect() as db: db.execute("UPDATE attempts SET answers_json=? WHERE id=?", (json.dumps(payload), attempt_id))


def submit_attempt(attempt, payload: dict, automatic: bool) -> None:
    correct = sum(set(payload["answers"].get(str(index), [])) == set(question["correct"]) if question.get("question_type") == "Multiple choice - select all that apply" else payload["answers"].get(str(index)) == question["correct"] for index, question in enumerate(payload["questions"]))
    score = (correct / len(payload["questions"]) * 100) if payload["questions"] else 0
    with connect() as db: db.execute("UPDATE attempts SET answers_json=?,submitted_at=?,score_percent=?,passed=?,auto_submitted=? WHERE id=?", (json.dumps(payload), utc_now(), score, int(score >= attempt["passing_score"]), int(automatic), attempt["id"]))
