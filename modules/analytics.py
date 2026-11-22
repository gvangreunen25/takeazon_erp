from datetime import datetime, timedelta, timezone

import plotly.express as px
import streamlit as st

from core.auth import has_perm
from core.database import run_query
from services.amazon_sp import clear_amazon_financial_data, sync_amazon_financial_transactions


def render(auth_user: dict):
    current_user = auth_user["username"]
    st.title("📊 Multi-Channel Financial & Inventory Analytics")

    can_sync_amz = has_perm(auth_user, "sync_amazon_finances") or auth_user["flags"].get("can_live_sync", False)
    with st.expander("🟠 Pull Live Financial Data from Amazon.co.za (`AMAZON_ZA` SP-API Finances v0)", expanded=False):
        if not can_sync_amz:
            st.warning("You need `sync_amazon_finances` permission (or `can_live_sync`) to pull live Amazon.co.za financial data.")
        else:
            ac1, ac2, ac3 = st.columns([1, 1, 1.3])
            start_d = ac1.date_input("Posted After (UTC)", value=datetime.utcnow().date() - timedelta(days=365))
            end_d = ac2.date_input("Posted Before (UTC)", value=datetime.utcnow().date())

            opt_c1, opt_c2 = st.columns(2)
            clear_first = opt_c1.checkbox(
                "🗑️ Clear previously synced Amazon financial rows before pulling (recommended when re-syncing)",
                value=True,
            )
            deduct_stock = opt_c2.checkbox(
                "📉 Deduct newly sold Amazon quantities from `inventory_items` (idempotent per order)",
                value=True,
            )

            ac3.markdown("<br>", unsafe_allow_html=True)
            if ac3.button("🔄 Pull Amazon.co.za Financial Events", type="primary", use_container_width=True):
                now_utc = datetime.now(timezone.utc) - timedelta(minutes=3)
                after_dt = datetime(start_d.year, start_d.month, start_d.day, 0, 0, 0, tzinfo=timezone.utc)
                before_dt = min(
                    datetime(end_d.year, end_d.month, end_d.day, 23, 59, 59, tzinfo=timezone.utc),
                    now_utc,
                )
                try:
                    with st.spinner("Fetching Amazon.co.za (ZAR) financial events from SP-API..."):
                        if clear_first:
                            clear_amazon_financial_data(triggered_by=current_user)
                        summary = sync_amazon_financial_transactions(
                            posted_after_iso=after_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
                            posted_before_iso=before_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
                            triggered_by=current_user,
                            deduct_inventory=deduct_stock,
                        )
                    st.success(
                        f"Amazon.co.za (`AMAZON_ZA`) Financial Sync Complete — "
                        f"Raw events fetched: **{summary['transactions_fetched']}**, "
                        f"ZAR line items inserted: **{summary['events_inserted']}**, "
                        f"Orders rolled up: **{summary['orders_rolled_up']}**, "
                        f"Inventory deducted: **{summary['units_deducted']}** unit(s) across **{summary['skus_deducted']}** SKU(s)."
                    )
                except Exception as exc:
                    st.error(f"Amazon Financial Sync Failed: {exc}")

    ch_options_df = run_query("SELECT DISTINCT UPPER(channel) AS ch FROM financial_ledger ORDER BY ch")
    ch_choices = ["AMAZON_ZA"] + [c for c in (ch_options_df["ch"].tolist() if not ch_options_df.empty else []) if c != "AMAZON_ZA"]
    if len(ch_choices) > 1:
        ch_choices = ["ALL CHANNELS"] + ch_choices
    selected_ch_filter = st.selectbox("Filter Financial Metrics by Channel", ch_choices, index=0)

    where_ch = "" if selected_ch_filter == "ALL CHANNELS" else "WHERE UPPER(channel) = :ch"
    query_params = {} if selected_ch_filter == "ALL CHANNELS" else {"ch": selected_ch_filter}

    fin_summary = run_query(
        f"""
        SELECT
            COALESCE(SUM(gross_sales), 0) AS gross_incl_vat,
            COALESCE(SUM(channel_fees), 0) AS fees_and_refunds,
            COALESCE(SUM(cogs), 0) AS cogs,
            COALESCE(SUM(net_profit), 0) AS net
        FROM financial_ledger
        {where_ch}
        """,
        query_params,
    )

    amz_vat_df = run_query("""
        SELECT
            COALESCE(SUM(CASE WHEN transaction_type = 'Shipment' AND charge_type = 'Principal' THEN amount ELSE 0 END), 0) AS shipped_excl_vat,
            COALESCE(SUM(CASE WHEN transaction_type = 'Shipment' AND charge_type = 'Tax' THEN amount ELSE 0 END), 0) AS vat_collected,
            COALESCE(SUM(CASE WHEN transaction_type = 'Refund' AND charge_type = 'Refund_Principal' THEN ABS(amount) ELSE 0 END), 0) AS refunds_excl_vat
        FROM amazon_financial_events
    """)

    gross_incl_vat = float(fin_summary.iloc[0]["gross_incl_vat"])
    net_profit_val = float(fin_summary.iloc[0]["net"])

    if selected_ch_filter in ("AMAZON_ZA", "ALL CHANNELS") and not amz_vat_df.empty:
        shipped_excl = float(amz_vat_df.iloc[0]["shipped_excl_vat"])
        vat_collected = float(amz_vat_df.iloc[0]["vat_collected"])
        refunds_excl = float(amz_vat_df.iloc[0]["refunds_excl_vat"])
        net_sales_excl_vat = shipped_excl - refunds_excl
    else:
        vat_collected = gross_incl_vat - (gross_incl_vat / 1.15)
        net_sales_excl_vat = gross_incl_vat / 1.15

    margin = (net_profit_val / net_sales_excl_vat * 100) if net_sales_excl_vat > 0 else 0.0

    inv_val = run_query("""
        SELECT COALESCE(SUM(quantity * cost_price), 0) AS stock_val,
               COALESCE(SUM(quantity), 0) AS total_units
        FROM inventory_items
    """)

    col1, col2, col3, col4, col5 = st.columns(5)
    col1.metric("Gross Sales (Incl. VAT)", f"R {gross_incl_vat:,.2f}")
    col2.metric("Net Sales (Excl. VAT — Xero)", f"R {net_sales_excl_vat:,.2f}", f"-R {vat_collected:,.2f} VAT", delta_color="off")
    col3.metric("Net Profit (Excl. VAT)", f"R {net_profit_val:,.2f}", f"{margin:.1f}% Margin")
    col4.metric("Stock Valuation (Cost)", f"R {float(inv_val.iloc[0]['stock_val']):,.2f}")
    col5.metric("Total Units in Master", f"{int(inv_val.iloc[0]['total_units']):,}")

    tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs([
        "Channel P&L Trend",
        "Amazon.co.za Financial Events",
        "SKU Profitability Breakdown",
        "Amazon Stock Deductions Log",
        "Regional Stock (CT / DBN / JHB)",
        "Payout Settlements",
    ])

    with tab1:
        df_ledger = run_query(
            f"""
            SELECT DATE(transaction_date) AS tx_date, UPPER(channel) AS channel,
                   SUM(gross_sales) AS gross_sales_incl_vat,
                   ROUND(SUM(gross_sales) / 1.15, 2) AS est_sales_excl_vat,
                   SUM(channel_fees) AS fees_and_refunds,
                   SUM(cogs) AS cogs,
                   SUM(net_profit) AS net_profit_excl_vat
            FROM financial_ledger
            {where_ch}
            GROUP BY DATE(transaction_date), UPPER(channel)
            ORDER BY tx_date DESC
            """,
            query_params,
        )
        if not df_ledger.empty:
            fig = px.bar(
                df_ledger,
                x="tx_date",
                y=["gross_sales_incl_vat", "est_sales_excl_vat", "net_profit_excl_vat"],
                color="channel",
                barmode="group",
                title="Daily Gross Sales (Incl. VAT) vs Sales (Excl. VAT) & Net Profit",
            )
            st.plotly_chart(fig, use_container_width=True)
            st.dataframe(df_ledger, use_container_width=True, hide_index=True)
        else:
            st.info("No records in `financial_ledger` yet.")

    with tab2:
        st.dataframe(
            run_query("""
                SELECT transaction_type, charge_type, COUNT(*) AS events, SUM(amount) AS total_zar
                FROM amazon_financial_events
                GROUP BY transaction_type, charge_type
                ORDER BY transaction_type, total_zar DESC
            """),
            use_container_width=True,
        )
        st.dataframe(
            run_query("""
                SELECT posted_date, amazon_order_id, transaction_type, sku, quantity_shipped,
                       charge_type, amount, currency
                FROM amazon_financial_events
                ORDER BY posted_date DESC
                LIMIT 250
            """),
            use_container_width=True,
        )

    with tab3:
        st.dataframe(
            run_query("""
                SELECT
                    e.sku,
                    COALESCE(MAX(i.title), e.sku) AS product_title,
                    SUM(CASE WHEN e.charge_type = 'Principal' THEN e.quantity_shipped ELSE 0 END) AS units_sold,
                    SUM(CASE WHEN e.transaction_type = 'Shipment' AND e.charge_type IN ('Principal', 'Tax') THEN e.amount ELSE 0 END) AS gross_incl_vat_zar,
                    SUM(CASE WHEN e.charge_type IN ('Principal', 'Refund_Principal') THEN e.amount ELSE 0 END) AS net_sales_excl_vat_zar,
                    SUM(CASE WHEN e.charge_type LIKE '%Tax%' THEN e.amount ELSE 0 END) AS vat_collected_zar,
                    SUM(CASE WHEN e.amount < 0 AND e.transaction_type LIKE '%Fee%' THEN ABS(e.amount) ELSE 0 END) AS amazon_fees_zar,
                    SUM(e.amount) AS net_amazon_payout_zar
                FROM amazon_financial_events e
                LEFT JOIN inventory_items i ON i.sku = e.sku
                WHERE e.sku IS NOT NULL AND e.sku != ''
                GROUP BY e.sku
                ORDER BY gross_incl_vat_zar DESC
                LIMIT 250
            """),
            use_container_width=True,
        )

    with tab4:
        st.subheader("Amazon.co.za Automated Inventory Deductions (`inventory_ledger`)")
        st.dataframe(
            run_query("""
                SELECT created_at, sku, reference_id AS order_sku_ref, quantity_change,
                       previous_quantity, new_quantity, user_id, notes
                FROM inventory_ledger
                WHERE channel = 'AMAZON_ZA' AND reason = 'AMAZON_SALE_DEDUCTION'
                ORDER BY id DESC
                LIMIT 300
            """),
            use_container_width=True,
        )

    with tab5:
        df_reg = run_query("""
            SELECT SUM(stock_ct) AS Cape_Town, SUM(stock_dbn) AS Durban, SUM(stock_jhb) AS Johannesburg, SUM(quantity) AS Total_Master
            FROM inventory_items
        """)
        st.bar_chart(df_reg.T)
        st.dataframe(
            run_query("""
                SELECT sku, title, stock_ct, stock_dbn, stock_jhb, quantity, cost_price
                FROM inventory_items
                WHERE (stock_ct + stock_dbn + stock_jhb) != quantity
                LIMIT 100
            """),
            use_container_width=True,
        )

    with tab6:
        st.dataframe(run_query("SELECT * FROM payout_settlements ORDER BY payout_date DESC"), use_container_width=True)