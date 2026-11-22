#!/usr/bin/env python3
"""Fixes workspace/module sync and clears stale navigation state on logout/login in `main.py`."""

from pathlib import Path
import re
import shutil

MAIN_PATH = Path("/Users/gjvangreunen/workspace/takeazon_erp/main.py")

CLEAN_NAV_BLOCK = '''from core.ui_helpers import inject_global_theme

inject_global_theme()

WORKSPACE_GROUPS = {
    "🏠 Command & Analytics": [
        "⚡ Operations Action Center",
        "📊 Executive & Financial Analytics",
    ],
    "🛒 Channels, Pricing & Research": [
        "🧭 Multi-Channel Wave Research",
        "🛒 Channel Profitability & Listings",
        "📥 Supplier Excel Import & Exclusions",
    ],
    "📦 Warehouse & Fulfilment": [
        "🔍 Warehouse Operations (GRN, Picks, Cycle Counts)",
        "🔫 Barcode Scan In / Out & Bins",
        "🏷️ Barcode & Shipping Label Printer",
        "🚚 The Courier Guy (Waybills & Tracking)",
    ],
    "⚙️ Finance & Admin": [
        "💼 Xero Accounting Sync",
        "🔐 Users, Roles & Granular Permissions",
        "⚙️ DB Secrets, Credentials & Requirements",
    ],
}

# Build available workspace groups strictly from the logged-in user's permitted modules
available_groups = {}
assigned_set = set()
for grp_name, grp_items in WORKSPACE_GROUPS.items():
    allowed_in_grp = [m for m in grp_items if m in permitted_modules]
    if allowed_in_grp:
        available_groups[grp_name] = allowed_in_grp
        assigned_set.update(allowed_in_grp)

leftover = [m for m in permitted_modules if m not in assigned_set]
if leftover:
    available_groups["📌 Other Modules"] = leftover

group_names = list(available_groups.keys())

# Reset navigation cleanly whenever the logged-in user session changes
current_session_sig = f"{auth_user.get('username')}:{auth_user.get('role_id')}"
if st.session_state.get("_nav_bound_user") != current_session_sig:
    st.session_state["_nav_bound_user"] = current_session_sig
    st.session_state["active_workspace_group"] = group_names[0]
    st.session_state["active_module_page"] = available_groups[group_names[0]][0]
    st.session_state.pop("nav_override_page", None)

# Handle intentional one-click cross-module navigation jumps
if "nav_override_page" in st.session_state:
    target_p = st.session_state.pop("nav_override_page")
    if target_p in permitted_modules:
        for grp_name, grp_items in available_groups.items():
            if target_p in grp_items:
                st.session_state["active_workspace_group"] = grp_name
                st.session_state["active_module_page"] = target_p
                break

if st.session_state.get("active_workspace_group") not in available_groups:
    st.session_state["active_workspace_group"] = group_names[0]


def _on_workspace_change():
    chosen_grp = st.session_state.get("active_workspace_group")
    if chosen_grp in available_groups and available_groups[chosen_grp]:
        st.session_state["active_module_page"] = available_groups[chosen_grp][0]


st.sidebar.markdown("### 🧭 Workspace")
selected_group = st.sidebar.selectbox(
    "Department / Workspace",
    options=group_names,
    index=group_names.index(st.session_state["active_workspace_group"]),
    key="active_workspace_group",
    on_change=_on_workspace_change,
    label_visibility="collapsed",
)

group_modules = available_groups[selected_group]
if st.session_state.get("active_module_page") not in group_modules:
    st.session_state["active_module_page"] = group_modules[0]

page = st.sidebar.radio(
    "Module",
    options=group_modules,
    index=group_modules.index(st.session_state["active_module_page"]),
    key="active_module_page",
    label_visibility="collapsed",
)
'''


def fix_nav_sync():
    txt = MAIN_PATH.read_text(encoding="utf-8")

    # 1. Also ensure logout button clears navigation keys if present
    logout_pattern = re.compile(r'(if\s+st\.sidebar\.button\([^\n]*[Ll]og\s*[Oo]ut[^\n]*\)\s*:\s*\n)([ \t]+)')
    m_out = logout_pattern.search(txt)
    if m_out and "_nav_bound_user" not in txt:
        header, indent = m_out.groups()
        clear_lines = (
            f'{indent}for _k in ("_nav_bound_user", "active_workspace_group", "active_module_page", "nav_override_page"):\n'
            f'{indent}    st.session_state.pop(_k, None)\n'
        )
        txt = txt[: m_out.start()] + header + clear_lines + txt[m_out.end() :]

    # 2. Replace the previous WORKSPACE_GROUPS block cleanly
    nav_pattern = re.compile(
        r'from core\.ui_helpers import inject_global_theme.*?page = st\.sidebar\.radio\(\s*"Module",\s*group_modules,\s*key="active_module_page",\s*label_visibility="collapsed",\s*\)',
        re.DOTALL,
    )
    if not nav_pattern.search(txt):
        print("[WARN] Could not locate existing WORKSPACE_GROUPS block in `main.py`.")
        return

    shutil.copy2(MAIN_PATH, MAIN_PATH.with_suffix(".py.bak"))
    updated = nav_pattern.sub(CLEAN_NAV_BLOCK, txt, count=1)
    MAIN_PATH.write_text(updated, encoding="utf-8")
    print("Fixed workspace/module synchronization and logout state reset in `main.py`.")


if __name__ == "__main__":
    fix_nav_sync()