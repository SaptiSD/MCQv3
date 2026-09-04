"""Admin console for managing teachers, students, and other administrators."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from repository import (add_admin, admins_list, create_user, remove_admin,
                        remove_user, update_admin, update_user, users_by_role)


def dashboard(user) -> None:
    st.markdown('<div class="eyebrow">Admin console</div><h1>Manage the workspace</h1>', unsafe_allow_html=True)
    st.caption("Add, edit, and remove teachers, students, and fellow administrators.")
    st.divider()
    user_section("Teachers", "Accounts that can build, assign, and review assessments.", "teacher", "teachers")
    user_section("Students", "Student accounts available across the platform.", "student", "students")
    admins_section(user)


def user_section(title: str, caption: str, role: str, prefix: str) -> None:
    singular = title.lower()[:-1]
    with st.container(border=True):
        st.subheader(title)
        st.caption(caption)
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
                st.success(f"{add_name.strip()} was added as a {singular}.")
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
                    st.success("Changes saved.")
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
                    st.success(f"{singular.title()} removed.")
                    st.rerun()


def admins_section(user) -> None:
    with st.container(border=True):
        st.subheader("Administrators")
        st.caption("Accounts that can log in and manage this workspace.")
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
                st.success(f"{add_name.strip() or add_email.strip()} was added as an administrator.")
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
                    st.success("Changes saved.")
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
                        st.success("Administrator removed.")
                        st.rerun()