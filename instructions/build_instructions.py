"""Write the MCQ guides to disk as PDF and DOCX.

The content lives in `guide.py` and is exactly what the website's Guide page
shows, so the handouts and the site can never disagree. The old version of this
script kept its own copy of the text; that copy is gone. Run from anywhere:

    python instructions/build_instructions.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from docx import Document  # noqa: E402
from docx.shared import Pt, RGBColor  # noqa: E402

import guide  # noqa: E402

HERE = Path(__file__).resolve().parent
GREEN = RGBColor(0x12, 0x69, 0x4F)
MUTED = RGBColor(0x6B, 0x7C, 0x74)

DOCUMENTS = [
    (guide.TEACHER_GUIDE, "MCQ Teacher Guide", "Building and running assessments on MCQ"),
    (guide.STUDENT_GUIDE, "MCQ Student Guide", "How to take assessments on MCQ"),
]


def build_docx(sections: list, title: str, subtitle: str, path: Path) -> None:
    document = Document()
    heading = document.add_heading(title, level=0)
    for run in heading.runs:
        run.font.color.rgb = GREEN
    lede = document.add_paragraph(subtitle)
    for run in lede.runs:
        run.font.color.rgb = MUTED
        run.font.size = Pt(12)

    for number, (section, blocks) in enumerate(sections, 1):
        section_heading = document.add_heading(f"{number}. {section}", level=1)
        for run in section_heading.runs:
            run.font.color.rgb = GREEN
        for kind, payload in blocks:
            if kind == "h3":
                document.add_heading(payload, level=2)
            elif kind == "p":
                document.add_paragraph(payload)
            elif kind == "ul":
                for item in payload:
                    document.add_paragraph(item, style="List Bullet")
            elif kind == "ol":
                for item in payload:
                    document.add_paragraph(item, style="List Number")
            elif kind == "note":
                note = document.add_paragraph()
                run = note.add_run(f"Note: {payload}")
                run.italic = True
                run.font.color.rgb = GREEN
    document.save(str(path))


def main() -> None:
    for sections, title, subtitle in DOCUMENTS:
        pdf_path = HERE / f"{title}.pdf"
        docx_path = HERE / f"{title}.docx"
        pdf_path.write_bytes(guide.to_pdf(sections, title, subtitle))
        build_docx(sections, title, subtitle, docx_path)
        print(f"  {pdf_path.name}   ({pdf_path.stat().st_size:,} bytes)")
        print(f"  {docx_path.name}  ({docx_path.stat().st_size:,} bytes)")
    print("\nDone. These are the same words the in-app Guide page shows.")


if __name__ == "__main__":
    main()
