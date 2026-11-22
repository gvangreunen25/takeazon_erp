from datetime import datetime
import pandas as pd
import streamlit as st

from core.database import run_query
from services.ecomm_research_engine import (
    load_mysql_as_research_inputs,
    run_multichannel_research_pipeline,
)


def _current_sa_wave_banner() -> tuple[str, str]:
    """Returns the active South African retail wave title and focus window based on current month."""
    month = datetime.now().month
    if month == 1:
        return "W01 (Jan): Back-to-School & Term 1 Prep", "Focus: Stationery, lunch boxes, water bottles, backpacks & summer pool clearance"
    if month == 2:
        return "W02 (Feb): Valentine's Day & Romantic Gifting", "Focus: Plush, novelty gifts, romantic decor & autumn transition"
    if month in (3, 4):
        return "W03 (Mar-Apr): Easter & Autumn School Holidays", "Focus: Easter hunt baskets, crafts, travel games & early winter warmers"
    if month in (5, 6):
        return "W04 (May-Jun): Mother's/Father's Day & SA Winter", "Focus: Flasks, mugs, fleece blankets, torches & gifting"
    if month in (7, 8):
        return "W05 (Jul-Aug): Lighting/Power & Women's Month", "Focus: Solar/LED lanterns, emergency lighting, beauty & spa accessories"
    if month in (9, 10):
        return "W06 (Sep-Oct): Spring Clean, Braai & Halloween", "Focus: Braai tools, home storage/organisers, garden & Halloween costumes"
    if month == 11:
        return "W07–W08 (Nov): Black Friday & Early Festive Peak", "Focus: Tech/mobile accessories, kitchen gadgets, Xmas decor, wrap & kids toys"
    return "W08–W09 (Dec): Christmas Gifting & SA Summer Pool/Beach", "Focus: Intex/Bestway pools, inflatables, beach gear, toys & festive wrap"


def _load_pending_sellerflex() -> pd.DataFrame:
    """Safely inspects `sellerflex_inventory` columns and loads queued / active Seller Flex rows."""
    try:
        cols_df = run_query("SHOW COLUMNS FROM sellerflex_inventory")
        if cols_df.empty:
            return pd.DataFrame(columns=["SKU", "Description", "Queued_Qty", "Amazon_Price", "Decision"])

        cols = cols_df["Field"].astype(str).tolist()
        sku_col = next((c for c in cols if c.lower() in ("sku", "seller_sku", "item_sku", "item")), None)
        qty_col = next(
            (c for c in cols if c.lower() in ("available_qty", "quantity", "qty", "stock_qty", "available_quantity", "stock")),
            None,
        )
        status_col = next(
            (c for c in cols if c.lower() in ("sync_status", "status", "state")),
            None,
        )

        if not sku_col:
            return pd.DataFrame(columns=["SKU", "Description", "Queued_Qty", "Amazon_Price", "Decision"])

        qty_expr = f"COALESCE(s.`{qty_col}`, 0)" if qty_col else "0"
        where_clause = f"WHERE UPPER(COALESCE(s.`{status_col}`, '')) IN ('PENDING', 'QUEUED', 'NEW', 'READY')" if status_col else ""

        return run_query(f"""
            SELECT
                s.`{sku_col}` AS SKU,
                COALESCE(i.description, s.`{sku_col}`) AS Description,
                {qty_expr} AS Queued_Qty,
                COALESCE(NULLIF(p.amazon_actual_price, 0), NULLIF(p.amazon_rec_price, 0), 0) AS Amazon_Price,
                COALESCE(p.amazon_decision, 'UNPRICED') AS Decision
            FROM sellerflex_inventory s
            LEFT JOIN inventory_items i ON TRIM(i.sku) = TRIM(s.`{sku_col}`)
            LEFT JOIN inventory_item_profitability p ON TRIM(p.sku) = TRIM(s.`{sku_col}`)
            {where_clause}
            ORDER BY s.`{sku_col}` ASC
            LIMIT 500
        """)
    except Exception:
        return pd.DataFrame(columns=["SKU", "Description", "Queued_Qty", "Amazon_Price", "Decision"])


