"""Admin console for managing teachers, students, and other administrators."""

from __future__ import annotations

import pandas as pd
import streamlit as st

import server_state
from ui import flash, page_header, show_flash
from repository import (add_admin, admins_list, assign_role, create_user, remove_admin,
                        remove_user, update_admin, update_user, users_by_role)


def dashboard(user) -> None:
    page_header("Admin console", "Manage the workspace", "Teachers, students and administrators — and moving accounts between roles.")
    st.divider()
    role_section()
    user_section("Teachers", "Accounts that can build, assign, and review assessments.", "teacher", "teachers")
    user_section("Students", "Student accounts available across the platform.", "student", "students")
    admins_section(user)


def role_section() -> None:
    """Change an existing account's role. Sign-ups no longer wait for approval."""
    with st.container(border=True):
        st.subheader("Change a role")
        st.caption("Accounts are created with a role the moment someone signs in, so nothing waits here for approval. Use this to move someone between roles.")
        accounts = [*users_by_role("teacher"), *users_by_role("student"), *users_by_role("unassigned")]
        if not accounts:
            st.info("No accounts yet.")
            return
        show_flash("role")
        # The label deliberately leaves the role out. A selectbox keeps showing the
        # label it was rendered with, so baking in a value that this very control
        # changes would leave the closed box insisting on the old role.
        options = {row["id"]: f"{row['name']}  ·  {row['email']}" for row in accounts}
        by_id = {row["id"]: row for row in accounts}
        pick, choose = st.columns([3, 2])
        with pick:
            user_id = st.selectbox("Select account", list(options), format_func=options.get, key="role-select")
        with choose:
            role = st.selectbox("New role", ["student", "teacher", "admin"], key="role-new")
        current = by_id[user_id]["role"]
        st.caption(f"Currently a **{current}**.")
        if role == "admin":
            st.caption("Promoting to administrator moves the account into the administrators table; they sign in with Google.")
        if st.button("Change role", type="primary", key="role-assign", width="stretch"):
            if role == current:
                st.info(f"{by_id[user_id]['name']} is already a {current}.")
            else:
                try:
                    assign_role(user_id, role)
                except ValueError as exc:
                    st.error(str(exc))
                else:
                    # Any tab that account has open is now signed in under the old
                    # role, so end those sessions and make them sign in again.
                    server_state.revoke(server_state.account_key(by_id[user_id]))
                    flash("role", f"{by_id[user_id]['name']} is now a {role}.")
                    st.rerun()


def user_section(title: str, caption: str, role: str, prefix: str) -> None:
    singular = title.lower()[:-1]
    with st.container(border=True):
        st.subheader(title)
        st.caption(caption)
        show_flash(prefix)
        rows = users_by_role(role)
        if rows:
            st.dataframe(pd.DataFrame([{"Name": row["name"], "Email": row["email"]} for row in rows]), width="stretch", hide_index=True)
        else:
            st.info(f"No {title.lower()} yet.")
        st.divider()
        st.markdown(f"**Add {singular}**")
        add_cols = st.columns(3)
        with add_cols[0]: add_name = st.text_input("Name", key=f"{prefix}-add-name")
        with add_cols[1]: add_email = st.text_input("Email", key=f"{prefix}-add-email")
        with add_cols[2]: add_password = st.text_input("Password", type="password", key=f"{prefix}-add-password")
        if st.button(f"Add {singular}", type="primary", key=f"{prefix}-add", width="stretch"):
            try:
                create_user(add_name, add_email, role, add_password)
            except ValueError as exc:
                st.error(str(exc))
            else:
                flash(prefix, f"{add_name.strip()} was added as a {singular}.")
                st.rerun()
        if rows:
            st.divider()
            st.markdown(f"**Edit {singular}**")
            edit_options = {row["id"]: f"{row['name']}  ·  {row['email']}" for row in rows}
            edit_id = st.selectbox(f"Select {singular}", list(edit_options), format_func=edit_options.get, key=f"{prefix}-edit-select")
            selected = next(row for row in rows if row["id"] == edit_id)
            edit_cols = st.columns(3)
            with edit_cols[0]: edit_name = st.text_input("Name", value=selected["name"], key=f"{prefix}-edit-name")
            with edit_cols[1]: edit_email = st.text_input("Email", value=selected["email"], key=f"{prefix}-edit-email")
            with edit_cols[2]: edit_password = st.text_input("New password", type="password", key=f"{prefix}-edit-password")
            if st.button("Save changes", key=f"{prefix}-edit-save", width="stretch"):
                try:
                    update_user(edit_id, edit_name, edit_email, edit_password or None)
                except ValueError as exc:
                    st.error(str(exc))
                else:
                    flash(prefix, f"Changes to {edit_options[edit_id]} saved.")
                    st.rerun()
            st.divider()
            st.markdown(f"**Remove {singular}**")
            remove_options = {row["id"]: row["email"] for row in rows}
            remove_id = st.selectbox(f"Select {singular} to remove", list(remove_options), format_func=remove_options.get, key=f"{prefix}-remove-select")
            if st.button(f"Remove {singular}", key=f"{prefix}-remove", width="stretch"):
                try:
                    remove_user(remove_id)
                except ValueError as exc:
                    st.error(str(exc))
                else:
                    # Don't leave a deleted account signed in somewhere.
                    server_state.revoke(server_state.account_key({"email": remove_options[remove_id]}))
                    flash(prefix, f"{singular.title()} {remove_options[remove_id]} removed.")
                    st.rerun()


