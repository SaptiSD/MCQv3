"""Student dashboard and assessment-taking experience."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import streamlit as st

import attempt_sync
import grading
from repository import (all_teachers, attempt_with_quiz, attempts_for_student, available_quizzes,
                        complete_attempt, create_attempt, join_teacher, leave_teacher,
                        open_attempt, questions_for_quiz, quiz_average_score, quiz_paper_fingerprint,
                        refresh_attempt_key, resync_attempt, save_attempt_answers, search_teachers,
                        submitted_attempt, teachers_for_student)
from ui import empty_state, metric_row, page_header, percent, pill, require_session, sign_out, text


SELECT_ALL_TYPE = attempt_sync.SELECT_ALL_TYPE
TEXT_ANSWER_TYPES = attempt_sync.TEXT_ANSWER_TYPES


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
    blocked = st.session_state.pop("retake_blocked", None)
    if blocked:
        st.warning(f"**{blocked}** has already been submitted and your teacher didn't allow retakes.")
    closed = st.session_state.pop("window_closed", None)
    if closed:
        st.warning(f"**{closed}** closed before you could start it, so no attempt was recorded. "
                   "Ask your teacher if you need it reopened.")
    early = st.session_state.pop("window_not_open", None)
    if early:
        st.info(f"**{early}** hasn't opened yet. It will be ready at the time your teacher set.")
    if st.session_state.pop("attempt_withdrawn", False):
        st.warning("The assessment you were taking is no longer yours to take - your teacher either removed it "
                   "or unassigned you - so that attempt has ended and nothing was submitted.")

    _assessment_list(user, previewing)
    if st.session_state.get("attempt_id"):
        take_attempt(user, st.session_state.attempt_id)


# Streamlit only re-runs a page when somebody interacts with it, so an
# assessment assigned while a student sat looking at their dashboard did not
# appear until they refreshed -- and nothing on screen gave them any reason to.
# Polling is what makes new work arrive on its own. Twenty seconds is quick
# enough that a teacher assigning in front of the class sees it land, and slow
# enough to be a few queries a minute for a student who is just sitting there.
@st.fragment(run_every=20)
def _assessment_list(user, previewing: bool) -> None:
    # A fragment never re-executes the main script body, so the signed-out check
    # belongs here as well -- without it a tab signed out somewhere else carries
    # on polling, and carries on offering to start assessments.
    if not require_session(user):
        return
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

    # Say so, rather than leaving a new card to be noticed. Keyed per view so
    # the teacher's Student view preview and their own dashboard don't announce
    # each other's assessments.
    seen_key = f"seen-quizzes-{'preview' if previewing else 'mine'}"
    on_offer = {quiz["id"] for quiz in quizzes}
    seen = st.session_state.get(seen_key)
    if seen is None:
        st.session_state[seen_key] = on_offer
    elif (arrived := on_offer - seen):
        st.session_state[seen_key] = on_offer
        titles = [quiz["title"] for quiz in quizzes if quiz["id"] in arrived]
        heading = titles[0] if len(titles) == 1 else f"{len(titles)} new assessments"
        st.toast(f"New: {heading}", icon=":material/assignment:")

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
                st.markdown(f"### {text(quiz['title'])} &nbsp;{pill(label, tone)}", unsafe_allow_html=True)
                closes = (
                    f"closes {datetime.fromisoformat(quiz['closing_time']).astimezone().strftime('%b %d, %I:%M %p')}"
                    if quiz["closing_enabled"] else "no closing date"
                )
                minutes = quiz["duration_minutes"]
                bits = [f"{minutes} minute{'s' if minutes != 1 else ''}", f"pass at {quiz['passing_score']}%", closes]
                if quiz["allow_retake"]:
                    bits.append("retakes allowed")
                st.caption("  ·  ".join(bits))
                if submitted:
                    average = ""
                    if quiz["show_average"]:
                        class_average = quiz_average_score(quiz["id"])
                        if class_average is not None:
                            average = f"  ·  class average {class_average:.0f}%"
                    st.markdown(f"Your score: **{percent(attempt['score_percent'])}**{average}")
            with action:
                if submitted and not quiz["allow_retake"]:
                    st.caption("Completed — no retakes")
                else:
                    label = "Resume" if attempt and not submitted else ("Retake" if submitted else "Start quiz")
                    if st.button(label, key=f"start-{quiz['id']}", type="primary", width="stretch"):
                        start_attempt(user, quiz)
                        # App scope on purpose: the attempt itself is drawn by the
                        # main script body, outside this fragment.
                        st.rerun()


@st.fragment
def my_teachers_page(user) -> None:
    # Joining and leaving are writes, and a fragment rerun never re-executes the
    # main script body -- so `main()`'s session check does not run for the clicks
    # in here. Without this a tab signed out in another window kept the run of
    # its own enrolment, and joining a teacher hands over that teacher's
    # class-wide assessments.
    if not require_session(user):
        return
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


OPEN, NOT_YET, CLOSED = "open", "not_yet", "closed"


def window_state(quiz, now: datetime) -> str:
    """Whether `quiz` may be started at `now`.

    Pulled out of `start_attempt` so it can be tested without a browser: the
    bug it exists to stop is off by seconds, which is exactly the kind of thing
    that never gets exercised by hand.
    """
    if quiz["closing_enabled"] and datetime.fromisoformat(quiz["closing_time"]) <= now:
        return CLOSED
    if quiz["opening_enabled"] and datetime.fromisoformat(quiz["opening_time"]) > now:
        return NOT_YET
    return OPEN


def _paper_id(payload: dict) -> str:
    """Which version of the questions a payload holds.

    This goes in the answer widgets' keys, so a paper that changes shape gets a
    fresh set of widgets rather than the previous paper's selections redrawn
    against different questions. Deleting the old keys instead looks like the
    same thing and is not: Streamlit fires a deleted widget's `on_change`, and
    the callback then wrote its own copy of the old paper back over the new one.
    """
    return attempt_sync.payload_fingerprint(payload, include_key=False)[:10]


def _announce(attempt_id: int, message: str) -> None:
    """Leave a note for the student at the top of their paper."""
    st.session_state[f"attempt-news-{attempt_id}"] = message


def start_attempt(user, quiz) -> None:
    existing = open_attempt(quiz["id"], user["id"])
    if existing:
        # Picking a saved attempt back up is the moment to catch it up with the
        # quiz. The student is between questions rather than mid-thought, so a
        # question their teacher has added since they saved can be slotted in
        # without pulling the paper around under them -- and resuming into a
        # version of the assessment nobody else is sitting is worse.
        summary = resync_attempt(existing["id"], user["id"])
        if summary:
            _announce(existing["id"], attempt_sync.describe(summary))
        st.session_state.attempt_id = existing["id"]
        return
    if not quiz["allow_retake"] and submitted_attempt(quiz["id"], user["id"]):
        # "Completed — no retakes" is only a rendering decision on the dashboard,
        # so a second tab still showing "Start quiz" (or a stale card in this
        # one) could open a fresh attempt on a quiz with retakes switched off.
        st.session_state.retake_blocked = quiz["title"]
        return
    started = current_time()
    # The card this was pressed from was drawn from the quiz list as it stood
    # when the page rendered, and a card is a rendering decision rather than a
    # permission -- the same reasoning as the retake check above. A student who
    # reads the card for a few seconds and then presses Start can arrive after
    # the assessment has closed, and the deadline below is a `min` against the
    # closing time: it lands in the past, the countdown fires on the very first
    # refresh, and they are recorded as having scored nothing on a paper they
    # never saw. With retakes switched off that nothing is final.
    state = window_state(quiz, started)
    if state == CLOSED:
        st.session_state.window_closed = quiz["title"]
        return
    if state == NOT_YET:
        st.session_state.window_not_open = quiz["title"]
        return
    frozen = attempt_sync.freeze_all(questions_for_quiz(quiz["id"]),
                                     quiz["randomize_questions"], quiz["randomize_answers"])
    deadline = started + timedelta(minutes=quiz["duration_minutes"])
    if quiz["closing_enabled"]:
        deadline = min(deadline, datetime.fromisoformat(quiz["closing_time"]))
    st.session_state.attempt_id = create_attempt(
        quiz["id"], user["id"], started.isoformat(), deadline.isoformat(),
        json.dumps({"questions": frozen, "answers": {}, "revision": 0})
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
    # This fragment re-runs on a timer as well as on every answer, and neither
    # re-executes the main script body, so it has to check for itself that the
    # account is still signed in here.
    if not require_session(user):
        return
    attempt = attempt_with_quiz(attempt_id, user["id"])
    if not attempt:
        # The attempt row has gone, which means the assessment was deleted out
        # from under it -- deleting a quiz takes its attempts with it. Returning
        # quietly left the questions simply disappearing off the screen.
        st.session_state.pop("attempt_id", None)
        st.session_state.attempt_withdrawn = True
        st.rerun()
    try:
        payload = json.loads(attempt["answers_json"] or "{}")
        questions = payload["questions"]
        answers = payload.setdefault("answers", {})
    except (ValueError, TypeError, KeyError):
        # An attempt row whose frozen question list never made it to the
        # database. Left unhandled this raised on every five-second refresh and
        # the student had no way past it.
        st.session_state.pop("attempt_id", None)
        st.error("This attempt could not be opened. Ask your teacher to reset it for you.")
        return
    if attempt["submitted_at"]:
        # Already scored (time ran out, or another tab submitted it).
        st.session_state.pop("attempt_id", None)
        st.rerun()

    # Every save stamps the attempt with a number that only goes up, and each tab
    # remembers the one it last saw. A tab that has been sitting open while the
    # student worked in another one is therefore recognisable — and, crucially,
    # is repainted from what is stored rather than being left showing answers the
    # server does not have and writing them back on the next click.
    seen_key = f"attempt-seen-{attempt_id}"
    paint_key = f"attempt-paint-{attempt_id}"
    paint = int(st.session_state.get(paint_key, 0))
    if attempt_sync.overtaken(payload, st.session_state.get(seen_key)):
        # Draw the answers again under fresh widget keys. The stored answers have
        # moved on and a widget's key outranks the `value=`/`index=` it is
        # re-rendered with, so the only way to show what is really saved is to
        # ask for different widgets.
        st.session_state[seen_key] = attempt_sync.revision(payload)
        st.session_state[paint_key] = paint + 1
        _announce(attempt_id, "You had this assessment open in more than one place. "
                              "The answers below are the ones that were saved most recently.")
        st.rerun(scope="fragment")
    st.session_state[seen_key] = attempt_sync.revision(payload)
    paper = _paper_id(payload)

    # Has the teacher changed what this paper *looks like* since it was frozen?
    # A corrected answer key deliberately does not count: it is invisible here
    # and is applied when the paper is marked, so it is no reason to interrupt.
    # `None` means the question bank came back empty — a half-finished save, not
    # an empty quiz — and nothing should be concluded from it.
    live_fingerprint = quiz_paper_fingerprint(attempt["quiz_id"])
    out_of_date = bool(live_fingerprint) and live_fingerprint != attempt_sync.payload_fingerprint(payload, include_key=False)

    remaining = datetime.fromisoformat(attempt["deadline_at"]) - current_time()
    if remaining.total_seconds() <= 0:
        if out_of_date:
            # Time is up. Catch the paper up and hand it in, rather than making
            # the student choose between the two with no clock left to do it in.
            caught_up = resync_attempt(attempt_id, user["id"])
            if caught_up:
                payload = caught_up["payload"]
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

        What goes back is the *stored* attempt with this one answer changed, not
        the copy this run was drawn from. Writing the drawn copy back meant a tab
        that had been open a while restored its own questions and answers over
        whatever had happened since — another tab's work, or a teacher's edit.
        """
        given = _answer_from_widget(questions[index], st.session_state.get(widget_key))
        latest = attempt_with_quiz(attempt_id, user["id"])
        if not latest or latest["submitted_at"]:
            return
        try:
            stored = json.loads(latest["answers_json"] or "{}")
        except (TypeError, ValueError):
            return
        if _paper_id(stored) != paper:
            # This box was drawn against a version of the paper that has since
            # been replaced. Its value cannot be placed on the new one, and its
            # index would land on a different question.
            return
        saved_answers = stored.get("answers") or {}
        if given is None:
            saved_answers.pop(str(index), None)
        else:
            saved_answers[str(index)] = given
        stored["answers"] = saved_answers
        stored["revision"] = attempt_sync.revision(stored) + 1
        save_attempt_answers(attempt_id, json.dumps(stored))
        st.session_state[seen_key] = stored["revision"]
        st.session_state.pop(f"attempt-news-{attempt_id}", None)
        st.session_state.pop(f"confirm-submit-{attempt_id}", None)

    st.divider()
    heading, clock = st.columns([3, 1.4], vertical_alignment="center")
    with heading:
        st.markdown(f"### {text(attempt['title'])}", unsafe_allow_html=True)
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
    if news := st.session_state.get(f"attempt-news-{attempt_id}"):
        st.info(news)
    if out_of_date:
        # A backstop, not a routine path: a quiz's questions are fixed once anyone
        # starts it, so the only ways to get here are an attempt that was already
        # open before that rule existed, or the database being changed underneath
        # the app. Either way, handing in a paper that no longer exists is not an
        # option, so Submit waits until the student has the current one.
        st.warning(
            "Your teacher changed this assessment while you had it open, so what you see below is "
            "no longer the current version. Load the update to carry on — the answers you have "
            "already given are kept."
        )
        if st.button("Load the updated version", key=f"resync-{attempt_id}", type="primary", width="stretch"):
            summary = resync_attempt(attempt_id, user["id"])
            _announce(attempt_id, attempt_sync.describe(summary) if summary
                      else "This assessment is already up to date.")
            st.rerun(scope="fragment")
    for index, question in enumerate(questions):
        widget_key = f"q-{attempt_id}-{paper}-{paint}-{index}"
        labels = [f"{label}) {option_text}" for label, option_text in question["options"]]
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
            current = [f"{label}) {option_text}" for label, option_text in question["options"]
                       if label in (answers.get(str(index)) or [])]
            st.multiselect(f"{index + 1}. {question['text']}", labels, default=current,
                           key=widget_key, on_change=_record, args=(index, widget_key))
        else:
            current = next((label for label in labels if label.split(")", 1)[0] == answers.get(str(index))), None)
            st.radio(f"{index + 1}. {question['text']}", labels,
                     index=labels.index(current) if current in labels else None,
                     key=widget_key, on_change=_record, args=(index, widget_key))
    st.divider()
    if out_of_date:
        st.caption("Submitting is paused until you load your teacher's update, so that you are "
                   "marked on the same assessment as everyone else.")
    else:
        st.caption("Your answers save as you go, so you can come back later or run out of time without losing them. "
                   "Submitting is final unless your teacher allowed retakes.")
    def _hand_in() -> None:
        # Every route to a submission goes through here, so the stale-paper
        # guard does too rather than living on one button. The unanswered
        # confirmation is raised before the paper can go stale and stays on
        # screen afterwards, so its "Submit anyway" was a second door into
        # handing in a paper that no longer exists.
        if out_of_date:
            return
        payload["answers"] = answers
        submit_attempt(attempt, payload, False)
        st.session_state.pop(f"confirm-submit-{attempt_id}", None)
        st.session_state.pop("attempt_id", None)
        st.rerun()

    # Submitting is final, and a question the student meant to come back to
    # looks exactly like one they decided to skip. Ask once, name the questions,
    # and let them go straight back to them. The clock running out still submits
    # without asking -- there is nobody left to answer.
    blanks = [index + 1 for index in range(len(questions))
              if answers.get(str(index)) in (None, "", [])]
    confirm_key = f"confirm-submit-{attempt_id}"
    if blanks and st.session_state.get(confirm_key) and not out_of_date:
        listed = ", ".join(str(number) for number in blanks[:12])
        if len(blanks) > 12:
            listed += f" and {len(blanks) - 12} more"
        st.warning(
            f"**{len(blanks)} question{'s are' if len(blanks) != 1 else ' is'} still unanswered** "
            f"({listed}). Unanswered questions are marked wrong."
        )
        back, anyway = st.columns(2)
        if back.button("Go back to them", key=f"resume-blanks-{attempt_id}", type="primary", width="stretch"):
            st.session_state.pop(confirm_key, None)
            st.rerun(scope="fragment")
        if anyway.button("Submit anyway", key=f"submit-anyway-{attempt_id}", width="stretch"):
            _hand_in()
    elif st.button("Submit quiz", type="primary", width="stretch", key=f"submit-{attempt_id}",
                   disabled=out_of_date):
        if blanks:
            st.session_state[confirm_key] = True
            st.rerun(scope="fragment")
        _hand_in()


def submit_attempt(attempt, payload: dict, automatic: bool) -> None:
    # Mark against the answer key as it stands now, not the copy that was frozen
    # when the attempt started. A student who began before their teacher fixed a
    # wrong answer used to be marked against the mistake, and the teacher's own
    # Regrade button — which does exactly this — then disagreed with the score
    # the student had already been shown.
    refresh_attempt_key(payload, attempt["quiz_id"])
    # `grading.score_payload` is the same marking the teacher-side regrade uses,
    # so a rescored attempt can never disagree with its original submission.
    score = grading.score_payload(payload)
    complete_attempt(attempt["id"], json.dumps(payload), score, int(score >= attempt["passing_score"]), int(automatic))
