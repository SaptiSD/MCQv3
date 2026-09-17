"""MCQ-BUG-022: the blank white page. Run with: python test_editor_callbacks.py

A tester reported the teacher's screen going completely white after saving an
answer key, with the save having gone through. The same crash was then hit a
second way -- open Manage, add a question, press Close without saving -- and that
one came with a traceback:

    KeyError: st.session_state has no key "manual-type-137-2-0"
    ... in _write:  value = st.session_state[widget_key]

Streamlit runs a widget's `on_change` for whatever values the browser sends at
the start of a run, and the browser is always one render behind. `reset_editor_state`
renamed every widget (bumping the epoch) and deleted the old keys on top of that;
the old widgets' callbacks still arrived, read a key that was gone, and raised.
Callbacks run before the script body, so nothing had been drawn -- hence a white
page rather than an error, and no way out but a manual refresh.

Worth knowing, because it moves where the fix has to live: Streamlit culls the
state of any widget it did not render, so those keys disappear whether the app
deletes them or not. Not deleting them is just not fighting the framework; what
actually stops the crash is every callback checking that the widget it was told
to read is still there.

These run the real Streamlit runtime through `AppTest`, because the bug lives in
the order the runtime does things and nothing simpler would have caught it.
"""

from streamlit.testing.v1 import AppTest

results = []


def check(name, condition):
    results.append((name, bool(condition)))
    print(("  pass " if condition else "  FAIL ") + name)


# The editor's shape, reduced to what the bug needs: a working copy in session
# state, widgets keyed by an epoch, callbacks that copy values back into the
# working copy, and the real `reset_editor_state` behind Close.
EDITOR = """
import streamlit as st
import teacher_portal as TP

QUIZ = 137
STATE = TP.editor_state_key(QUIZ)

if STATE not in st.session_state:
    st.session_state[STATE] = [{"type": "Multiple choice", "text": "one"}]
draft = st.session_state[STATE]
epoch = TP.editor_epoch(QUIZ)


def _write(field, index, widget_key):
    if TP._vanished(widget_key) or index >= len(draft):
        return
    draft[index][field] = st.session_state[widget_key]


for index, entry in enumerate(draft):
    key = f"manual-type-{QUIZ}-{epoch}-{index}"
    st.selectbox("Question type", ["Multiple choice", "True / False"],
                 index=["Multiple choice", "True / False"].index(entry["type"]),
                 key=key, on_change=_write, args=("type", index, key))

if st.button("Add another question"):
    draft.append({"type": "Multiple choice", "text": "two"})
    st.rerun()

if st.button("Close manager"):
    TP.reset_editor_state(QUIZ)
    st.rerun()

st.text(f"epoch={epoch} questions={len(draft)}")
"""


print("== a late callback after reset_editor_state is survivable ==")
at = AppTest.from_string(EDITOR, default_timeout=60).run()
check("the editor starts at epoch 0", "epoch=0" in at.text[0].value)

# Touch the widget so the browser has a value to send back, the way a teacher
# who has edited something does.
at.selectbox[0].select("True / False").run()
check("changing a field does not raise", not at.exception)

at.button[1].click().run()          # Close manager
check("closing does not raise", not at.exception)
check("the epoch moved on", "epoch=1" in at.text[0].value)
# Streamlit culls the state of widgets it did not render, so the epoch-0 key is
# gone here no matter what the app does -- which is exactly why the guard in the
# callbacks is the fix and not-deleting is merely not fighting the framework.
check("the renamed-away widget is gone either way",
      "manual-type-137-0-0" not in at.session_state)
check("and its callback arriving late is survivable", not at.exception)


print()
print("== the tester's sequence: add a question, then Close without saving ==")
at = AppTest.from_string(EDITOR, default_timeout=60).run()
at.selectbox[0].select("True / False").run()
at.button[0].click().run()          # Add another question
check("a second question appears", "questions=2" in at.text[0].value)
at.selectbox[1].select("True / False").run()
check("editing the new question is fine", not at.exception)

at.button[1].click().run()          # Close manager, without saving
if at.exception:
    for exc in at.exception:
        print("       ", exc.value)
check("closing without saving does not blank the page", not at.exception)
check("the working copy reloaded to one question", "questions=1" in at.text[0].value)

# The callback for the question that no longer exists must simply do nothing,
# rather than reaching past the end of the reloaded draft.
at.selectbox[0].select("Multiple choice").run()
check("and the editor still works afterwards", not at.exception)


print()
print("== Discard draft empties the form by renaming it, not by deleting keys ==")
# Deleting a widget's key cannot empty it: the browser still holds what was
# typed, re-sends it on the next run, and Streamlit restores it under the same
# key. The Create page had no epoch, so Discard draft looked like it did
# nothing -- the title came straight back and the callback wrote it into a fresh
# draft. Verified in the browser too; this pins the pieces.
DISCARD = """
import streamlit as st
import teacher_portal as TP

st.text(f"widget_key={TP._ck('new-title')}")
st.text(f"epoch_key_is_swept={TP._CREATE_EPOCH.startswith('new-')}")

if st.button("type something"):
    st.session_state[TP._ck("new-title")] = "a draft"
    TP._create_save_setting("new-title")
    st.rerun()

if st.button("discard"):
    TP._clear_new_quiz_state()
    st.rerun()

st.text(f"draft={dict(st.session_state.get('new_quiz_data') or {})}")
"""
at = AppTest.from_string(DISCARD, default_timeout=60).run()
check("the widget key carries the epoch", at.text[0].value.endswith("~0"))
check("the epoch key would not be swept away by its own cleanup",
      "epoch_key_is_swept=False" in at.text[1].value)

at.button[0].click().run()
check("a typed value lands in the draft under its bare name",
      "'new-title': 'a draft'" in at.text[2].value)
check("and the widget key is still the epoch-0 one", at.text[0].value.endswith("~0"))

at.button[1].click().run()
check("discarding empties the draft", "draft={}" in at.text[2].value)
check("and renames every box by bumping the epoch", at.text[0].value.endswith("~1"))
check("no exception on the way through", not at.exception)

# The form has to keep working afterwards, or publishing would silently lose
# everything typed after a discard.
at.button[0].click().run()
check("typing after a discard still reaches the draft",
      "'new-title': 'a draft'" in at.text[2].value)
check("under the bare name, not the epoched one",
      "~1" not in at.text[2].value)


print()
print("== a callback whose widget is genuinely gone does nothing ==")
GUARD = """
import streamlit as st
import teacher_portal as TP

if "new_quiz_data" not in st.session_state:
    st.session_state["new_quiz_data"] = {"new-title": "kept"}
# Exactly what `_clear_new_quiz_state` leaves behind: the widget key is gone,
# but the browser still sends its value and Streamlit still calls the callback.
TP._create_save_setting("new-title")
TP._create_save_typed("new-typed-0-answer")
st.text(str(st.session_state["new_quiz_data"]))
"""
at = AppTest.from_string(GUARD, default_timeout=60).run()
check("neither create-page callback raises", not at.exception)
if at.exception:
    for exc in at.exception:
        print("       ", exc.value)
else:
    check("and neither invents a value", "kept" in at.text[0].value)


print()
failed = [name for name, ok in results if not ok]
print(f"{len(results) - len(failed)} passed, {len(failed)} failed.")
if failed:
    raise SystemExit("FAILED: " + ", ".join(failed))
print("All editor-callback tests passed.")
