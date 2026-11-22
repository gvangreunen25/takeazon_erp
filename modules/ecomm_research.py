from datetime import datetime
import pandas as pd
import streamlit as st
from sqlalchemy import text

from core.auth import has_perm
from core.database import engine, get_excel_sheet_names, load_uploaded_spreadsheet, log_audit
from services.ecomm_research_engine import (
    build_research_excel_bytes,
    load_mysql_as_research_inputs,
    run_multichannel_research_pipeline,
)


def render(auth_user: dict):
    current_user = auth_user["username"]
    st.title("🧭 Multi-Channel Full-Year Research & 10-Wave Planner")
    st.caption(
        "Classifies inventory across all 10 South African retail waves (W01 Back-to-School → W10 Evergreen), "
        "filters restricted consumables/pet food, scores channel fit (Amazon, Takealot, Takeazon), "
        "prioritizes Seller Flex expansion candidates, and audits existing Seller1 stock levels."
    )

    data_source = st.radio(
        "Select Research Input Source",
        [
            "1. Live MySQL Database (`inventory_items` + `amazon_active_listings` + `inventory_item_profitability`)",
            "2. Upload Seller1 & Seller2 Spreadsheets (.xlsx / .csv)",
        ],
        horizontal=True,
    )

    df1_in = None
    df2_in = None

    if data_source.startswith("2."):
        up1, up2 = st.columns(2)
        with up1:
            f1 = st.file_uploader("Upload Seller1 File (Existing Amazon Listings)", type=["xlsx", "xls", "csv"], key="res_s1_file")
            if f1 is not None:
                fb1 = f1.getvalue()
                sh1 = "CSV" if f1.name.lower().endswith(".csv") else get_excel_sheet_names(fb1)[0]
                df1_in = load_uploaded_spreadsheet(fb1, f1.name, sh1)
                st.caption(f"Loaded `{f1.name}` ({len(df1_in):,} rows)")
        with up2:
            f2 = st.file_uploader("Upload Seller2 File (Supplier / Master Catalog)", type=["xlsx", "xls", "csv"], key="res_s2_file")
            if f2 is not None:
                fb2 = f2.getvalue()
                sh2 = "CSV" if f2.name.lower().endswith(".csv") else get_excel_sheet_names(fb2)[0]
                df2_in = load_uploaded_spreadsheet(fb2, f2.name, sh2)
                st.caption(f"Loaded `{f2.name}` ({len(df2_in):,} rows)")

    c1, c2, c3, c4 = st.columns(4)
    min_stock = c1.number_input("Min Eligible Stock Threshold", min_value=0, value=100, step=10)
    flex_target = c2.number_input("Amazon Flex Target SKU Count", min_value=10, max_value=5000, value=1000, step=50)
    disc_pct = c3.number_input("Promo Discount %", min_value=0.0, max_value=90.0, value=20.0, step=1.0) / 100.0
    excl_cons = c4.checkbox("Exclude Sweets, Candy & Pet Food", value=True)

    ready = data_source.startswith("1.") or (df1_in is not None and df2_in is not None)

    if st.button("🚀 Run Multi-Channel Full-Year Research", type="primary", disabled=not ready):
        try:
            with st.spinner("Running 10-wave classification, MySQL pricing lookup & stock audit..."):
                if data_source.startswith("1."):
                    df1_in, df2_in = load_mysql_as_research_inputs()
                results = run_multichannel_research_pipeline(
                    df1_in=df1_in,
                    df2_in=df2_in,
                    min_stock_threshold=int(min_stock),
                    amazon_flex_target_count=int(flex_target),
                    discount_pct=disc_pct,
                    exclude_consumables=excl_cons,
                )
                st.session_state["ecomm_research_results"] = results
                st.success("Multi-channel full-year research completed!")
        except Exception as exc:
            st.error(f"Research pipeline error: {exc}")

    if "ecomm_research_results" in st.session_state:
        res = st.session_state["ecomm_research_results"]
        waves_df: pd.DataFrame = res["annual_wave_summary"]
        flex_df: pd.DataFrame = res["amazon_flex_import"]
        master_df: pd.DataFrame = res["multichannel_master"]
        audit_df: pd.DataFrame = res["audit"]

        m1, m2, m3, m4 = st.columns(4)
        m1.metric(f"Eligible SKUs (>{min_stock} Stock)", f"{len(master_df):,}")
        m2.metric("Amazon Flex Candidates", f"{len(flex_df):,}")
        m3.metric("Excluded Consumables", f"{res['excluded_count']:,}")
        low_stock_alerts = int(audit_df["Stock_Status"].astype(str).str.startswith("ALERT").sum()) if not audit_df.empty else 0
        m4.metric("Seller1 Low-Stock Alerts", f"{low_stock_alerts:,}")

        excel_bytes = build_research_excel_bytes(res)
        today_str = datetime.now().strftime("%d-%m-%Y")
        st.download_button(
            "⬇️ Download Full 4-Sheet Research Workbook (`multichannel_full_year_research.xlsx`)",
            data=excel_bytes,
            file_name=f"multichannel_full_year_research_{today_str}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

        st.divider()
        t_waves, t_flex, t_master, t_audit = st.tabs([
            "🌊 SA Annual 10 Retail Waves",
            f"🚚 Amazon Seller Flex Import ({len(flex_df):,})",
            f"🌐 All Channels Master ({len(master_df):,})",
            f"🔍 Seller1 Stock Audit ({len(audit_df):,})",
        ])

        with t_waves:
            st.dataframe(waves_df, use_container_width=True)

        with t_flex:
            st.caption("Prioritizes immediate Q4/Q1 waves (W07, W08, W09, W01) and key brands first, excluding pack-only SKUs and capping quantity at 500.")
            st.dataframe(flex_df, use_container_width=True, height=450)

            can_push_sf = has_perm(auth_user, "sync_amazon_finances") or auth_user["flags"].get("can_live_sync", False)
            if not flex_df.empty and can_push_sf:
                if st.button("📥 Stage Selected Candidates into `sellerflex_inventory` Table"):
                    try:
                        with engine.begin() as conn:
                            rows_to_insert = [
                                {
                                    "sku": str(r["SKU"]).strip(),
                                    "qty": int(r["Qty"]),
                                }
                                for _, r in flex_df.iterrows()
                            ]
                            for i in range(0, len(rows_to_insert), 500):
                                conn.execute(
                                    text("""
                                        INSERT INTO sellerflex_inventory (sku, available_qty, sync_status)
                                        VALUES (:sku, :qty, 'PENDING')
                                        ON DUPLICATE KEY UPDATE available_qty = VALUES(available_qty), sync_status = 'PENDING'
                                    """),
                                    rows_to_insert[i : i + 500],
                                )
                        log_audit(
                            current_user,
                            "STAGE_SELLERFLEX_RESEARCH",
                            "Staged Seller Flex candidates from Research Planner",
                            f"Staged {len(flex_df):,} SKUs into sellerflex_inventory",
                            len(flex_df),
                        )
                        st.success(f"Staged **{len(flex_df):,}** SKUs into `sellerflex_inventory` (ready to sync in Channel Profitability & Listings → SellerFlex Sync).")
                    except Exception as exc:
                        st.error(f"Could not stage into `sellerflex_inventory`: {exc}")

        with t_master:
            if not master_df.empty:
                f_col1, f_col2 = st.columns(2)
                wave_opts = ["ALL"] + sorted(master_df["SA_Annual_Wave"].dropna().unique().tolist())
                sel_wave = f_col1.selectbox("Filter by SA Retail Wave", wave_opts)
                fit_opts = ["ALL", "Amazon Fit (Yes)", "Takealot Fit (Yes)", "Takeazon Fit (Yes)"]
                sel_fit = f_col2.selectbox("Filter by Channel Fit", fit_opts)

                filt_master = master_df.copy()
                if sel_wave != "ALL":
                    filt_master = filt_master[filt_master["SA_Annual_Wave"] == sel_wave]
                if sel_fit == "Amazon Fit (Yes)":
                    filt_master = filt_master[filt_master["Amazon_Fit"].astype(str).str.startswith("Yes")]
                elif sel_fit == "Takealot Fit (Yes)":
                    filt_master = filt_master[filt_master["Takealot_Fit"].astype(str).str.startswith("Yes")]
                elif sel_fit == "Takeazon Fit (Yes)":
                    filt_master = filt_master[filt_master["Takeazon_Fit"].astype(str).str.startswith("Yes")]

                st.dataframe(filt_master, use_container_width=True, height=480)
            else:
                st.info("No eligible SKUs matched the current stock threshold.")

        with t_audit:
            st.dataframe(audit_df, use_container_width=True, height=480)