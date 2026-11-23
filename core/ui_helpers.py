from __future__ import annotations

from typing import Dict, List, Optional, Tuple
import pandas as pd
import streamlit as st
import streamlit.components.v1 as components
from sqlalchemy import text

from core.database import engine, run_query


def inject_global_theme() -> None:
    """Applies cohesive modern ERP styling that automatically adapts to both Light and Dark Mode."""
    st.markdown(
        """
        <style>
        /* Layout & Container Rhythm (Desktop + Safe-Area Mobile) */
        .block-container {
            padding-top: calc(1.25rem + env(safe-area-inset-top, 0px));
            padding-bottom: calc(2.5rem + env(safe-area-inset-bottom, 0px));
            padding-left: max(1rem, env(safe-area-inset-left, 1rem));
            padding-right: max(1rem, env(safe-area-inset-right, 1rem));
            max-width: 1480px;
        }

        /* Theme-Adaptive KPI Metric Cards (Works in both Light & Dark Mode) */
        div[data-testid="stMetric"] {
            background-color: var(--secondary-background-color) !important;
            color: var(--text-color) !important;
            border: 1px solid rgba(148, 163, 184, 0.25) !important;
            border-radius: 12px !important;
            padding: 14px 16px !important;
            box-shadow: 0 1px 3px rgba(0, 0, 0, 0.08);
        }
        div[data-testid="stMetricLabel"] p,
        div[data-testid="stMetricLabel"] {
            color: var(--text-color) !important;
            opacity: 0.85;
            font-weight: 500 !important;
        }
        div[data-testid="stMetricValue"] {
            color: var(--text-color) !important;
            font-weight: 700 !important;
        }

        /* Theme-Adaptive Expanders (Fixes washed-out sidebar & page expanders in Dark Mode) */
        div[data-testid="stExpander"],
        details[data-testid="stExpander"] {
            background-color: var(--secondary-background-color) !important;
            color: var(--text-color) !important;
            border: 1px solid rgba(148, 163, 184, 0.25) !important;
            border-radius: 10px !important;
            overflow: hidden;
        }
        div[data-testid="stExpander"] summary,
        details[data-testid="stExpander"] summary,
        div[data-testid="stExpander"] summary span,
        div[data-testid="stExpander"] summary p {
            background-color: transparent !important;
            color: var(--text-color) !important;
        }
        div[data-testid="stExpander"] summary:hover,
        details[data-testid="stExpander"] summary:hover {
            background-color: rgba(148, 163, 184, 0.12) !important;
        }

        /* Horizontally Scrollable Touch-Friendly Tabs */
        .stTabs [data-baseweb="tab-list"] {
            gap: 6px;
            overflow-x: auto;
            overflow-y: hidden;
            -webkit-overflow-scrolling: touch;
            scrollbar-width: none;
            border-bottom: 1px solid rgba(148, 163, 184, 0.25);
            padding-bottom: 4px;
        }
        .stTabs [data-baseweb="tab-list"]::-webkit-scrollbar {
            display: none;
        }
        .stTabs [data-baseweb="tab"] {
            border-radius: 8px;
            padding: 8px 14px;
            font-weight: 500;
            white-space: nowrap;
            flex-shrink: 0;
        }

        /* Dataframe Touch Scrolling & Rounding */
        div[data-testid="stDataFrame"] {
            border-radius: 10px;
            overflow-x: auto;
            -webkit-overflow-scrolling: touch;
        }

        /* Sidebar Polish & Prevent Button Label Truncation */
        section[data-testid="stSidebar"] .block-container {
            padding-top: 1.1rem;
        }
        section[data-testid="stSidebar"] div[data-testid="stButton"] > button {
            white-space: nowrap;
            padding-left: 0.6rem;
            padding-right: 0.6rem;
            font-size: 0.88rem;
        }

        /* =========================================================
           MOBILE & TABLET RESPONSIVE OPTIMIZATIONS (<= 768px)
           ========================================================= */
        @media (max-width: 768px) {
            .block-container {
                padding-top: 0.85rem;
                padding-left: 0.75rem;
                padding-right: 0.75rem;
            }
            h1 {
                font-size: 1.45rem !important;
                line-height: 1.25 !important;
            }
            h2, h3 {
                font-size: 1.15rem !important;
            }
            /* Prevent iOS auto-zoom on input focus */
            input, textarea, select, div[data-baseweb="select"] * {
                font-size: 16px !important;
            }
            /* Touch-friendly 44px minimum tap targets */
            button[kind="primary"], button[kind="secondary"], div[data-testid="stButton"] > button,
            div[data-testid="stDownloadButton"] > button {
                min-height: 44px !important;
                border-radius: 10px !important;
                font-weight: 600 !important;
            }
            /* 2-column grid wrap for 4-card KPI strips on mobile screens */
            div[data-testid="stHorizontalBlock"] {
                flex-wrap: wrap !important;
                gap: 0.5rem !important;
            }
            div[data-testid="stHorizontalBlock"] > div[data-testid="column"] {
                min-width: calc(50% - 0.5rem) !important;
                flex: 1 1 calc(50% - 0.5rem) !important;
            }
            div[data-testid="stMetric"] {
                padding: 10px 12px !important;
            }
            div[data-testid="stMetricValue"] {
                font-size: 1.25rem !important;
            }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )

    # Inject PWA manifest, iOS/Android standalone meta tags, and Service Worker into parent document <head>
    components.html(
        """
        <script>
        (function() {
            const doc = window.parent.document;
            const head = doc.head;
            if (!head) return;

            function ensureTag(selector, tagName, attrs) {
                let el = head.querySelector(selector);
                if (!el) {
                    el = doc.createElement(tagName);
                    head.appendChild(el);
                }
                for (const [k, v] of Object.entries(attrs)) {
                    el.setAttribute(k, v);
                }
            }

            ensureTag('meta[name="viewport"]', 'meta', {
                name: 'viewport',
                content: 'width=device-width, initial-scale=1.0, maximum-scale=5.0, viewport-fit=cover'
            });
            ensureTag('link[rel="manifest"]', 'link', {
                rel: 'manifest',
                href: '/app/static/manifest.json'
            });
            ensureTag('meta[name="theme-color"]', 'meta', {
                name: 'theme-color',
                content: '#0f172a'
            });
            ensureTag('meta[name="apple-mobile-web-app-capable"]', 'meta', {
                name: 'apple-mobile-web-app-capable',
                content: 'yes'
            });
            ensureTag('meta[name="apple-mobile-web-app-status-bar-style"]', 'meta', {
                name: 'apple-mobile-web-app-status-bar-style',
                content: 'black-translucent'
            });
            ensureTag('meta[name="apple-mobile-web-app-title"]', 'meta', {
                name: 'apple-mobile-web-app-title',
                content: 'Takeazon ERP'
            });
            ensureTag('link[rel="apple-touch-icon"]', 'link', {
                rel: 'apple-touch-icon',
                href: '/app/static/icon-192.png'
            });

            if ('serviceWorker' in window.parent.navigator && !window.parent.__takeazonSwRegistered) {
                window.parent.__takeazonSwRegistered = true;
                window.parent.navigator.serviceWorker.register('/app/static/sw.js').catch(() => {});
            }
        })();
        </script>
        """,
        height=0,
        width=0,
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