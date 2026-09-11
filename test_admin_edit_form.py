"""The admin console's edit forms must follow the account picker.

A Streamlit text box keeps whatever its key already holds and ignores `value` on
later runs. The edit forms therefore have to key their boxes by the id of the
account being edited: with one shared key, moving the picker to a different
person left their name and email showing the *previous* person's details, and
saving wrote those details onto the newly selected account.

The boxes are found by position rather than by key, so this tests the behaviour
rather than the naming scheme that currently delivers it.

Run with `python test_admin_edit_form.py`.
"""

from __future__ import annotations

import sys
from pathlib import Path

from streamlit.testing.v1 import AppTest

HERE = Path(__file__).parent
HARNESS = HERE / "_admin_edit_harness.py"

# Text boxes in render order: three for "Add", then three for "Edit".
EDIT_NAME, EDIT_EMAIL = 3, 4
ADMIN_EDIT_EMAIL, ADMIN_EDIT_NAME = 9, 10

# A standalone script for AppTest to execute. It stubs out the database so the
# test exercises widget state and nothing else.
HARNESS_SOURCE = '''
import sys
sys.path.insert(0, r"{here}")

import admin_portal

TEACHERS = [
    {{"id": 1, "name": "Ada Teacher", "email": "ada@example.com", "role": "teacher"}},
    {{"id": 2, "name": "Bo Teacher", "email": "bo@example.com", "role": "teacher"}},
]
ADMINS = [
    {{"id": 10, "name": "Root Admin", "email": "root@example.com"}},
    {{"id": 11, "name": "Second Admin", "email": "second@example.com"}},
]

admin_portal.users_by_role = lambda role: TEACHERS if role == "teacher" else []
admin_portal.admins_list = lambda: ADMINS

admin_portal.user_section("Teachers", "cap", "teacher", "teachers")
admin_portal.admins_section({{"id": 10}})
'''

failures = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    global failures
    if not condition:
        failures += 1
    print(f"  {'pass' if condition else 'FAIL'} {name}{'' if condition else f' -- got {detail!r}'}")


def main() -> int:
    HARNESS.write_text(HARNESS_SOURCE.format(here=str(HERE)), encoding="utf-8")
    try:
        app = AppTest.from_file(str(HARNESS), default_timeout=30)
        app.run()
        assert not app.exception, app.exception

        print("== the teacher edit form follows the picker ==")
        check("starts on the first teacher", app.selectbox[0].value == 1, app.selectbox[0].value)
        check("shows their name", app.text_input[EDIT_NAME].value == "Ada Teacher", app.text_input[EDIT_NAME].value)
        check("shows their email", app.text_input[EDIT_EMAIL].value == "ada@example.com", app.text_input[EDIT_EMAIL].value)

        # The reported bug: edit the boxes, then move the picker. The boxes must
        # show the newly selected teacher, not the edits meant for the previous one.
        app.text_input[EDIT_NAME].set_value("Edited Ada").run()
        app.selectbox[0].select(2).run()
        assert not app.exception, app.exception

        check("picker moved to the second teacher", app.selectbox[0].value == 2, app.selectbox[0].value)
        check("name box follows the picker", app.text_input[EDIT_NAME].value == "Bo Teacher", app.text_input[EDIT_NAME].value)
        check("email box follows the picker", app.text_input[EDIT_EMAIL].value == "bo@example.com", app.text_input[EDIT_EMAIL].value)

        print("== the administrator edit form follows its picker ==")
        check("starts on the first admin", app.text_input[ADMIN_EDIT_NAME].value == "Root Admin", app.text_input[ADMIN_EDIT_NAME].value)
        app.text_input[ADMIN_EDIT_NAME].set_value("Edited Root").run()
        app.selectbox[2].select(11).run()
        assert not app.exception, app.exception
        check("admin name box follows the picker", app.text_input[ADMIN_EDIT_NAME].value == "Second Admin", app.text_input[ADMIN_EDIT_NAME].value)
        check("admin email box follows the picker", app.text_input[ADMIN_EDIT_EMAIL].value == "second@example.com", app.text_input[ADMIN_EDIT_EMAIL].value)
    finally:
        HARNESS.unlink(missing_ok=True)

    print()
    if failures:
        print(f"{failures} failed.")
        return 1
    print("All admin edit-form tests passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
