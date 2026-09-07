"""The MCQ user guide.

The same content is rendered two ways: as the in-app **Guide** page and as a
downloadable PDF, so the printed handout can never drift from the website.
"""

from __future__ import annotations


import streamlit as st
from fpdf import FPDF

# FPDF's built-in fonts are Latin-1 only, but quiz titles and guide copy get
# pasted in from Word all the time.
_REPLACEMENTS = {
    "‘": "'", "’": "'", "‚": "'", "“": '"', "”": '"',
    "„": '"', "–": "-", "—": "-", "…": "...", "•": "-",
    " ": " ", "−": "-", "′": "'", "″": '"', "±": "+/-",
    "·": "-", "→": "->",
}


def pdf_text(value) -> str:
    text = str(value or "")
    for source, target in _REPLACEMENTS.items():
        text = text.replace(source, target)
    return text.encode("latin-1", "replace").decode("latin-1")


# --------------------------------------------------------------------------- content
# Each section is (heading, [blocks]); each block is (kind, payload) where kind
# is one of: p, ul, ol, note, h3.

TEACHER_GUIDE = [
    ("Getting started", [
        ("p", "MCQ is a place to write multiple-choice and short-answer assessments, assign them to your students, and see how everyone did. This guide covers everything a teacher needs."),
        ("h3", "Signing in"),
        ("p", "The sign-in page has two sides. Use the left panel, Teachers & administrators. Students use the right panel. If you sign in on the wrong side the page will tell you and point you to the other one."),
        ("p", "Signing in with Google from the teacher panel creates your teacher account straight away - there is no approval queue and nothing to wait for. You can also sign in with an email and password if an administrator set one up for you."),
        ("note", "Anyone can create a teacher account. Students, by contrast, must choose at least one teacher before they can do anything, so your students will need to find you by name or email."),
    ]),

    ("Your roster", [
        ("p", "Your roster is the list of students you teach. Only students on your roster can be assigned your assessments, and only their results appear in your analytics."),
        ("h3", "How students join you"),
        ("ol", [
            "A student signs in for the first time and is asked to choose a teacher.",
            "They search for you by name or email and select you.",
            "They appear on your roster immediately, and pick up any assessment you had already assigned to the whole class.",
        ]),
        ("h3", "Adding students yourself"),
        ("p", "Open Students to add someone directly. Add an existing student to roster finds a student who already has an account. Add a new student creates the account from their name and email."),
        ("note", "A student you create this way has no password yet, so they will need to sign in with Google using that same email address."),
        ("h3", "Teams"),
        ("p", "Teams are named groups inside your roster - a class period, a set, a study group. Once a team exists you can assign a whole assessment to it in one click. Manage members from the Students page."),
    ]),

    ("Building an assessment", [
        ("p", "Choose Create quiz. The page has two tabs: Quiz settings and Questions. You can move between them freely; nothing is lost until you publish or leave the page."),
        ("h3", "Quiz settings"),
        ("ul", [
            "Quiz title - what students see on their dashboard.",
            "Time allowed - the countdown each student gets once they press Start. It runs from their own start time, not the clock.",
            "Passing score - the percentage needed to pass.",
            "Allow retakes - lets a student take the assessment more than once. Off by default.",
            "Show class average to students - reveals the class average alongside their own result.",
            "Randomize question order and Randomize answer order - shuffle per student, so neighbours do not see the same paper. True/False options are never shuffled.",
            "Opening and closing date and time - when the assessment is visible. Turn either off to leave that end open.",
        ]),
        ("note", "Times are entered and shown in your own local time zone."),
        ("h3", "Audience"),
        ("p", "Assign to All gives the assessment to everyone currently on your roster, and to anyone who joins you later. Choose Students or Teams to be more specific."),
        ("h3", "Publishing"),
        ("p", "Press Publish quiz. If anything is missing - an untitled quiz, a question with no text, a correct answer that is not one of the options - the page lists exactly what to fix and nothing is saved."),
    ]),

    ("Question types", [
        ("p", "Every question has a type, chosen from the dropdown above it."),
        ("h3", "Multiple choice"),
        ("p", "Up to four options, A to D, with exactly one correct answer. Leave an option blank to offer fewer than four; you need at least two."),
        ("h3", "Multiple choice - select all that apply"),
        ("p", "Same options, but the student picks any number of them. It is marked right only when their selection matches yours exactly - no partial credit."),
        ("h3", "True / False"),
        ("p", "Two fixed options. Pick which one is correct."),
        ("h3", "Fill in the blank and Short answer"),
        ("p", "The student types their answer into a box instead of picking from a list. The two behave identically; use whichever name reads better in your question. See the next section for how they are marked."),
    ]),

    ("How typed answers are marked", [
        ("p", "Typed questions are marked automatically. Each one has an Answer type - Text or Number - which decides how forgiving the marking is."),
        ("h3", "Number answers"),
        ("p", "Use Number whenever the answer is a quantity. The system compares values, not characters, so 6, 6.0, 6.00, +6 and 12/2 are all the same answer."),
        ("p", "How precise the student must be is taken from how precisely you wrote the answer:"),
        ("ul", [
            "Write a whole number, such as 6, and only that exact value is accepted. 6.5 and 7 are wrong.",
            "Write 33.33 and anything within 0.005 is accepted - so 33.33, 33.333 and 100/3 all pass, while 33.3 does not.",
            "Write 2.5 and anything within 0.05 is accepted.",
        ]),
        ("p", "In short: the number of decimal places you type is the precision you are asking for. As you type, the line under the answer box shows the exact range that will be accepted, so you can check before publishing."),
        ("p", "If you want a wider window, set Accept answers within under Marking options. For example, an answer of 100 with a tolerance of 5 accepts anything from 95 to 105."),
        ("p", "Students see a matching hint under their answer box - Enter a whole number, or Round to 2 decimal places - so nobody has to guess."),
        ("h3", "Text answers"),
        ("p", "Capitals, surrounding spaces and trailing punctuation are always ignored, so paris, Paris and \"Paris.\" all match Paris."),
        ("ul", [
            "Also accept - a comma-separated list of other answers you will take, for example USA, US, America.",
            "Forgive single-letter spelling slips - accepts an answer that is one letter away from the right one. It only applies to answers of four letters or more, so short answers are never guessed at.",
        ]),
        ("h3", "Keeping the answer box sensible"),
        ("p", "The answer box is sized automatically from your answer - a two-digit number gets a small box. Set Limit the answer box to under Marking options to cap it yourself, which is a simple way to stop students writing an essay where a number belongs."),
        ("note", "Students never receive the correct answer in the page. Typed answers are marked on the server."),
    ]),

    ("Uploading a question bank", [
        ("p", "Instead of typing questions one at a time you can upload a .txt or .docx file. Open a quiz, choose Manage, then Upload question bank."),
        ("p", "Format the file with numbered questions, lettered options, and an answer key at the end:"),
        ("ul", [
            "1. What does CPU stand for?",
            "A. Central Processing Unit",
            "B. Computer Personal Utility",
            "...",
            "Answer Key: 1: A, 2: B, 3: A",
        ]),
        ("p", "After reading the file you get a table of everything it found. Check it, change any question's Type - including to a typed answer - and press Save question bank and publish. Questions without a matching answer key entry are left out."),
    ]),

    ("Assigning, scheduling and results", [
        ("h3", "Changing who gets an assessment"),
        ("p", "Open Manage on a quiz, then Assign by students or teams. You can switch between All, specific students, and teams at any time."),
        ("h3", "While students are working"),
        ("p", "Analytics shows every student's status on a given exam - Not started, In progress or Completed - along with their score and last activity. The same table appears under Manage."),
        ("note", "Once a real student starts an assessment its settings become read-only, so the paper cannot change underneath them. Your own preview attempts from Student view do not count."),
        ("h3", "Downloads"),
        ("ul", [
            "Download PDF - the paper itself, optionally with the answer key and the quiz settings.",
            "Download results CSV - every assigned student with status, score and result.",
            "Download question bank CSV and Download printable DOCX - for editing or handing out on paper.",
        ]),
    ]),

    ("Previewing and troubleshooting", [
        ("p", "Student view shows the workspace exactly as a student sees it, and lets you take your own assessments to check they read correctly. These attempts are excluded from your analytics."),
        ("h3", "Common questions"),
        ("ul", [
            "A student cannot see my assessment - check they are on your roster, that the quiz is assigned to them, and that the opening time has passed and the closing time has not.",
            "A student ran out of time - their answers are submitted automatically when the timer reaches zero, and whatever they had answered is marked.",
            "I need to fix a published question - open Manage, edit it, and save. If a student has already started, the settings lock; delete and republish instead.",
            "A student joined the wrong teacher - they can leave you from My teachers, and you can remove them from your roster.",
        ]),
    ]),
]

