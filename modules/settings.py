import streamlit as st
from core.database import execute_sql, log_audit, run_query
from core.secrets import get_db_secret, load_all_db_configs


def render(auth_user: dict):
    current_user = auth_user["username"]
    st.title("⚙️ Database-Backed Secrets, Channel Credentials & System Settings")

    s1, s2, s3, s4 = st.tabs([
        "`app_secrets`",
        "`channel_credentials`",
        "`system_settings` (incl. `app_requirements`)",
        "`app_xero_tokens`",
    ])

    with s1:
        st.dataframe(run_query("SELECT secret_key, category, updated_at FROM app_secrets ORDER BY category, secret_key"), use_container_width=True)
        col_sec1, col_sec2 = st.columns(2)
        with col_sec1:
            with st.form("upsert_app_secret"):
                st.markdown("**Add / Update Secret (`app_secrets`)**")
                k = st.selectbox(
                    "Secret Key",
                    [
                        "AMAZON_LWA_CLIENT_ID",
                        "AMAZON_LWA_CLIENT_SECRET",
                        "AMAZON_REFRESH_TOKEN",
                        "AMAZON_SP_API_URL",
                        "TCG_API_TOKEN",
                        "TCG_API_URL",
                        "XERO_CLIENT_ID",
                        "XERO_CLIENT_SECRET",
                    ],
                )
                custom_k = st.text_input("Custom Secret Key (overrides dropdown if filled)")
                v = st.text_input("Secret Value", type="password")
                cat = st.text_input("Category", value="amazon")
                if st.form_submit_button("💾 Save to `app_secrets`", type="primary"):
                    final_k = custom_k.strip() or k.strip()
                    if final_k and v:
                        execute_sql(
                            "INSERT INTO app_secrets (secret_key, secret_value, category) VALUES (:k, :v, :c) ON DUPLICATE KEY UPDATE secret_value = :v, category = :c",
                            {"k": final_k, "v": v.strip(), "c": cat.strip()},
                        )
                        load_all_db_configs.clear()
                        log_audit(current_user, "SECRET_UPDATED", f"Updated secret {final_k}", f"Category: {cat}")
                        st.success(f"Updated `{final_k}` in `app_secrets`.")
                        st.rerun()

        with col_sec2:
            sec_keys_df = run_query("SELECT secret_key FROM app_secrets ORDER BY secret_key")
            if not sec_keys_df.empty:
                with st.form("delete_app_secret"):
                    st.markdown("**Delete Secret Key (`app_secrets`)**")
                    del_k = st.selectbox("Select Key to Remove", sec_keys_df["secret_key"].tolist())
                    if st.form_submit_button("🗑️ Delete Selected Secret"):
                        execute_sql("DELETE FROM app_secrets WHERE secret_key = :k", {"k": del_k})
                        load_all_db_configs.clear()
                        log_audit(current_user, "SECRET_DELETED", f"Deleted secret {del_k}", "")
                        st.warning(f"Deleted `{del_k}` from `app_secrets`.")
                        st.rerun()

    with s2:
        ch_table_df = run_query(
            "SELECT id, channel_name, api_url, api_endpoint, is_active, last_connection_test FROM channel_credentials"
        )
        st.dataframe(ch_table_df, use_container_width=True, hide_index=True)
        with st.form("upsert_channel_cred"):
            st.markdown("**Add / Update Channel Credential (`channel_credentials`)**")
            cc1, cc2, cc3 = st.columns(3)
            ch_name = cc1.text_input("Channel Name *", placeholder="e.g., AMAZON_ZA, Takealot, The Courier Guy")
            ch_url = cc2.text_input("API URL *", placeholder="https://sellingpartnerapi-eu.amazon.com")
            ch_active = cc3.checkbox("Is Active", value=True)
            cc4, cc5 = st.columns(2)
            ch_key = cc4.text_input("API Key / Client ID (leave blank to keep existing)", type="password")
            ch_sec = cc5.text_input("API Secret / Client Secret (leave blank to keep existing)", type="password")
            if st.form_submit_button("💾 Save Channel Credential", type="primary") and ch_name.strip():
                existing = run_query(
                    "SELECT id FROM channel_credentials WHERE LOWER(channel_name) = LOWER(:cn) LIMIT 1",
                    {"cn": ch_name.strip()},
                )
                if existing.empty:
                    execute_sql(
                        """
                        INSERT INTO channel_credentials (channel_name, api_url, api_endpoint, api_key, api_secret, is_active)
                        VALUES (:cn, :url, :url, :k, :s, :act)
                        """,
                        {
                            "cn": ch_name.strip(),
                            "url": ch_url.strip(),
                            "k": ch_key.strip() or None,
                            "s": ch_sec.strip() or None,
                            "act": 1 if ch_active else 0,
                        },
                    )
                else:
                    execute_sql(
                        """
                        UPDATE channel_credentials
                        SET api_url = COALESCE(NULLIF(:url, ''), api_url),
                            api_endpoint = COALESCE(NULLIF(:url, ''), api_endpoint),
                            api_key = COALESCE(NULLIF(:k, ''), api_key),
                            api_secret = COALESCE(NULLIF(:s, ''), api_secret),
                            is_active = :act
                        WHERE id = :id
                        """,
                        {
                            "url": ch_url.strip(),
                            "k": ch_key.strip(),
                            "s": ch_sec.strip(),
                            "act": 1 if ch_active else 0,
                            "id": int(existing.iloc[0]["id"]),
                        },
                    )
                load_all_db_configs.clear()
                log_audit(current_user, "CHANNEL_CRED_UPDATED", f"Updated channel {ch_name}", ch_url)
                st.success(f"Saved credentials for `{ch_name}`.")
                st.rerun()

    with s3:
        st.dataframe(run_query("SELECT * FROM system_settings ORDER BY setting_key"), use_container_width=True)
        with st.form("upsert_system_setting"):
            st.markdown("**Add / Update General System Setting (`system_settings`)**")
            ss1, ss2 = st.columns(2)
            s_key = ss1.text_input("Setting Key *", placeholder="e.g., SUPPLIER_EXCLUDE_KEYWORDS")
            s_desc = ss2.text_input("Description", placeholder="Optional note")
            s_val = st.text_input("Setting Value *")
            if st.form_submit_button("💾 Save Setting to `system_settings`") and s_key.strip():
                execute_sql(
                    """
                    INSERT INTO system_settings (setting_key, setting_value, description)
                    VALUES (:k, :v, :d)
                    ON DUPLICATE KEY UPDATE
                        setting_value = :v,
                        description = COALESCE(NULLIF(:d, ''), description)
                    """,
                    {"k": s_key.strip(), "v": s_val, "d": s_desc.strip()},
                )
                load_all_db_configs.clear()
                st.success(f"Saved `{s_key}` in `system_settings`.")
                st.rerun()

        with st.form("save_requirements"):
            cur_reqs = get_db_secret(
                "app_requirements",
                default="streamlit>=1.38.0\npandas>=2.2.0\nopenpyxl>=3.1.2\nsqlalchemy>=2.0.30\npymysql>=1.1.1\nrequests>=2.32.0\npillow>=10.4.0\nplotly>=5.22.0",
            )
            req_text = st.text_area("`app_requirements` in `system_settings`", value=cur_reqs, height=180)
            if st.form_submit_button("Save `app_requirements` to `system_settings`"):
                execute_sql(
                    """
                    INSERT INTO system_settings (setting_key, setting_value, description)
                    VALUES ('app_requirements', :v, 'Python pip requirements auto-verified on app startup')
                    ON DUPLICATE KEY UPDATE setting_value = :v
                    """,
                    {"v": req_text},
                )
                load_all_db_configs.clear()
                st.success("Saved `app_requirements` into `system_settings`.")
                st.rerun()

    with s4:
        st.dataframe(run_query("SELECT id, tenant_id, expires_at, updated_at FROM app_xero_tokens"), use_container_width=True)