def render(auth_user: dict):
    st.title("⚡ Operations Action Center")
    wave_title, wave_focus = _current_sa_wave_banner()
    st.info(f"**Active Retail Window:** `{wave_title}` — {wave_focus}")

    low_stock_df = run_query("""
        SELECT
            a.sku AS SKU,
            COALESCE(NULLIF(a.item_name, ''), i.description, a.sku) AS Product,
            COALESCE(a.quantity, 0) AS Amazon_Qty,
            COALESCE(i.quantity, 0) AS Warehouse_Qty,
            COALESCE(a.current_price, 0) AS Current_Price
        FROM amazon_active_listings a
        LEFT JOIN inventory_items i ON TRIM(i.sku) = TRIM(a.sku)
        WHERE COALESCE(i.quantity, a.quantity, 0) <= 100
        ORDER BY COALESCE(i.quantity, a.quantity, 0) ASC, a.sku ASC
        LIMIT 250
    """)

    pending_sf_df = _load_pending_sellerflex()

    prof_counts_df = run_query("""
        SELECT
            SUM(CASE WHEN amazon_decision = 'LIST' THEN 1 ELSE 0 END) AS amz_list,
            SUM(CASE WHEN takealot_decision = 'LIST' THEN 1 ELSE 0 END) AS tal_list,
            SUM(CASE WHEN takeazon_decision = 'LIST' THEN 1 ELSE 0 END) AS tkz_list,
            COUNT(*) AS total_priced
        FROM inventory_item_profitability
    """)

    amz_ready = int(prof_counts_df.iloc[0]["amz_list"] or 0) if not prof_counts_df.empty else 0
    tal_ready = int(prof_counts_df.iloc[0]["tal_list"] or 0) if not prof_counts_df.empty else 0
    total_priced = int(prof_counts_df.iloc[0]["total_priced"] or 0) if not prof_counts_df.empty else 0

    # Top KPI Strip
    k1, k2, k3, k4 = st.columns(4)
    k1.metric("🚨 Low-Stock Listing Alerts", f"{len(low_stock_df):,}", "≤ 100 units remaining", delta_color="inverse")
    k2.metric("🚚 Seller Flex Queue", f"{len(pending_sf_df):,}", "Rows in sellerflex_inventory")
    k3.metric("✅ Amazon LIST-Ready SKUs", f"{amz_ready:,}", f"Out of {total_priced:,} priced SKUs")
    k4.metric("🛍️ Takealot LIST-Ready SKUs", f"{tal_ready:,}", "Passing margin & ROI rules")

    st.divider()

    # Quick Navigation Shortcuts
    q1, q2, q3 = st.columns(3)
    if q1.button("🧭 Open Multi-Channel Wave Research", use_container_width=True, type="primary"):
        st.session_state["nav_override_page"] = "🧭 Multi-Channel Wave Research"
        st.rerun()
    if q2.button("🛒 Open Profitability & Seller Flex Sync", use_container_width=True):
        st.session_state["nav_override_page"] = "🛒 Channel Profitability & Listings"
        st.rerun()
    if q3.button("🔄 Refresh Live Operational Snapshot", use_container_width=True):
        st.rerun()

    st.divider()

    # 3-Column Action Workbench
    col_alert, col_sf, col_wave = st.columns(3)

    with col_alert:
        st.subheader("🚨 Low-Stock Alerts")
        st.caption("Active Amazon listings at or below 100 warehouse units.")
        if low_stock_df.empty:
            st.success("All active listings are above the 100-unit buffer.")
        else:
            st.dataframe(low_stock_df, use_container_width=True, height=380, hide_index=True)

    with col_sf:
        st.subheader("🚚 Seller Flex Queue")
        st.caption("Staged SKUs awaiting batch inventory & price push.")
        if pending_sf_df.empty:
            st.info("No pending items in `sellerflex_inventory`. Use **Wave Research** to shortlist & push new SKUs.")
        else:
            unpriced_cnt = int((pd.to_numeric(pending_sf_df["Amazon_Price"], errors="coerce").fillna(0) <= 0).sum())
            if unpriced_cnt > 0:
                st.warning(f"**{unpriced_cnt}** queued SKU(s) still have R0.00 price — run Profitability first.")
            st.dataframe(pending_sf_df, use_container_width=True, height=380, hide_index=True)

    with col_wave:
        st.subheader("🌊 Top Seasonal Opportunities")
        st.caption("High-stock (>250 units) un-listed SKUs in priority waves.")
        if st.button("⚡ Scan Top Wave Opportunities Now", use_container_width=True):
            with st.spinner("Scanning live catalog for top seasonal wave candidates..."):
                s1, s2 = load_mysql_as_research_inputs()
                res = run_multichannel_research_pipeline(
                    df1_in=s1,
                    df2_in=s2,
                    min_stock_threshold=250,
                    amazon_flex_target_count=100,
                    discount_pct=0.20,
                    exclude_consumables=True,
                )
                st.session_state["action_center_wave_opps"] = res["amazon_flex_import"][
                    ["SKU", "Product Name", "Qty", "Amazon Price", "SA_Annual_Wave (Reference)"]
                ].head(50)

        wave_opps = st.session_state.get("action_center_wave_opps")
        if wave_opps is not None and not wave_opps.empty:
            st.dataframe(wave_opps, use_container_width=True, height=330, hide_index=True)
        else:
            st.info("Click **Scan Top Wave Opportunities Now** to surface high-depth seasonal SKUs not yet listed on Amazon.")