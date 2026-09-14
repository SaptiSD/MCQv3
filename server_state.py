"""Server-side state that outlives a single browser tab.

Streamlit keeps `st.session_state` per browser connection, so a second tab is a
second session that knows nothing about the first, and a page refresh throws the
session away entirely. Two things need to survive that:

* **Sign-out has to reach every tab.** Each sign-in stamps the tab with the
  account's current *epoch*. Signing out (or signing in under a different role)
  bumps the epoch, and every other tab notices on its next interaction and is
  sent back to the login page.
* **A half-built quiz has to survive a refresh.** The Create quiz page mirrors
  its draft here, keyed by account, and restores it when the page comes back.

Both live in one `@st.cache_resource` dict, which is shared by every session in
the server process and is exactly as durable as the process itself. That is the
right lifetime: a restart is a deploy, and nobody expects a draft to outlive one.
"""

from __future__ import annotations

import threading
import time

import streamlit as st

# A draft older than this is stale rather than useful; drop it so the store
# cannot grow without bound in a long-lived process.
DRAFT_TTL_SECONDS = 24 * 60 * 60


# A publish claim is held while the write is in flight and kept afterwards so a
# replayed click is recognised rather than re-run. Long enough to outlive a
# reconnect, short enough that the store cannot grow without bound.
PUBLISH_CLAIM_SECONDS = 120
PUBLISH_MEMORY_SECONDS = 60 * 60

_lock = threading.Lock()


@st.cache_resource
def _store() -> dict:
    return {"epochs": {}, "drafts": {}, "publishes": {}}


def account_key(user) -> str:
    """Identify the human, not the session — the same person in two tabs is one key."""
    return str(user.get("email") or user.get("id") or "").strip().casefold()


# --------------------------------------------------------------------------- sessions


def current_epoch(key: str) -> int:
    return _store()["epochs"].get(key, 0)


def revoke(key: str) -> None:
    """Invalidate every tab currently signed in to this account."""
    if key:
        epochs = _store()["epochs"]
        epochs[key] = epochs.get(key, 0) + 1


def stamp_session(user) -> None:
    """Record which epoch this tab signed in at."""
    st.session_state["session_epoch"] = current_epoch(account_key(user))


def session_is_stale(user) -> bool:
    """True when this tab's sign-in has been superseded elsewhere."""
    key = account_key(user)
    if not key:
        return False
    stamped = st.session_state.get("session_epoch")
    if stamped is None:
        # A session from before this check existed; adopt the current epoch.
        st.session_state["session_epoch"] = current_epoch(key)
        return False
    return stamped != current_epoch(key)


# --------------------------------------------------------------------------- drafts


def save_draft(key: str, name: str, draft: dict) -> None:
    if key:
        _store()["drafts"][(key, name)] = {"at": time.time(), "draft": draft}


def load_draft(key: str, name: str) -> dict | None:
    entry = _store()["drafts"].get((key, name))
    if not entry:
        return None
    if time.time() - entry["at"] > DRAFT_TTL_SECONDS:
        _store()["drafts"].pop((key, name), None)
        return None
    return entry["draft"]


def clear_draft(key: str, name: str) -> None:
    _store()["drafts"].pop((key, name), None)


# --------------------------------------------------------------------------- publishing


def _expire_publishes(publishes: dict) -> None:
    now = time.time()
    for entry_key in [key for key, entry in publishes.items() if now - entry["at"] > PUBLISH_MEMORY_SECONDS]:
        publishes.pop(entry_key, None)


def claim_publish(key: str, token: str) -> str:
    """Try to become the one script run that publishes `token`.

    `st.session_state` cannot see a second browser tab, so the in-session guard
    against a double-clicked Publish button did nothing when the two clicks
    landed in different sessions -- or when one arrived after a reconnect. The
    claim lives in the shared store instead, so every tab on this server sees it.

    Returns "claimed" (go ahead), "in_flight" (someone else is mid-publish) or
    "done" (it already happened; `publish_result` has the quiz id).
    """
    if not key or not token:
        return "claimed"
    with _lock:
        publishes = _store()["publishes"]
        _expire_publishes(publishes)
        entry = publishes.get((key, token))
        if entry is None:
            publishes[(key, token)] = {"at": time.time(), "quiz_id": None}
            return "claimed"
        if entry["quiz_id"] is not None:
            return "done"
        if time.time() - entry["at"] > PUBLISH_CLAIM_SECONDS:
            # The run that held this never came back. Let the next one try.
            entry["at"] = time.time()
            return "claimed"
        return "in_flight"


def finish_publish(key: str, token: str, quiz_id: int) -> None:
    if not key or not token:
        return
    with _lock:
        _store()["publishes"][(key, token)] = {"at": time.time(), "quiz_id": quiz_id}


def release_publish(key: str, token: str) -> None:
    """Hand the claim back after a failed publish so the teacher can retry."""
    if not key or not token:
        return
    with _lock:
        _store()["publishes"].pop((key, token), None)


def publish_result(key: str, token: str):
    if not key or not token:
        return None
    with _lock:
        entry = _store()["publishes"].get((key, token))
    return entry["quiz_id"] if entry else None