STUDENT_GUIDE = [
    ("Getting started", [
        ("p", "Sign in on the right-hand panel, Students. If you sign in with Google from that side your student account is created straight away."),
        ("h3", "Choosing your teacher"),
        ("p", "The first thing you will be asked to do is choose your teacher. Search for them by name or email and select them. You need at least one teacher before you can see any assessments; you can add more, or leave one, from My teachers at any time."),
    ]),
    ("Taking an assessment", [
        ("p", "Your dashboard lists everything your teachers have assigned and that is currently open. Press Start quiz to begin."),
        ("ul", [
            "The countdown starts when you press Start and keeps running - it does not pause if you close the tab.",
            "Save progress stores your answers so you can come back and Resume.",
            "When the timer reaches zero your answers are submitted automatically and marked as they stand.",
            "Submit quiz finishes the attempt. If your teacher allowed retakes you will see a Retake button afterwards.",
        ]),
        ("h3", "Typed answers"),
        ("p", "Some questions ask you to type an answer instead of choosing one. The hint under the box tells you what is expected - Enter a whole number, or Round to 2 decimal places."),
        ("ul", [
            "For numbers, 6 and 6.0 are the same answer, and you can write a fraction such as 100/3.",
            "Round to the number of decimal places the hint asks for.",
            "For words, capital letters and extra spaces do not matter.",
        ]),
    ]),
    ("Your results", [
        ("p", "Once you submit, your score appears on the dashboard along with whether you passed. If your teacher turned it on you will also see the class average."),
    ]),
]


