import streamlit as st
from sqlalchemy import text

from core.auth import ALL_MODULES, authenticate_user, hash_password, load_user_permissions
from core.database import (
    bootstrap_app_startup,
    engine,
    get_cached_active_bins,
    get_cached_master_skus,
    log_audit,
    run_query,
)
from core.secrets import load_all_db_configs
from modules import (
    ecomm_research,
    action_center,
    analytics,
    courier,
    label_printer,
    profitability,
    scanner,
    settings,
    supplier_import,
    users_rbac,
    warehouse_ops,
    xero,
)

st.set_page_config(
    page_title="Takeazon WMS & Omni-Channel ERP",
    page_icon="📦",
    layout="wide",
    initial_sidebar_state="auto",
)

bootstrap_app_startup()

if "auth_user" not in st.session_state:
    st.session_state["auth_user"] = None

if st.session_state["auth_user"] is None:
    col_l, col_m, col_r = st.columns([1, 1.3, 1])
    with col_m:
        st.title("🔐 Takeazon WMS & ERP Login")
        st.caption("Sign in with your `app_users`, `system_users`, or `users` credentials.")

        with st.form("login_form"):
            login_id = st.text_input("Username or Email")
            login_pw = st.text_input("Password", type="password")
            login_btn = st.form_submit_button("Sign In", type="primary", use_container_width=True)

        if login_btn:
            user_obj = authenticate_user(login_id, login_pw)
            if user_obj:
                st.session_state["auth_user"] = user_obj
                log_audit(
                    user_obj["username"],
                    "USER_LOGIN",
                    "User Logged In",
                    f"Authenticated via {user_obj['source_table']} (Role: {user_obj['role_name']})",
                )
                st.rerun()
            else:
                st.error("Invalid username/email or password, or account is inactive.")

        total_users_df = run_query("""
            SELECT
                (SELECT COUNT(*) FROM app_users WHERE is_active = 1) +
                (SELECT COUNT(*) FROM system_users WHERE is_active = 1) +
                (SELECT COUNT(*) FROM users WHERE active = 1) AS cnt
        """)
        if int(total_users_df.iloc[0]["cnt"]) == 0:
            st.warning("No active users detected in the database. Create your initial Super Admin account below:")
            with st.form("bootstrap_admin_form"):
                b_user = st.text_input("Admin Username", value="admin")
                b_name = st.text_input("Full Name", value="System Administrator")
                b_email = st.text_input("Email", value="admin@takeazon.co.za")
                b_pass = st.text_input("Initial Password", type="password")
                if st.form_submit_button("Create Initial Super Admin") and b_user and b_pass:
                    pw_h = hash_password(b_pass)
                    with engine.begin() as conn:
                        conn.execute(
                            text("""
                                INSERT IGNORE INTO app_roles
                                    (role_id, role_name, allowed_tabs, can_save_profitability, can_live_sync, can_edit_table_settings)
                                VALUES
                                    ('admin', 'Super Admin', '*', 1, 1, 1)
                            """)
                        )
                        conn.execute(
                            text("""
                                INSERT INTO app_users
                                    (username, full_name, password_hash, role_id, is_active, email, role, status)
                                VALUES
                                    (:u, :fn, :ph, 'admin', 1, :em, 'Super Admin', 'Active')
                            """),
                            {"u": b_user.strip(), "fn": b_name.strip(), "ph": pw_h, "em": b_email.strip()},
                        )
                    st.success(f"Super Admin `{b_user}` created! Please sign in above.")
    st.stop()

auth_user = st.session_state["auth_user"]
current_user = auth_user["username"]

st.sidebar.title("📦 Takeazon WMS / ERP")
st.sidebar.markdown(
    f"**User:** `{auth_user['full_name']}`  \n"
    f"**Role:** `{auth_user['role_name']}` (`{auth_user['role_id']}`)"
)

