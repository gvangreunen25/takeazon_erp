import json

import streamlit as st
from sqlalchemy import text

from core.auth import ALL_MODULES, hash_password
from core.database import engine, execute_sql, log_audit, run_query


def render(auth_user: dict):
    current_user = auth_user["username"]
    st.title("🔐 User Management, Roles & Granular RBAC Permissions")

    u_tab1, u_tab2, u_tab3, u_tab4 = st.tabs([
        "👤 Users (`app_users`)",
        "🎭 Roles & Module Access (`app_roles`)",
        "🛡️ Granular Action Permissions (`role_permissions`)",
        "📜 Security & App Audit Logs",
    ])

    with u_tab1:
        users_df = run_query("""
            SELECT id, username, full_name, email, cell_phone, role_id, role, is_active, status, created_at
            FROM app_users
            ORDER BY id
        """)
        st.dataframe(users_df, use_container_width=True, hide_index=True)

        roles_df = run_query("SELECT role_id, role_name FROM app_roles")
        role_map = dict(zip(roles_df["role_id"], roles_df["role_name"])) if not roles_df.empty else {"admin": "Admin", "operator": "Operator"}

        col_u1, col_u2 = st.columns(2)
        with col_u1:
            st.markdown("**Create New User**")
            with st.form("create_user_form"):
                nu_username = st.text_input("Username *")
                nu_fullname = st.text_input("Full Name *")
                nu_email = st.text_input("Email")
                nu_phone = st.text_input("Cell Phone")
                nu_role_id = st.selectbox("Role *", list(role_map.keys()), format_func=lambda r: f"{role_map[r]} ({r})")
                nu_password = st.text_input("Password *", type="password")
                if st.form_submit_button("➕ Create User", type="primary") and nu_username and nu_password:
                    pw_hash = hash_password(nu_password)
                    execute_sql(
                        """
                        INSERT INTO app_users
                            (username, full_name, password_hash, role_id, is_active, email, cell_phone, role, status)
                        VALUES
                            (:u, :fn, :ph, :rid, 1, :em, :phn, :rname, 'Active')
                        """,
                        {
                            "u": nu_username.strip(),
                            "fn": nu_fullname.strip() or nu_username.strip(),
                            "ph": pw_hash,
                            "rid": nu_role_id,
                            "em": nu_email.strip(),
                            "phn": nu_phone.strip(),
                            "rname": role_map[nu_role_id],
                        },
                    )
                    log_audit(current_user, "USER_CREATED", f"Created user {nu_username}", f"Role: {nu_role_id}")
                    st.success(f"User `{nu_username}` created.")
                    st.rerun()

        with col_u2:
            st.markdown("**Update User Role / Password / Status**")
            if not users_df.empty:
                with st.form("edit_user_form"):
                    sel_u = st.selectbox("Select User", users_df["username"].tolist())
                    u_row = users_df[users_df["username"] == sel_u].iloc[0]
                    cur_rid = u_row["role_id"] if u_row["role_id"] in role_map else list(role_map.keys())[0]
                    ed_role = st.selectbox(
                        "Assign Role",
                        list(role_map.keys()),
                        index=list(role_map.keys()).index(cur_rid),
                        format_func=lambda r: f"{role_map[r]} ({r})",
                    )
                    ed_active = st.checkbox("Account Active", value=bool(u_row["is_active"]))
                    ed_new_pw = st.text_input("Reset Password (leave blank to keep current)", type="password")
                    if st.form_submit_button("💾 Save User Changes"):
                        execute_sql(
                            """
                            UPDATE app_users
                            SET role_id = :rid, role = :rname, is_active = :act, status = :st
                            WHERE username = :u
                            """,
                            {
                                "rid": ed_role,
                                "rname": role_map[ed_role],
                                "act": 1 if ed_active else 0,
                                "st": "Active" if ed_active else "Inactive",
                                "u": sel_u,
                            },
                        )
                        if ed_new_pw:
                            execute_sql(
                                "UPDATE app_users SET password_hash = :ph WHERE username = :u",
                                {"ph": hash_password(ed_new_pw), "u": sel_u},
                            )
                        log_audit(current_user, "USER_UPDATED", f"Updated user {sel_u}", f"Role: {ed_role}, Active: {ed_active}")
                        st.success(f"Updated `{sel_u}`.")
                        st.rerun()

    with u_tab2:
        st.subheader("Roles & Permitted Sidebar Modules (`app_roles`)")
        st.dataframe(run_query("SELECT * FROM app_roles"), use_container_width=True)

        with st.form("upsert_role_form"):
            r1, r2 = st.columns(2)
            rid_in = r1.text_input("Role ID (e.g., warehouse_picker, finance_mgr, buyer)")
            rname_in = r2.text_input("Role Display Name (e.g., Warehouse Picker)")

            selected_tabs = st.multiselect("Permitted Modules in Sidebar", ALL_MODULES, default=ALL_MODULES[:3])

            f1, f2, f3 = st.columns(3)
            f_prof = f1.checkbox("Can Save Profitability (`can_save_profitability`)")
            f_sync = f2.checkbox("Can Live Sync Channels (`can_live_sync`)")
            f_tbl = f3.checkbox("Can Edit Table Settings (`can_edit_table_settings`)")

            if st.form_submit_button("💾 Save Role", type="primary") and rid_in:
                clean_rid = rid_in.strip()
                clean_rname = rname_in.strip() or clean_rid
                with engine.begin() as conn:
                    conn.execute(
                        text("""
                            INSERT INTO app_roles
                                (role_id, role_name, allowed_tabs, can_save_profitability, can_live_sync, can_edit_table_settings)
                            VALUES
                                (:rid, :rname, :tabs, :cp, :cs, :ct)
                            ON DUPLICATE KEY UPDATE
                                role_name = :rname,
                                allowed_tabs = :tabs,
                                can_save_profitability = :cp,
                                can_live_sync = :cs,
                                can_edit_table_settings = :ct
                        """),
                        {
                            "rid": clean_rid,
                            "rname": clean_rname,
                            "tabs": json.dumps(selected_tabs),
                            "cp": 1 if f_prof else 0,
                            "cs": 1 if f_sync else 0,
                            "ct": 1 if f_tbl else 0,
                        },
                    )
                    conn.execute(
                        text("INSERT IGNORE INTO system_roles (role_name, description) VALUES (:rn, :desc)"),
                        {"rn": clean_rid, "desc": clean_rname},
                    )
                log_audit(current_user, "ROLE_SAVED", f"Saved role {clean_rid}", f"Modules: {len(selected_tabs)}")
                st.success(f"Saved role `{clean_rid}`.")
                st.rerun()

    with u_tab3:
        st.subheader("Granular Action-Level Permission Matrix (`role_permissions`)")
        sp_df = run_query("SELECT permission_key, module, description FROM system_permissions ORDER BY module, permission_key")
        roles_for_matrix = run_query("SELECT role_id, role_name FROM app_roles")

        if not roles_for_matrix.empty and not sp_df.empty:
            target_role = st.selectbox(
                "Configure Granular Permissions for Role",
                roles_for_matrix["role_id"].tolist(),
                format_func=lambda r: f"{dict(zip(roles_for_matrix['role_id'], roles_for_matrix['role_name']))[r]} ({r})",
            )
            existing_rp = run_query(
                "SELECT permission_key FROM role_permissions WHERE role_name = :rn",
                {"rn": target_role},
            )
            granted_set = set(existing_rp["permission_key"].tolist()) if not existing_rp.empty else set()

            with st.form("granular_perms_form"):
                checked_keys = []
                for _, prow in sp_df.iterrows():
                    pk = prow["permission_key"]
                    label = f"**`{pk}`** — {prow['description']} *(Module: {prow['module']})*"
                    if st.checkbox(label, value=(pk in granted_set), key=f"perm_{target_role}_{pk}"):
                        checked_keys.append(pk)

                if st.form_submit_button("✅ Save Granular Permissions", type="primary"):
                    with engine.begin() as conn:
                        conn.execute(
                            text("DELETE FROM role_permissions WHERE role_name = :rn"),
                            {"rn": target_role},
                        )
                        if checked_keys:
                            conn.execute(
                                text("INSERT INTO role_permissions (role_name, permission_key) VALUES (:rn, :pk)"),
                                [{"rn": target_role, "pk": pk} for pk in checked_keys],
                            )
                    log_audit(
                        current_user,
                        "PERMISSIONS_UPDATED",
                        f"Updated granular permissions for {target_role}",
                        f"Granted: {', '.join(checked_keys)}",
                    )
                    st.success(f"Saved {len(checked_keys)} granular permissions for `{target_role}`.")

    with u_tab4:
        st.subheader("App & Security Audit Trail (`app_audit_logs`)")
        st.dataframe(run_query("SELECT * FROM app_audit_logs ORDER BY timestamp DESC LIMIT 200"), use_container_width=True)