# --------------------------------------------------------------------------- rendering


def render(sections: list) -> None:
    """Draw the guide into the page."""
    st.markdown('<div class="prose">', unsafe_allow_html=True)
    for heading, blocks in sections:
        st.markdown(f"## {heading}")
        for kind, payload in blocks:
            if kind == "p":
                st.markdown(payload)
            elif kind == "h3":
                st.markdown(f"### {payload}")
            elif kind == "ul":
                st.markdown("\n".join(f"- {item}" for item in payload))
            elif kind == "ol":
                st.markdown("\n".join(f"{number}. {item}" for number, item in enumerate(payload, 1)))
            elif kind == "note":
                st.info(payload)
    st.markdown("</div>", unsafe_allow_html=True)


def to_pdf(sections: list, title: str, subtitle: str) -> bytes:
    """Render the same guide as a printable PDF."""
    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=20)
    pdf.set_margins(20, 20, 20)
    pdf.add_page()

    pdf.set_font("Helvetica", "B", 26)
    pdf.set_text_color(18, 33, 28)
    pdf.multi_cell(0, 11, pdf_text(title), new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 12)
    pdf.set_text_color(107, 124, 116)
    pdf.multi_cell(0, 6, pdf_text(subtitle), new_x="LMARGIN", new_y="NEXT")
    pdf.ln(3)

    pdf.set_draw_color(18, 105, 79)
    pdf.set_line_width(0.8)
    pdf.line(20, pdf.get_y(), 190, pdf.get_y())
    pdf.ln(5)

    pdf.set_font("Helvetica", "B", 12)
    pdf.set_text_color(18, 33, 28)
    pdf.multi_cell(0, 6, "Contents", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 11)
    pdf.set_text_color(65, 84, 76)
    for number, (heading, _) in enumerate(sections, 1):
        pdf.multi_cell(0, 5.6, pdf_text(f"{number}.  {heading}"), new_x="LMARGIN", new_y="NEXT")

    for number, (heading, blocks) in enumerate(sections, 1):
        pdf.add_page()
        pdf.set_font("Helvetica", "B", 17)
        pdf.set_text_color(18, 105, 79)
        pdf.multi_cell(0, 8, pdf_text(f"{number}. {heading}"), new_x="LMARGIN", new_y="NEXT")
        pdf.ln(1.5)
        for kind, payload in blocks:
            if pdf.get_y() > 255:
                pdf.add_page()
            if kind == "h3":
                pdf.ln(1.5)
                pdf.set_font("Helvetica", "B", 12)
                pdf.set_text_color(18, 33, 28)
                pdf.multi_cell(0, 6, pdf_text(payload), new_x="LMARGIN", new_y="NEXT")
                pdf.ln(0.5)
            elif kind == "p":
                pdf.set_font("Helvetica", "", 11)
                pdf.set_text_color(45, 60, 54)
                pdf.multi_cell(0, 5.6, pdf_text(payload), new_x="LMARGIN", new_y="NEXT")
                pdf.ln(2)
            elif kind in ("ul", "ol"):
                pdf.set_font("Helvetica", "", 11)
                pdf.set_text_color(45, 60, 54)
                for index, item in enumerate(payload, 1):
                    marker = f"{index}." if kind == "ol" else "-"
                    pdf.set_x(26)
                    pdf.multi_cell(0, 5.6, pdf_text(f"{marker}  {item}"), new_x="LMARGIN", new_y="NEXT")
                pdf.ln(2)
            elif kind == "note":
                pdf.set_font("Helvetica", "I", 10.5)
                pdf.set_text_color(18, 105, 79)
                pdf.set_x(24)
                pdf.multi_cell(0, 5.4, pdf_text(f"Note:  {payload}"), new_x="LMARGIN", new_y="NEXT")
                pdf.set_text_color(45, 60, 54)
                pdf.ln(2)
    return bytes(pdf.output())


