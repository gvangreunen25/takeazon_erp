#!/usr/bin/env python3
"""Consolidates the 12 ERP screens into 6 unified operational hubs with single-click navigation."""

from pathlib import Path
import re
import shutil

ROOT = Path("/Users/gjvangreunen/workspace/takeazon_erp")
HUBS_PATH = ROOT / "modules" / "hubs.py"
MAIN_PATH = ROOT / "main.py"

HUBS_CODE = '''from __future__ import annotations

import streamlit as st

from modules import (
    action_center,
    analytics,
    courier,
    ecomm_research,
    label_printer,
    profitability,
    scanner,
    settings,
    supplier_import,
    users_rbac,
    warehouse_ops,
    xero,
)

# Maps legacy single-screen labels to their consolidated Hub label
LEGACY_TO_HUB = {
    "⚡ Operations Action Center": "⚡ Command & Analytics",
    "📊 Executive & Financial Analytics": "⚡ Command & Analytics",
    "📥 Supplier Excel Import & Exclusions": "🛒 Merchandising, Pricing & Sync",
    "🧭 Multi-Channel Wave Research": "🛒 Merchandising, Pricing & Sync",
    "🛒 Channel Profitability & Listings": "🛒 Merchandising, Pricing & Sync",
    "🔍 Warehouse Operations (GRN, Picks, Cycle Counts)": "📦 Warehouse Floor & Bin Ops",
    "🔫 Barcode Scan In / Out & Bins": "📦 Warehouse Floor & Bin Ops",
    "🏷️ Barcode & Shipping Label Printer": "🚚 Dispatch, Labels & Courier",
    "🚚 The Courier Guy (Waybills & Tracking)": "🚚 Dispatch, Labels & Courier",
    "💼 Xero Accounting Sync": "💼 Finance & Xero Sync",
    "🔐 Users, Roles & Granular Permissions": "⚙️ System, Users & Credentials",
    "⚙️ DB Secrets, Credentials & Requirements": "⚙️ System, Users & Credentials",
}

HUB_DEFINITIONS = [
    {
        "label": "⚡ Command & Analytics",
        "members": [
            "⚡ Operations Action Center",
            "📊 Executive & Financial Analytics",
        ],
    },
    {
        "label": "🛒 Merchandising, Pricing & Sync",
        "members": [
            "🧭 Multi-Channel Wave Research",
            "🛒 Channel Profitability & Listings",
            "📥 Supplier Excel Import & Exclusions",
        ],
    },
    {
        "label": "📦 Warehouse Floor & Bin Ops",
        "members": [
            "🔍 Warehouse Operations (GRN, Picks, Cycle Counts)",
            "🔫 Barcode Scan In / Out & Bins",
        ],
    },
    {
        "label": "🚚 Dispatch, Labels & Courier",
        "members": [
            "🚚 The Courier Guy (Waybills & Tracking)",
            "🏷️ Barcode & Shipping Label Printer",
        ],
    },
    {
        "label": "💼 Finance & Xero Sync",
        "members": [
            "💼 Xero Accounting Sync",
        ],
    },
    {
        "label": "⚙️ System, Users & Credentials",
        "members": [
            "🔐 Users, Roles & Granular Permissions",
            "⚙️ DB Secrets, Credentials & Requirements",
        ],
    },
]


def _render_sub_views(auth_user: dict, permitted_modules: list[str], items: list[tuple[str, str, callable]]):
    """Renders permitted sub-modules as top tabs (or directly if user only has 1 sub-module)."""
    allowed = [(tab_title, fn) for mod_key, tab_title, fn in items if mod_key in permitted_modules]
    if not allowed:
        st.warning("You do not have permissions for any tabs in this hub.")
        return
    if len(allowed) == 1:
        allowed[0][1](auth_user)
        return
    tabs = st.tabs([t[0] for t in allowed])
    for tab, (_, render_fn) in zip(tabs, allowed):
        with tab:
            render_fn(auth_user)


def render_command_hub(auth_user: dict, permitted_modules: list[str]):
    _render_sub_views(
        auth_user,
        permitted_modules,
        [
            ("⚡ Operations Action Center", "⚡ Live Action Center", action_center.render),
            ("📊 Executive & Financial Analytics", "📊 Executive & Financial Analytics", analytics.render),
        ],
    )


def render_merchandising_hub(auth_user: dict, permitted_modules: list[str]):
    _render_sub_views(
        auth_user,
        permitted_modules,
        [
            ("🧭 Multi-Channel Wave Research", "🧭 10-Wave Research & Shortlist", ecomm_research.render),
            ("🛒 Channel Profitability & Listings", "🛒 Pricing, Profitability & Sync", profitability.render),
            ("📥 Supplier Excel Import & Exclusions", "📥 Supplier Excel Import", supplier_import.render),
        ],
    )


def render_warehouse_hub(auth_user: dict, permitted_modules: list[str]):
    _render_sub_views(
        auth_user,
        permitted_modules,
        [
            ("🔍 Warehouse Operations (GRN, Picks, Cycle Counts)", "📋 GRN, Picks & Cycle Counts", warehouse_ops.render),
            ("🔫 Barcode Scan In / Out & Bins", "🔫 Barcode Scan In / Out & Bins", scanner.render),
        ],
    )


def render_dispatch_hub(auth_user: dict, permitted_modules: list[str]):
    _render_sub_views(
        auth_user,
        permitted_modules,
        [
            ("🚚 The Courier Guy (Waybills & Tracking)", "🚚 Courier Waybills & Tracking", courier.render),
            ("🏷️ Barcode & Shipping Label Printer", "🏷️ Barcode & Shipping Labels", label_printer.render),
        ],
    )


def render_finance_hub(auth_user: dict, permitted_modules: list[str]):
    _render_sub_views(
        auth_user,
        permitted_modules,
        [
            ("💼 Xero Accounting Sync", "💼 Xero Accounting Sync", xero.render),
        ],
    )


def render_admin_hub(auth_user: dict, permitted_modules: list[str]):
    _render_sub_views(
        auth_user,
        permitted_modules,
        [
            ("🔐 Users, Roles & Granular Permissions", "🔐 Users & Roles", users_rbac.render),
            ("⚙️ DB Secrets, Credentials & Requirements", "⚙️ Secrets & Settings", settings.render),
        ],
    )


HUB_ROUTES = {
    "⚡ Command & Analytics": render_command_hub,
    "🛒 Merchandising, Pricing & Sync": render_merchandising_hub,
    "📦 Warehouse Floor & Bin Ops": render_warehouse_hub,
    "🚚 Dispatch, Labels & Courier": render_dispatch_hub,
    "💼 Finance & Xero Sync": render_finance_hub,
    "⚙️ System, Users & Credentials": render_admin_hub,
}
'''

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
)
'''


def apply_6_hubs():
    # 1. Write modules/hubs.py
    HUBS_PATH.write_text(HUBS_CODE, encoding="utf-8")
    print(f"Created `{HUBS_PATH}`.")

    # 2. Update main.py navigation + dispatch
    txt = MAIN_PATH.read_text(encoding="utf-8")
    updated = txt

    nav_pattern = re.compile(
        r'from core\.ui_helpers import inject_global_theme.*?page = st\.sidebar\.radio\(\s*"Module".*?\)',
        re.DOTALL,
    )
    if not nav_pattern.search(updated):
        print("[WARN] Could not locate existing navigation block in `main.py`.")
        return

    updated = nav_pattern.sub(MAIN_HUB_NAV_BLOCK, updated, count=1)

    # Update final page dispatch `MODULE_ROUTES[page](auth_user)` to support `HUB_ROUTES`
    old_dispatch = "MODULE_ROUTES[page](auth_user)"
    new_dispatch = """if page in HUB_ROUTES:
    HUB_ROUTES[page](auth_user, permitted_modules)
else:
    MODULE_ROUTES[page](auth_user)"""

    if old_dispatch in updated and "if page in HUB_ROUTES:" not in updated:
        updated = updated.replace(old_dispatch, new_dispatch, 1)

    shutil.copy2(MAIN_PATH, MAIN_PATH.with_suffix(".py.bak"))
    MAIN_PATH.write_text(updated, encoding="utf-8")
    print("Updated `main.py` to use the 6-Hub single-click sidebar.")


if __name__ == "__main__":
    apply_6_hubs()