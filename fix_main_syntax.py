#!/usr/bin/env python3
"""Fixes the unmatched ')' on line 178 in `main.py` and cleanly installs the 6-Hub sidebar."""

from pathlib import Path
import re
import shutil

ROOT = Path("/Users/gjvangreunen/workspace/takeazon_erp")
MAIN_PATH = ROOT / "main.py"

MAIN_HUB_NAV_BLOCK = '''from core.ui_helpers import inject_global_theme
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
)'''


def repair_main_py():
    bak = MAIN_PATH.with_suffix(".py.bak")
    txt = bak.read_text(encoding="utf-8") if bak.exists() else MAIN_PATH.read_text(encoding="utf-8")

    # Match from `from core.ui_helpers import inject_global_theme` all the way to the closing `)` of `page = st.sidebar.radio(...)`
    nav_pattern = re.compile(
        r'from core\.ui_helpers import inject_global_theme.*?page = st\.sidebar\.radio\(\n.*?\n\)',
        re.DOTALL,
    )
    if not nav_pattern.search(txt):
        # Fallback if single-line radio existed
        nav_pattern = re.compile(
            r'from core\.ui_helpers import inject_global_theme.*?page = st\.sidebar\.radio\([^\n]*\)',
            re.DOTALL,
        )

    updated = nav_pattern.sub(MAIN_HUB_NAV_BLOCK, txt, count=1)

    old_dispatch = "MODULE_ROUTES[page](auth_user)"
    new_dispatch = """if page in HUB_ROUTES:
    HUB_ROUTES[page](auth_user, permitted_modules)
else:
    MODULE_ROUTES[page](auth_user)"""

    if old_dispatch in updated and "if page in HUB_ROUTES:" not in updated:
        updated = updated.replace(old_dispatch, new_dispatch, 1)

    # Verify syntax before writing
    compile(updated, str(MAIN_PATH), "exec")
    MAIN_PATH.write_text(updated, encoding="utf-8")
    print("Verified syntax and repaired `main.py` successfully.")


if __name__ == "__main__":
    repair_main_py()