c_sb1, c_sb2 = st.sidebar.columns(2)
if c_sb1.button("🔄 Refresh Perms"):
    refreshed = load_user_permissions(auth_user["username"], auth_user["role_id"], auth_user["role_name"])
    st.session_state["auth_user"].update(refreshed)
    load_all_db_configs.clear()
    get_cached_active_bins.clear()
    get_cached_master_skus.clear()
    st.rerun()

if c_sb2.button("🚪 Logout"):
    log_audit(current_user, "USER_LOGOUT", "User Logged Out", "Session terminated by user")
    st.session_state["auth_user"] = None
    st.rerun()

permitted_modules = [m for m in ALL_MODULES if m in auth_user["allowed_tabs"]]
if not permitted_modules:
    st.error(
        f"Your role (`{auth_user['role_name']}`) currently has **0 modules assigned** in `app_roles` or `role_permissions`. "
        "Ask an Administrator to grant module permissions."
    )
    st.stop()


from core.ui_helpers import inject_global_theme
from modules.hubs import HUB_DEFINITIONS, HUB_ROUTES, LEGACY_TO_HUB

inject_global_theme()

# Build the user's permitted 6-Hub list from their underlying module permissions
available_hubs = []
covered_modules = set()
for hub_def in HUB_DEFINITIONS:
    if any(m in permitted_modules for m in hub_def["members"]):
        available_hubs.append(hub_def["label"])
    covered_modules.update(hub_def["members"])

extra_modules = [m for m in permitted_modules if m not in covered_modules]
all_nav_options = available_hubs + extra_modules

# Reset navigation cleanly whenever the logged-in user session changes
current_session_sig = f"{auth_user.get('username')}:{auth_user.get('role_id')}"
if st.session_state.get("_nav_bound_user") != current_session_sig:
    st.session_state["_nav_bound_user"] = current_session_sig
    st.session_state["active_hub_page"] = all_nav_options[0]
    st.session_state.pop("nav_override_page", None)

# Handle one-click cross-module jumps (maps legacy screen names to their consolidated hub)
if "nav_override_page" in st.session_state:
    raw_target = st.session_state.pop("nav_override_page")
    mapped_target = LEGACY_TO_HUB.get(raw_target, raw_target)
    if mapped_target in all_nav_options:
        st.session_state["active_hub_page"] = mapped_target

if st.session_state.get("active_hub_page") not in all_nav_options:
    st.session_state["active_hub_page"] = all_nav_options[0]

st.sidebar.markdown("### 🧭 Navigation")
page = st.sidebar.radio(
    "Navigation",
    options=all_nav_options,
    index=all_nav_options.index(st.session_state["active_hub_page"]),
    key="active_hub_page",
    label_visibility="collapsed",
)



with st.sidebar.expander("🔑 My Active Permissions"):
    st.write(auth_user["permissions"])
    st.json(auth_user["flags"])

MODULE_ROUTES = {
    "⚡ Operations Action Center": action_center.render,
    "📊 Executive & Financial Analytics": analytics.render,
    "🧭 Multi-Channel Wave Research": ecomm_research.render,
    "📥 Supplier Excel Import & Exclusions": supplier_import.render,
    "🔫 Barcode Scan In / Out & Bins": scanner.render,
    "🏷️ Barcode & Shipping Label Printer": label_printer.render,
    "🚚 The Courier Guy (Waybills & Tracking)": courier.render,
    "💼 Xero Accounting Sync": xero.render,
    "🛒 Channel Profitability & Listings": profitability.render,
    "🔍 Warehouse Operations (GRN, Picks, Cycle Counts)": warehouse_ops.render,
    "⚙️ DB Secrets, Credentials & Requirements": settings.render,
    "🔐 Users, Roles & Granular Permissions": users_rbac.render,
}

if page in HUB_ROUTES:
    HUB_ROUTES[page](auth_user, permitted_modules)
else:
    MODULE_ROUTES[page](auth_user)
