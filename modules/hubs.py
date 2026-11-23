from __future__ import annotations

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
    "⚡ Operations Action Center": "⚡ Operations Action Center",
    "📥 Supplier Excel Import & Exclusions": "🛒 Merchandising, Pricing & Sync",
    "🧭 Multi-Channel Wave Research": "🛒 Merchandising, Pricing & Sync",
    "🛒 Channel Profitability & Listings": "🛒 Merchandising, Pricing & Sync",
    "🔍 Warehouse Operations (GRN, Picks, Cycle Counts)": "📦 Warehouse Floor & Bin Ops",
    "🔫 Barcode Scan In / Out & Bins": "📦 Warehouse Floor & Bin Ops",
    "🏷️ Barcode & Shipping Label Printer": "🚚 Dispatch, Labels & Courier",
    "🚚 The Courier Guy (Waybills & Tracking)": "🚚 Dispatch, Labels & Courier",
    "📊 Executive & Financial Analytics": "💼 Finance & Executive Analytics",
    "💼 Xero Accounting Sync": "💼 Finance & Executive Analytics",
    "🔐 Users, Roles & Granular Permissions": "⚙️ System, Users & Credentials",
    "⚙️ DB Secrets, Credentials & Requirements": "⚙️ System, Users & Credentials",
}

HUB_DEFINITIONS = [
    {
        "label": "⚡ Operations Action Center",
        "members": [
            "⚡ Operations Action Center",
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
        "label": "💼 Finance & Executive Analytics",
        "members": [
            "📊 Executive & Financial Analytics",
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


def render_operations_home(auth_user: dict, permitted_modules: list[str]):
    action_center.render(auth_user)


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
            ("📊 Executive & Financial Analytics", "📊 Executive & Financial Analytics", analytics.render),
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
    "⚡ Operations Action Center": render_operations_home,
    "🛒 Merchandising, Pricing & Sync": render_merchandising_hub,
    "📦 Warehouse Floor & Bin Ops": render_warehouse_hub,
    "🚚 Dispatch, Labels & Courier": render_dispatch_hub,
    "💼 Finance & Executive Analytics": render_finance_hub,
    "⚙️ System, Users & Credentials": render_admin_hub,
}