def page(user) -> None:
    """The in-app Guide page, tailored to who is reading it."""
    from ui import page_header

    is_student = user["role"] == "student"
    if is_student:
        page_header("Help", "Using MCQ", "How to choose a teacher, take an assessment, and read your results.")
        sections, title, filename = STUDENT_GUIDE, "MCQ - Student guide", "mcq-student-guide.pdf"
        subtitle = "How to take assessments on MCQ"
    else:
        page_header("Help", "Teacher guide", "Everything you need to build assessments, manage your roster, and read the results.")
        sections, title, filename = TEACHER_GUIDE, "MCQ - Teacher guide", "mcq-teacher-guide.pdf"
        subtitle = "Building and running assessments on MCQ"

    download, spacer = st.columns([2, 5])
    with download:
        st.download_button(
            "Download this guide (PDF)", to_pdf(sections, title, subtitle),
            filename, "application/pdf", type="primary", width="stretch", key="guide-pdf",
        )
    if not is_student:
        with spacer:
            st.download_button(
                "Download the student guide (PDF)",
                to_pdf(STUDENT_GUIDE, "MCQ - Student guide", "How to take assessments on MCQ"),
                "mcq-student-guide.pdf", "application/pdf", width="stretch", key="guide-pdf-student",
            )
    st.divider()
    render(sections)
