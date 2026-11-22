from __future__ import annotations

from typing import Dict, List, Optional, Tuple
import pandas as pd
import streamlit as st
from sqlalchemy import text

from core.database import engine, run_query


def inject_global_theme() -> None:
    """Applies cohesive modern ERP styling across all Streamlit modules."""
    st.markdown(
        """
        <style>
        /* Layout & Container Rhythm */
        .block-container {
            padding-top: 1.35rem;
            padding-bottom: 2.25rem;
            max-width: 1480px;
        }
        /* KPI Metric Cards */
        div[data-testid="stMetric"] {
            background: linear-gradient(180deg, rgba(248, 250, 252, 0.9) 0%, rgba(241, 245, 249, 0.7) 100%);
            border: 1px solid rgba(148, 163, 184, 0.28);
            border-radius: 10px;
            padding: 12px 16px;
            box-shadow: 0 1px 2px rgba(15, 23, 42, 0.04);
        }
        /* Clean Expanders & Containers */
        div[data-testid="stExpander"] {
            border-radius: 10px;
            border: 1px solid rgba(148, 163, 184, 0.25);
            background-color: rgba(255, 255, 255, 0.6);
        }
        /* Pill-like Tabs */
        .stTabs [data-baseweb="tab-list"] {
            gap: 6px;
            border-bottom: 1px solid rgba(148, 163, 184, 0.25);
            padding-bottom: 4px;
        }
        .stTabs [data-baseweb="tab"] {
            border-radius: 8px;
            padding: 7px 14px;
            font-weight: 500;
        }
        /* Dataframe Rounding */
        div[data-testid="stDataFrame"] {
            border-radius: 8px;
            overflow: hidden;
        }
        /* Sidebar Polish */
        section[data-testid="stSidebar"] .block-container {
            padding-top: 1.1rem;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def render_page_header(title: str, subtitle: str, badge: Optional[str] = None) -> None:
    """Renders a consistent, scannable header across every module."""
    if badge:
        st.caption(f"`{badge}`")
    st.title(title)
    st.caption(subtitle)


def jump_to_module(module_label: str) -> None:
    """Triggers a clean one-click navigation jump to another permitted module."""
    st.session_state["nav_override_page"] = module_label
    st.rerun()


def filter_dataframe_by_search(df: pd.DataFrame, query: str, columns: Optional[List[str]] = None) -> pd.DataFrame:
    """Case-insensitive substring search across specified or all string-like columns."""
    if df.empty or not query or not query.strip():
        return df
    q = query.strip().lower()
    target_cols = [c for c in (columns or df.columns.tolist()) if c in df.columns]
    if not target_cols:
        return df
    mask = pd.Series(False, index=df.index)
    for col in target_cols:
        mask = mask | df[col].astype(str).str.lower().str.contains(q, na=False)
    return df[mask]


def get_sellerflex_schema() -> Dict[str, Optional[str]]:
    """Introspects `sellerflex_inventory` columns once so all modules read/write safely."""
    cols_df = run_query("SHOW COLUMNS FROM sellerflex_inventory")
    cols = cols_df["Field"].astype(str).tolist() if not cols_df.empty else []
    sku_col = next((c for c in cols if c.lower() in ("sku", "seller_sku", "item_sku", "item")), None)
    qty_col = next(
        (c for c in cols if c.lower() in ("available_qty", "quantity", "qty", "stock_qty", "available_quantity", "stock")),
        None,
    )
    status_col = next((c for c in cols if c.lower() in ("sync_status", "status", "state")), None)
    return {"sku_col": sku_col, "qty_col": qty_col, "status_col": status_col}


def stage_items_to_sellerflex(items: List[Tuple[str, int]]) -> int:
    """Schema-safe upsert of `(sku, qty)` pairs into `sellerflex_inventory`."""
    if not items:
        return 0
    schema = get_sellerflex_schema()
    sku_col = schema["sku_col"] or "sku"
    qty_col = schema["qty_col"]
    status_col = schema["status_col"]

    cols = [f"`{sku_col}`"]
    vals = [":sku"]
    updates = []

    if qty_col:
        cols.append(f"`{qty_col}`")
        vals.append(":qty")
        updates.append(f"`{qty_col}` = VALUES(`{qty_col}`)")
    if status_col:
        cols.append(f"`{status_col}`")
        vals.append("'PENDING'")
        updates.append(f"`{status_col}` = 'PENDING'")

    if not updates:
        sql = f"INSERT IGNORE INTO sellerflex_inventory ({', '.join(cols)}) VALUES ({', '.join(vals)})"
    else:
        sql = (
            f"INSERT INTO sellerflex_inventory ({', '.join(cols)}) VALUES ({', '.join(vals)}) "
            f"ON DUPLICATE KEY UPDATE {', '.join(updates)}"
        )

    payload = [{"sku": str(s).strip(), "qty": max(0, int(q))} for s, q in items if str(s).strip()]
    with engine.begin() as conn:
        for i in range(0, len(payload), 500):
            conn.execute(text(sql), payload[i : i + 500])
    return len(payload)