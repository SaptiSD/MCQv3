"""Write the response to the tester's bug report as a short PDF.

One page of principle, one of decisions, one of everything else. Run from
anywhere:

    python instructions/build_fix_summary.py

The content lives here rather than in `guide.py` because it is a record of what
changed and why, not instructions for using the app.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from fpdf import FPDF  # noqa: E402

from guide import pdf_text  # noqa: E402

HERE = Path(__file__).resolve().parent
TITLE = "MCQ - response to the bug report"
SUBTITLE = "Pages 12-18, plus a second testing pass. What changed, and why."

INK = (18, 33, 28)
BODY = (45, 60, 54)
MUTED = (107, 124, 116)
GREEN = (18, 105, 79)
RULE = (188, 217, 203)

# Each entry: the reports it answers, the one-line finding, the decision, the
# reasoning. "kind" drives the colour of the decision line.
DECISIONS = [
    (
        "011, 013, 015",
        "A question added or removed while a student was part-way through never "
        "reached them, and a resumed attempt showed the old paper.",
        "spec",
        "The paper is now fixed as soon as the first student starts it. Questions, "
        "options and how many there are cannot change after that.",
        "This is the rule the quiz's settings have always followed, so the question "
        "bank was simply missing it. Students have to sit the same paper for their "
        "results to mean anything side by side - which is the harm the report "
        "describes. To change the questions after that, delete the assessment and "
        "publish a new one.",
    ),
    (
        "012, 016",
        "An attempt was marked against the answer key that applied when the student "
        "started, so a correction never reached them.",
        "spec",
        "The answer key is deliberately NOT part of the fixed paper. It stays "
        "correctable, and marking always uses the key as it stands when the paper "
        "is handed in.",
        "A mis-keyed question is a mistake, not a change of assessment, and fixing "
        "it alters nothing the student sees. Freezing it would have made a wrong "
        "answer permanent. Results already recorded are brought up to date with "
        "Regrade submitted attempts.",
    ),
    (
        "014",
        "The same quiz open in two tabs: the older tab could submit its stale "
        "answers over newer progress already saved.",
        "bug",
        "Every save stamps the attempt with a version. A tab that has been overtaken "
        "says so and redraws itself from what is actually stored.",
        "A real defect rather than a spec gap - last-write-wins with no way to "
        "detect it simply loses a student's work.",
    ),
    (
        "017",
        'A quiz titled "Understanding <div> and <p> Tags" displayed as '
        '"Understanding and Tags".',
        "bug",
        "Anything a person typed is escaped before it reaches the page.",
        "The browser was reading the title as markup. The same hole would have run "
        "a script tag in a quiz title, so this was a security fix as much as a "
        "display one.",
    ),
    (
        "018",
        "Repeated assignment created duplicate quiz entries for teacher and student.",
        "done",
        "Already fixed before this pass: the duplicates came from a double-clicked "
        "Publish, which is now claimed once per draft. Assigning is idempotent too.",
        "Six rapid clicks on Publish now produce exactly one quiz. Saving the same "
        "audience twice is a no-op rather than a delete-then-insert that could race.",
    ),
]

ALSO_FIXED = [
    ("Silent data loss", [
        "Adding someone to a team and pressing Save members deleted them again, "
        "reporting \"Team updated.\"",
        "An uploaded question bank could mark the wrong option correct, because the "
        "review table re-lettered options by position.",
        "Narrowing a quiz's audience hid results already recorded from every screen "
        "and export, while the dashboard kept counting them.",
        "A question written \"1.What is 2+2?\" was dropped from an upload with no "
        "skip note.",
    ]),
    ("Security and access", [
        "A removed administrator kept working in any open tab - including the form "
        "that let them add themselves back.",
        "The My teachers page could still be used to join and leave teachers after "
        "the account had been signed out elsewhere.",
    ]),
    ("Correctness", [
        "Two options that marking could not tell apart (\"Paris\" and \"Paris.\") "
        "passed validation, and which one counted depended on the shuffle.",
        "A question bank saved in Windows ANSI was read as unintelligible text, "
        "depending on whether the file's length was even.",
        "An email pasted with a trailing space created an account nobody could sign "
        "in to.",
    ]),
    ("Clarity", [
        "Deleting a quiz while a student was taking it removed their paper with no "
        "message, and the confirmation never said anyone was mid-attempt.",
        "Counts were not pluralised (\"1 minutes\", \"1 members\").",
    ]),
]

TEACHER_NOTES = [
    "Build and check the paper before you assign it. Student view lets you sit "
    "your own quiz and read it as a student does.",
    "Once the first student starts, the questions lock. The banner in Manage says "
    "so, and the upload option disappears.",
    "A wrong answer can still be corrected at any time. Save it, then use Regrade "
    "submitted attempts to apply it to results already in.",
    "Your own preview attempts never lock a quiz.",
    "If the paper itself is wrong after students have started, delete the "
    "assessment and publish a new one.",
]

BADGES = {
    "spec": ("Decision", GREEN),
    "bug": ("Fixed", (164, 50, 42)),
    "done": ("Already fixed", MUTED),
}


class Summary(FPDF):
    def header(self) -> None:
        if self.page_no() == 1:
            return
        self.set_font("Helvetica", "", 8)
        self.set_text_color(*MUTED)
        self.cell(0, 6, pdf_text(TITLE), align="R", new_x="LMARGIN", new_y="NEXT")
        self.ln(2)

    def footer(self) -> None:
        self.set_y(-15)
        self.set_font("Helvetica", "", 8)
        self.set_text_color(*MUTED)
        self.cell(0, 6, f"{self.page_no()}", align="C")


def rule(pdf: FPDF, colour=RULE, width=0.4) -> None:
    pdf.set_draw_color(*colour)
    pdf.set_line_width(width)
    pdf.line(20, pdf.get_y(), 190, pdf.get_y())


def heading(pdf: FPDF, text: str, size: int = 15) -> None:
    if pdf.get_y() > 240:
        pdf.add_page()
    pdf.ln(3)
    pdf.set_font("Helvetica", "B", size)
    pdf.set_text_color(*GREEN)
    pdf.multi_cell(0, 7, pdf_text(text), new_x="LMARGIN", new_y="NEXT")
    pdf.ln(1)


def paragraph(pdf: FPDF, text: str, size: int = 10.5, colour=BODY, style: str = "") -> None:
    pdf.set_font("Helvetica", style, size)
    pdf.set_text_color(*colour)
    pdf.multi_cell(0, 5.2, pdf_text(text), new_x="LMARGIN", new_y="NEXT")


def bullets(pdf: FPDF, items: list[str]) -> None:
    for item in items:
        if pdf.get_y() > 258:
            pdf.add_page()
        pdf.set_font("Helvetica", "B", 10.5)
        pdf.set_text_color(*GREEN)
        pdf.cell(5, 5.2, pdf_text("-"))
        pdf.set_font("Helvetica", "", 10.5)
        pdf.set_text_color(*BODY)
        pdf.multi_cell(0, 5.2, pdf_text(item), new_x="LMARGIN", new_y="NEXT")
        pdf.ln(0.8)


def badge(pdf: FPDF, kind: str) -> None:
    label, colour = BADGES[kind]
    pdf.set_font("Helvetica", "B", 8)
    pdf.set_text_color(*colour)
    pdf.cell(0, 4.6, pdf_text(label.upper()), new_x="LMARGIN", new_y="NEXT")


def build() -> bytes:
    pdf = Summary()
    pdf.set_auto_page_break(auto=True, margin=18)
    pdf.set_margins(20, 18, 20)
    pdf.add_page()

    pdf.set_font("Helvetica", "B", 24)
    pdf.set_text_color(*INK)
    pdf.multi_cell(0, 10, pdf_text(TITLE), new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 11)
    pdf.set_text_color(*MUTED)
    pdf.multi_cell(0, 5.6, pdf_text(SUBTITLE), new_x="LMARGIN", new_y="NEXT")
    pdf.ln(3)
    rule(pdf, GREEN, 0.8)
    pdf.ln(5)

    heading(pdf, "The short version", 16)
    paragraph(pdf,
              "Most of the reports were not defects. They described situations the "
              "product had never taken a position on, so the honest fix was to take "
              "one rather than patch the example given.")
    pdf.ln(2)
    paragraph(pdf, "The position taken, in one line:", style="B", colour=INK)
    pdf.ln(1)
    paragraph(pdf,
              "A quiz's paper is fixed the moment the first student starts it. Its "
              "answer key is not.",
              size=12.5, colour=GREEN, style="B")
    pdf.ln(2)
    paragraph(pdf,
              "That single rule answers five of the eight reports. The paper - the "
              "wording, the options, how many questions there are - is what students "
              "are compared on, so it must not move under them. The answer key is "
              "marking rather than assessment: correcting it changes nothing a "
              "student saw, and a wrong answer has to stay fixable.")
    pdf.ln(2)
    paragraph(pdf,
              "This was not a new idea in the product. A quiz's settings - timing, "
              "passing score, retakes - have always locked as soon as a student "
              "starts. The question bank was simply never given the same rule. "
              "Applying it removes the whole class of \"a teacher edited mid-attempt\" "
              "problems at the source, instead of trying to reconcile two versions "
              "of a paper after the fact.")
    pdf.ln(3)
    rule(pdf)

    heading(pdf, "How each report was handled", 16)
    for reports, finding, kind, decision, why in DECISIONS:
        if pdf.get_y() > 215:
            pdf.add_page()
        badge(pdf, kind)
        pdf.set_font("Helvetica", "B", 11.5)
        pdf.set_text_color(*INK)
        pdf.multi_cell(0, 5.6, pdf_text(f"MCQ-BUG-{reports}"), new_x="LMARGIN", new_y="NEXT")
        pdf.ln(0.5)
        paragraph(pdf, finding, size=10, colour=MUTED)
        pdf.ln(1)
        paragraph(pdf, decision, style="B", colour=INK)
        pdf.ln(0.8)
        paragraph(pdf, why)
        pdf.ln(4)

    pdf.add_page()
    heading(pdf, "Also found and fixed", 16)
    paragraph(pdf,
              "A separate sweep over the parts of the app the report did not reach - "
              "two reviewers reading code, plus live testing with a teacher and a "
              "student signed in side by side. Twelve issues, each reproduced before "
              "it was touched and re-checked after.",
              colour=MUTED)
    pdf.ln(2)
    for group, items in ALSO_FIXED:
        if pdf.get_y() > 240:
            pdf.add_page()
        pdf.set_font("Helvetica", "B", 11)
        pdf.set_text_color(*INK)
        pdf.multi_cell(0, 5.6, pdf_text(group), new_x="LMARGIN", new_y="NEXT")
        pdf.ln(0.8)
        bullets(pdf, items)
        pdf.ln(1.5)

    heading(pdf, "What this means for teachers", 16)
    bullets(pdf, TEACHER_NOTES)
    pdf.ln(2)
    rule(pdf)
    pdf.ln(3)
    paragraph(pdf,
              "The in-app Guide and the teacher and student handouts have been "
              "updated to describe the rule, so the documentation and the app cannot "
              "disagree. Every fix is covered by a test that fails without it.",
              size=10, colour=MUTED)

    return bytes(pdf.output())


def main() -> None:
    path = HERE / "MCQ Bug Report Response.pdf"
    path.write_bytes(build())
    print(f"  {path.name}  ({path.stat().st_size:,} bytes)")
    print()
    print("Done.")


if __name__ == "__main__":
    main()
