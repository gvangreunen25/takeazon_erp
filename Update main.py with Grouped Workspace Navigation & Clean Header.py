#!/usr/bin/env python3
"""Upgrades `main.py` with categorized workspace navigation and cohesive UI styling."""

from pathlib import Path
import re
import shutil

MAIN_PATH = Path("/Users/gjvangreunen/workspace/takeazon_erp/main.py")

GROUPED_NAV_BLOCK = '''from core.ui_helpers import inject_global_theme

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

# Handle cross-module navigation jumps
if "nav_override_page" in st.session_state:
    target_p = st.session_state.pop("nav_override_page")
    if target_p in permitted_modules:
        st.session_state["active_module_page"] = target_p
        for grp_name, grp_items in WORKSPACE_GROUPS.items():
            if target_p in grp_items:
                st.session_state["active_workspace_group"] = grp_name
                break

# Build available workspace groups based on user's permitted modules
available_groups = {}
assigned_set = set()
for grp_name, grp_items in WORKSPACE_GROUPS.items():
    allowed_in_grp = [m for m in grp_items if m in permitted_modules]
    if allowed_in_grp:
        available_groups[grp_name] = allowed_in_grp
        assigned_set.update(allowed_in_grp)

# Catch any custom/unlisted modules safely
leftover = [m for m in permitted_modules if m not in assigned_set]
if leftover:
    available_groups["📌 Other Modules"] = leftover

group_names = list(available_groups.keys())
if st.session_state.get("active_workspace_group") not in group_names:
    st.session_state["active_workspace_group"] = group_names[0]

st.sidebar.markdown("### 🧭 Workspace")
selected_group = st.sidebar.selectbox(
    "Department / Workspace",
    group_names,
    key="active_workspace_group",
    label_visibility="collapsed",
)

group_modules = available_groups[selected_group]
if st.session_state.get("active_module_page") not in group_modules:
    st.session_state["active_module_page"] = group_modules[0]

page = st.sidebar.radio(
    "Module",
    group_modules,
    key="active_module_page",
    label_visibility="collapsed",
)
'''


def upgrade_main_nav():
    txt = MAIN_PATH.read_text(encoding="utf-8")
    if "WORKSPACE_GROUPS" in txt:
        print("`main.py` already has grouped workspace navigation.")
        return

    # Replace existing sidebar radio block (with or without previous CSS/nav patch)
    pattern = re.compile(
        r'(?:st\.markdown\(\s*"""\s*<style>.*?</style>\s*""",\s*unsafe_allow_html=True,\s*\)\s*)?'
        r'(?:if "nav_override_page" in st\.session_state:.*?st\.session_state\["sidebar_nav_radio"\] = target_p\s*)?'
        r'page = st\.sidebar\.radio\("Permitted Modules", permitted_modules(?:, key="sidebar_nav_radio")?\)',
        re.DOTALL,
    )
    if not pattern.search(txt):
        print("[WARN] Could not auto-match sidebar radio block in `main.py`.")
        return

    shutil.copy2(MAIN_PATH, MAIN_PATH.with_suffix(".py.bak"))
    updated = pattern.sub(GROUPED_NAV_BLOCK, txt, count=1)
    MAIN_PATH.write_text(updated, encoding="utf-8")
    print("Upgraded `main.py` with categorized workspace navigation and global UI theme.")


if __name__ == "__main__":
    upgrade_main_nav()