def admins_section(user) -> None:
    with st.container(border=True):
        st.subheader("Administrators")
        st.caption("Accounts that can log in and manage this workspace.")
        show_flash("admins")
        rows = admins_list()
        if rows:
            st.dataframe(pd.DataFrame([{"Email": row["email"], "Name": row["name"]} for row in rows]), width="stretch", hide_index=True)
        else:
            st.info("No administrators yet.")
        st.divider()
        st.markdown("**Add administrator**")
        add_cols = st.columns(3)
        with add_cols[0]: add_email = st.text_input("Email", key="admins-add-email")
        with add_cols[1]: add_password = st.text_input("Password", type="password", key="admins-add-password")
        with add_cols[2]: add_name = st.text_input("Name", key="admins-add-name")
        if st.button("Add administrator", type="primary", key="admins-add", width="stretch"):
            try:
                add_admin(add_email, add_password, add_name)
            except ValueError as exc:
                st.error(str(exc))
            else:
                flash("admins", f"{add_name.strip() or add_email.strip()} was added as an administrator.")
                st.rerun()
        if rows:
            st.divider()
            st.markdown("**Edit administrator**")
            edit_options = {row["id"]: row["email"] for row in rows}
            edit_id = st.selectbox("Select administrator", list(edit_options), format_func=edit_options.get, key="admins-edit-select")
            selected = next(row for row in rows if row["id"] == edit_id)
            edit_cols = st.columns(3)
            with edit_cols[0]: edit_email = st.text_input("Email", value=selected["email"], key="admins-edit-email")
            with edit_cols[1]: edit_name = st.text_input("Name", value=selected["name"], key="admins-edit-name")
            with edit_cols[2]: edit_password = st.text_input("New password", type="password", key="admins-edit-password")
            if st.button("Save changes", key="admins-edit-save", width="stretch"):
                try:
                    update_admin(edit_id, edit_email, edit_name, edit_password or None)
                except ValueError as exc:
                    st.error(str(exc))
                else:
                    flash("admins", f"Changes to {edit_options[edit_id]} saved.")
                    st.rerun()
            st.divider()
            st.markdown("**Remove administrator**")
            remove_options = {row["id"]: row["email"] for row in rows}
            remove_id = st.selectbox("Select administrator to remove", list(remove_options), format_func=remove_options.get, key="admins-remove-select")
            if st.button("Remove administrator", key="admins-remove", width="stretch"):
                if remove_id == user["id"]:
                    st.error("You cannot remove your own admin account.")
                else:
                    try:
                        remove_admin(remove_id)
                    except ValueError as exc:
                        st.error(str(exc))
                    else:
                        flash("admins", f"Administrator {remove_options[remove_id]} removed.")
                        st.rerun()