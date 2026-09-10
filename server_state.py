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

import time

import streamlit as st

# A draft older than this is stale rather than useful; drop it so the store
# cannot grow without bound in a long-lived process.
DRAFT_TTL_SECONDS = 24 * 60 * 60


@st.cache_resource
def _store() -> dict:
    return {"epochs": {}, "drafts": {}}


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
