"""Deleting a class warns first, and only the confirmation deletes it.

Run with: python test_class_delete.py

Drives the real Streamlit runtime through `AppTest` with the database stubbed
out, so what is pinned is the behaviour on screen: pressing Delete opens a
warning that names what will happen and says it cannot be undone, Cancel
deletes nothing, and only "Yes, delete" calls through to the database.
"""

from __future__ import annotations

import sys
from pathlib import Path

from streamlit.testing.v1 import AppTest

HERE = Path(__file__).parent
HARNESS = HERE / "_class_delete_harness.py"

HARNESS_SOURCE = '''
import sys
sys.path.insert(0, r"{here}")

import streamlit as st
import teacher_portal as TP

CLASSES = st.session_state.setdefault("classes", [{{"id": 7, "name": "Section 501", "teacher_id": 1}},
                                                  {{"id": 8, "name": "Section 502", "teacher_id": 1}}])
DELETED = st.session_state.setdefault("deleted", [])

TP.require_session = lambda user: True
TP.teams_for_teacher = lambda teacher_id: [row for row in CLASSES if row["id"] not in DELETED]
TP.team_student_ids = lambda class_id: {{101, 102, 103}} if class_id == 7 else set()
TP.quizzes_for_teacher = lambda teacher_id: [{{"id": 50, "title": "Chapter 6"}}, {{"id": 51, "title": "Chapter 8"}}]
TP.assigned_student_ids = lambda quiz_id: {{101, 102, 103, 104}} if quiz_id == 50 else {{104}}
TP.delete_class = lambda teacher_id, class_id: DELETED.append(class_id)

TP.show_flash("classes")
TP._manage_classes({{"id": 1, "role": "teacher"}}, TP.teams_for_teacher(1), 0)
# In a browser a click inside a dialog re-runs only the dialog, which AppTest
# does not replay; holding it open by hand stands in for that re-run.
if st.session_state.get("hold-open"):
    TP.delete_class_dialog({{"id": 1, "role": "teacher"}}, st.session_state["hold-open"])
'''

failures = 0


def check(name: str, condition: bool, detail="") -> None:
    global failures
    if not condition:
        failures += 1
    print(f"  {'pass' if condition else 'FAIL'} {name}{'' if condition else f' -- got {detail!r}'}")


def button(app, label: str):
    return next(widget for widget in app.button if widget.label == label)


def page_text(app) -> str:
    return " ".join(str(getattr(element, "value", "")) for element in
                    [*app.warning, *app.markdown, *app.caption, *app.success, *app.info])


def main() -> int:
    HARNESS.write_text(HARNESS_SOURCE.format(here=str(HERE)), encoding="utf-8")
    try:
        app = AppTest.from_file(str(HARNESS), default_timeout=30)
        app.run()
        assert not app.exception, app.exception
        print("== Delete warns before it deletes ==")
        deletes = [widget for widget in app.button if widget.label == "Delete"]
        check("every class has a Delete button", len(deletes) == 2, len(deletes))

        deletes[0].click().run()
        assert not app.exception, app.exception
        text = page_text(app)
        check("pressing Delete deletes nothing yet", app.session_state["deleted"] == [], app.session_state["deleted"])
        check("it warns that this cannot be undone", "cannot be undone" in text, text[:300])
        check("names the class and how many students are in it",
              "Section 501" in text and "3 students" in text, text[:300])
        check("says the students stay on the roster", "stay on your roster" in text, text[:300])
        check("and that assignments stay", "stay assigned" in text, text[:300])
        check("naming the assessments assigned through it", "Chapter 6" in text and "Chapter 8" not in text, text[-300:])

        print("== inside the dialog ==")
        app.session_state["hold-open"] = 7
        app.run()
        button(app, "Cancel").click().run()
        check("Cancel deletes nothing", app.session_state["deleted"] == [], app.session_state["deleted"])
        button(app, "Yes, delete Section 501").click().run()
        assert not app.exception, app.exception
        check("only the confirmation deletes, and only that class", app.session_state["deleted"] == [7],
              app.session_state["deleted"])
        check("and the page says what happened", any("Section 501" in str(s.value) and "deleted" in str(s.value)
                                                      for s in app.success), [s.value for s in app.success])
        check("the deleted class is gone from the list",
              [w.label for w in app.button].count("Delete") == 1, [w.label for w in app.button])

        print("== an empty class ==")
        app.session_state["hold-open"] = 8
        app.run()
        text = page_text(app)
        check("still warns it cannot be undone", "cannot be undone" in text, text[:300])
        check("without counting students it doesn't have", "0 students" not in text, text[:300])
    finally:
        HARNESS.unlink(missing_ok=True)

    print()
    if failures:
        print(f"{failures} failed.")
        return 1
    print("All class-delete tests passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
