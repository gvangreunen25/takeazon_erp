from __future__ import annotations

import copy
import io
from typing import Any, Dict

import pandas as pd
from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.workbook.properties import CalcProperties
from sqlalchemy import text, types

from core.database import engine, execute_sql, log_audit, run_query

MARKETPLACES = ("Amazon", "Takealot", "Takeazon")

DEFAULT_MP_CONFIG: Dict[str, Dict[str, Any]] = {
    "Amazon": {
        "referral_fee": 0.15,
        "fulfilment_fee": 35.0,
        "subscription_allocation": 10.0,
        "return_fee": 15.0,
        "packaging_admin": 5.0,
        "total_integration_fee": 500.0,
        "other_cost": 0.0,
        "discount_pct": 0.0,
        "col_letter": "B",
    },
    "Takealot": {
        "referral_fee": 0.12,
        "fulfilment_fee": 42.0,
        "subscription_allocation": 15.0,
        "return_fee": 25.0,
        "packaging_admin": 5.0,
        "total_integration_fee": 500.0,
        "other_cost": 0.0,
        "discount_pct": 0.0,
        "col_letter": "C",
    },
    "Takeazon": {
        "referral_fee": 0.05,
        "fulfilment_fee": 24.0,
        "subscription_allocation": 5.0,
        "return_fee": 10.0,
        "packaging_admin": 5.0,
        "total_integration_fee": 500.0,
        "other_cost": 0.0,
        "discount_pct": 0.0,
        "col_letter": "D",
    },
}

DEFAULT_GLOBAL_CONFIG: Dict[str, float] = {
    "vat_rate": 0.15,
    "inbound_freight_pct": 0.05,
    "inbound_freight_flat": 0.00,
    "minimum_profit": 20.0,
    "rounding_increment": 5.0,
    "price_ending_deduction": 0.01,
    "minimum_listing_margin": 0.20,
    "minimum_listing_roi": 0.25,
    "target_margin_floor": 0.30,
    "global_discount_pct": 0.0,  # > 0 overrides individual marketplace discounts
}

# Excel Styling Constants
NAVY = "17365D"
BLUE = "1F4E78"
LIGHT_BLUE = "D9EAF7"
INPUT_YELLOW = "FFF2CC"
CURRENCY_FORMAT = '"R"#,##0.00;[Red]-"R"#,##0.00;"R"-'
PERCENT_FORMAT = "0.0%"
COUNT_FORMAT = "#,##0"
BORDER_COLOR = "B7C9D6"
THIN_SIDE = Side(style="thin", color=BORDER_COLOR)
THIN_BORDER = Border(left=THIN_SIDE, right=THIN_SIDE, top=THIN_SIDE, bottom=THIN_SIDE)


def get_default_configs() -> tuple[dict, dict]:
    return copy.deepcopy(DEFAULT_MP_CONFIG), copy.deepcopy(DEFAULT_GLOBAL_CONFIG)


def get_effective_discount(mp_config: dict, global_config: dict) -> float:
    """Returns global_discount_pct if > 0, otherwise falls back to the marketplace's own discount_pct."""
    global_disc = float(global_config.get("global_discount_pct", 0.0) or 0.0)
    if global_disc > 0:
        return min(max(global_disc, 0.0), 0.99)
    mp_disc = float(mp_config.get("discount_pct", 0.0) or 0.0)
    return min(max(mp_disc, 0.0), 0.99)


def _apply_rounding_and_ending(raw_price: float, global_config: dict) -> float:
    inc = max(0.01, float(global_config["rounding_increment"]))
    deduct = float(global_config["price_ending_deduction"])
    rounded_up = int((raw_price + deduct) / inc + 0.999999) * inc - deduct
    return round(max(0.0, float(rounded_up)), 2)


def calculate_recommended_price(
    landed_cost_incl_vat: float,
    total_items: int,
    mp_config: dict,
    global_config: dict,
) -> float:
    """Calculate target recommended retail price per listing/pack, applying effective marketplace or global discount."""
    integration_per_item = mp_config["total_integration_fee"] / total_items if total_items > 0 else 0.0
    fixed_fees = (
        landed_cost_incl_vat
        + mp_config["fulfilment_fee"]
        + mp_config["subscription_allocation"]
        + mp_config["return_fee"]
        + mp_config["packaging_admin"]
        + integration_per_item
        + mp_config["other_cost"]
    )

    target_margin = float(global_config.get("target_margin_floor", 0.30))
    denom_margin = 1.0 - mp_config["referral_fee"] - target_margin
    denom_profit = 1.0 - mp_config["referral_fee"]

    p_margin = fixed_fees / denom_margin if denom_margin > 0 else fixed_fees
    p_profit = (fixed_fees + global_config["minimum_profit"]) / denom_profit if denom_profit > 0 else fixed_fees
    base_target = max(p_margin, p_profit)

    base_rec_price = _apply_rounding_and_ending(base_target, global_config)

    effective_disc = get_effective_discount(mp_config, global_config)
    if effective_disc > 0:
        discounted_raw = base_rec_price * (1.0 - effective_disc)
        return _apply_rounding_and_ending(discounted_raw, global_config)

    return base_rec_price


def evaluate_listing_decision(net_profit: float, margin: float, roi: float, global_config: dict) -> str:
    """Classify listing decision based on profit, margin, and ROI rules."""
    if net_profit <= 0:
        return "DO NOT LIST"
    if (
        net_profit >= global_config["minimum_profit"]
        and margin >= global_config["minimum_listing_margin"]
        and roi >= global_config["minimum_listing_roi"]
    ):
        return "LIST"
    return "REVIEW"


def ensure_min_packs_column():
    try:
        if run_query("SHOW COLUMNS FROM inventory_items LIKE 'sold_in_min_packs_only'").empty:
            execute_sql("ALTER TABLE inventory_items ADD COLUMN sold_in_min_packs_only INT NOT NULL DEFAULT 1")
    except Exception:
        pass


def compute_profitability_dataframe(
    mp_configs: dict = None,
    global_configs: dict = None,
    preserve_existing_overrides: bool = True,
) -> pd.DataFrame:
    """
    Reads `inventory_items` from MySQL, calculates landed pack cost & multi-marketplace pricing/profit/ROI/decisions
    (including per-channel or global discount overrides), and optionally preserves existing `*_actual_price` overrides.
    """
    ensure_min_packs_column()
    mp_cfg = mp_configs or copy.deepcopy(DEFAULT_MP_CONFIG)
    g_cfg = global_configs or copy.deepcopy(DEFAULT_GLOBAL_CONFIG)

    df = run_query("""
        SELECT
            COALESCE(NULLIF(sku, ''), item_code) AS sku,
            COALESCE(NULLIF(description, ''), NULLIF(title, ''), sku) AS description,
            single_price_excl_vat,
            COALESCE(sold_in_min_packs_only, 1) AS sold_in_min_packs_only
        FROM inventory_items
        WHERE single_price_excl_vat IS NOT NULL AND single_price_excl_vat > 0
        ORDER BY sku
    """)

    total_items = len(df)
    if total_items == 0:
        return pd.DataFrame()

    existing_overrides = {}
    if preserve_existing_overrides:
        try:
            ex_df = run_query("""
                SELECT sku, amazon_actual_price, takealot_actual_price, takeazon_actual_price
                FROM inventory_item_profitability
            """)
            if not ex_df.empty:
                for _, er in ex_df.iterrows():
                    existing_overrides[str(er["sku"]).strip()] = {
                        "amazon": pd.to_numeric(er.get("amazon_actual_price"), errors="coerce"),
                        "takealot": pd.to_numeric(er.get("takealot_actual_price"), errors="coerce"),
                        "takeazon": pd.to_numeric(er.get("takeazon_actual_price"), errors="coerce"),
                    }
        except Exception:
            pass

    df["sku"] = df["sku"].astype(str).str.strip()
    df["single_price_excl_vat"] = pd.to_numeric(df["single_price_excl_vat"], errors="coerce").fillna(0.0).round(2)
    df["sold_in_min_packs_only"] = (
        pd.to_numeric(df["sold_in_min_packs_only"], errors="coerce")
        .fillna(1)
        .astype(int)
        .clip(lower=1)
    )

    freight_pct = float(g_cfg["inbound_freight_pct"])
    freight_flat = float(g_cfg["inbound_freight_flat"])
    vat_rate = float(g_cfg["vat_rate"])

    pack_price_excl_vat = df["single_price_excl_vat"] * df["sold_in_min_packs_only"]
    df["landed_cost_excl_vat"] = (pack_price_excl_vat * (1.0 + freight_pct) + freight_flat).round(2)
    df["cost_incl_vat"] = (df["landed_cost_excl_vat"] * (1.0 + vat_rate)).round(2)

    for mp in MARKETPLACES:
        prefix = mp.lower()
        cfg = mp_cfg[mp]
        integration_per_item = cfg["total_integration_fee"] / total_items
        eff_disc = get_effective_discount(cfg, g_cfg)

        rec_prices, act_prices, disc_applied, net_profits, margins, rois, decisions = [], [], [], [], [], [], []

        for _, row in df.iterrows():
            sku_key = row["sku"]
            c_landed_incl = float(row["cost_incl_vat"])
            rec_price = calculate_recommended_price(c_landed_incl, total_items, cfg, g_cfg)

            ov_price = existing_overrides.get(sku_key, {}).get(prefix)
            if preserve_existing_overrides and ov_price is not None and pd.notna(ov_price) and float(ov_price) > 0:
                act_price = round(float(ov_price), 2)
            else:
                act_price = rec_price

            ref_fee = act_price * cfg["referral_fee"]
            total_cost = (
                c_landed_incl
                + ref_fee
                + cfg["fulfilment_fee"]
                + cfg["subscription_allocation"]
                + cfg["return_fee"]
                + cfg["packaging_admin"]
                + integration_per_item
                + cfg["other_cost"]
            )

            net_profit = act_price - total_cost
            margin = net_profit / act_price if act_price > 0 else 0.0
            roi = net_profit / total_cost if total_cost > 0 else 0.0
            decision = evaluate_listing_decision(net_profit, margin, roi, g_cfg)

            rec_prices.append(rec_price)
            act_prices.append(act_price)
            disc_applied.append(round(eff_disc, 4))
            net_profits.append(round(net_profit, 2))
            margins.append(round(margin, 4))
            rois.append(round(roi, 4))
            decisions.append(decision)

        df[f"{prefix}_discount_pct"] = disc_applied
        df[f"{prefix}_rec_price"] = rec_prices
        df[f"{prefix}_actual_price"] = act_prices
        df[f"{prefix}_net_profit"] = net_profits
        df[f"{prefix}_margin_pct"] = margins
        df[f"{prefix}_roi_pct"] = rois
        df[f"{prefix}_decision"] = decisions

    return df


def recalculate_edited_matrix(
    edited_df: pd.DataFrame,
    mp_configs: dict,
    global_configs: dict,
) -> pd.DataFrame:
    """Recalculates Net Profit, Margin %, ROI %, and Decision after inline price/pack edits in Streamlit."""
    df = edited_df.copy()
    total_items = max(1, len(df))

    df["single_price_excl_vat"] = pd.to_numeric(df["single_price_excl_vat"], errors="coerce").fillna(0.0).round(2)
    df["sold_in_min_packs_only"] = pd.to_numeric(df["sold_in_min_packs_only"], errors="coerce").fillna(1).astype(int).clip(lower=1)

    pack_excl = df["single_price_excl_vat"] * df["sold_in_min_packs_only"]
    df["landed_cost_excl_vat"] = (pack_excl * (1.0 + float(global_configs["inbound_freight_pct"])) + float(global_configs["inbound_freight_flat"])).round(2)
    df["cost_incl_vat"] = (df["landed_cost_excl_vat"] * (1.0 + float(global_configs["vat_rate"]))).round(2)

    for mp in MARKETPLACES:
        prefix = mp.lower()
        cfg = mp_configs[mp]
        integration_per_item = cfg["total_integration_fee"] / total_items
        eff_disc = get_effective_discount(cfg, global_configs)

        net_profits, margins, rois, decisions = [], [], [], []
        for _, row in df.iterrows():
            c_landed_incl = float(row["cost_incl_vat"])
            act_price = float(pd.to_numeric(row.get(f"{prefix}_actual_price"), errors="coerce") or 0.0)
            ref_fee = act_price * cfg["referral_fee"]
            total_cost = (
                c_landed_incl
                + ref_fee
                + cfg["fulfilment_fee"]
                + cfg["subscription_allocation"]
                + cfg["return_fee"]
                + cfg["packaging_admin"]
                + integration_per_item
                + cfg["other_cost"]
            )
            net_profit = act_price - total_cost
            margin = net_profit / act_price if act_price > 0 else 0.0
            roi = net_profit / total_cost if total_cost > 0 else 0.0
            manual_dec = str(row.get(f"{prefix}_decision") or "").strip().upper()
            decision = manual_dec if manual_dec in ("REJECT", "SKIP") else evaluate_listing_decision(net_profit, margin, roi, global_configs)

            net_profits.append(round(net_profit, 2))
            margins.append(round(margin, 4))
            rois.append(round(roi, 4))
            decisions.append(decision)

        df[f"{prefix}_discount_pct"] = round(eff_disc, 4)
        df[f"{prefix}_net_profit"] = net_profits
        df[f"{prefix}_margin_pct"] = margins
        df[f"{prefix}_roi_pct"] = rois
        df[f"{prefix}_decision"] = decisions

    return df


def save_profitability_to_mysql(df: pd.DataFrame, triggered_by: str = "admin"):
    """Persists the calculated profitability DataFrame to `inventory_item_profitability` and indexes `sku`."""
    cols = [
        "sku", "description", "single_price_excl_vat", "sold_in_min_packs_only",
        "landed_cost_excl_vat", "cost_incl_vat",
        "amazon_discount_pct", "amazon_rec_price", "amazon_actual_price", "amazon_net_profit", "amazon_margin_pct", "amazon_roi_pct", "amazon_decision",
        "takealot_discount_pct", "takealot_rec_price", "takealot_actual_price", "takealot_net_profit", "takealot_margin_pct", "takealot_roi_pct", "takealot_decision",
        "takeazon_discount_pct", "takeazon_rec_price", "takeazon_actual_price", "takeazon_net_profit", "takeazon_margin_pct", "takeazon_roi_pct", "takeazon_decision",
    ]
    df_mysql = df[[c for c in cols if c in df.columns]].copy()

    dtype_mapping = {
        "sku": types.VARCHAR(100),
        "description": types.TEXT(),
        "single_price_excl_vat": types.DECIMAL(10, 2),
        "sold_in_min_packs_only": types.INTEGER(),
        "landed_cost_excl_vat": types.DECIMAL(10, 2),
        "cost_incl_vat": types.DECIMAL(10, 2),
        "amazon_discount_pct": types.DECIMAL(6, 4),
        "amazon_rec_price": types.DECIMAL(10, 2),
        "amazon_actual_price": types.DECIMAL(10, 2),
        "amazon_net_profit": types.DECIMAL(10, 2),
        "amazon_margin_pct": types.DECIMAL(6, 4),
        "amazon_roi_pct": types.DECIMAL(6, 4),
        "amazon_decision": types.VARCHAR(20),
        "takealot_discount_pct": types.DECIMAL(6, 4),
        "takealot_rec_price": types.DECIMAL(10, 2),
        "takealot_actual_price": types.DECIMAL(10, 2),
        "takealot_net_profit": types.DECIMAL(10, 2),
        "takealot_margin_pct": types.DECIMAL(6, 4),
        "takealot_roi_pct": types.DECIMAL(6, 4),
        "takealot_decision": types.VARCHAR(20),
        "takeazon_discount_pct": types.DECIMAL(6, 4),
        "takeazon_rec_price": types.DECIMAL(10, 2),
        "takeazon_actual_price": types.DECIMAL(10, 2),
        "takeazon_net_profit": types.DECIMAL(10, 2),
        "takeazon_margin_pct": types.DECIMAL(6, 4),
        "takeazon_roi_pct": types.DECIMAL(6, 4),
        "takeazon_decision": types.VARCHAR(20),
    }

    df_mysql.to_sql(
        name="inventory_item_profitability",
        con=engine,
        if_exists="replace",
        index=False,
        dtype=dtype_mapping,
        chunksize=1000,
    )

    try:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE inventory_item_profitability ADD INDEX idx_prof_sku (sku)"))
    except Exception:
        pass

    log_audit(
        triggered_by,
        "PROFITABILITY_SAVED",
        "Saved Multi-Marketplace Profitability Matrix",
        f"Persisted {len(df_mysql):,} SKUs to inventory_item_profitability",
        len(df_mysql),
    )


def build_profitability_excel_bytes(
    df: pd.DataFrame,
    mp_configs: dict = None,
    global_configs: dict = None,
) -> bytes:
    """Generates the formula-driven 3-sheet Excel workbook (Assumptions, Profitability, Dashboard) in memory."""
    mp_cfg = mp_configs or copy.deepcopy(DEFAULT_MP_CONFIG)
    g_cfg = global_configs or copy.deepcopy(DEFAULT_GLOBAL_CONFIG)

    wb = Workbook()
    wb.calcProperties = CalcProperties(fullCalcOnLoad=True)

    # SHEET 1: ASSUMPTIONS
    ws_assump = wb.active
    ws_assump.title = "Assumptions"
    ws_assump.sheet_view.showGridLines = False

    ws_assump.merge_cells("A1:E2")
    top_left = ws_assump["A1"]
    top_left.value = "E-commerce Multi-Marketplace Profitability Assumptions"
    top_left.fill = PatternFill("solid", fgColor=NAVY)
    top_left.font = Font(name="Aptos", size=18, bold=True, color="FFFFFF")
    top_left.alignment = Alignment(vertical="center", horizontal="left")

    headers = ["Parameter / Fee", "Amazon", "Takealot", "Takeazon", "Unit / Notes"]
    for col, val in enumerate(headers, 1):
        cell = ws_assump.cell(3, col, val)
        cell.fill = PatternFill("solid", fgColor=BLUE)
        cell.font = Font(name="Aptos", bold=True, color="FFFFFF")
        cell.border = THIN_BORDER

    fee_rows = [
        ("Referral / Marketplace Fee", "referral_fee", "% of selling price", PERCENT_FORMAT),
        ("Fulfilment Fee", "fulfilment_fee", "R per item/pack", CURRENCY_FORMAT),
        ("Subscription Allocation", "subscription_allocation", "R per item/pack", CURRENCY_FORMAT),
        ("Fixed Return Fee", "return_fee", "R per item/pack", CURRENCY_FORMAT),
        ("Packaging / Admin", "packaging_admin", "R per item/pack", CURRENCY_FORMAT),
        ("Total Integration Fee", "total_integration_fee", "R total split across catalog", CURRENCY_FORMAT),
        ("Other Direct Cost", "other_cost", "R per item/pack", CURRENCY_FORMAT),
        ("Channel Discount %", "discount_pct", "% discount off base recommended price (overridden if Global Discount > 0%)", PERCENT_FORMAT),
    ]

    for r_idx, (label, key, unit, fmt) in enumerate(fee_rows, 4):
        ws_assump.cell(r_idx, 1, label).font = Font(name="Aptos", bold=True, color=NAVY)
        ws_assump.cell(r_idx, 5, unit)
        for c_idx, mp in enumerate(MARKETPLACES, 2):
            cell = ws_assump.cell(r_idx, c_idx, mp_cfg[mp].get(key, 0.0))
            cell.fill = PatternFill("solid", fgColor=INPUT_YELLOW)
            cell.font = Font(name="Aptos", bold=True, color=BLUE)
            cell.number_format = fmt
            cell.border = THIN_BORDER

    ws_assump.merge_cells("A13:E13")
    ws_assump["A13"] = "Global Rules, Freight & Discount Override Thresholds"
    for cell in ws_assump["A13:E13"][0]:
        cell.fill = PatternFill("solid", fgColor=LIGHT_BLUE)
        cell.font = Font(name="Aptos", bold=True, color=NAVY, size=12)

    global_rows = [
        ("VAT Rate", g_cfg["vat_rate"], "%", PERCENT_FORMAT, 14),
        ("Inbound Freight / Fuel %", g_cfg["inbound_freight_pct"], "% of supplier cost", PERCENT_FORMAT, 15),
        ("Inbound Freight Flat", g_cfg["inbound_freight_flat"], "R per pack", CURRENCY_FORMAT, 16),
        ("Minimum Profit per Unit/Pack", g_cfg["minimum_profit"], "R per pack", CURRENCY_FORMAT, 17),
        ("Price Rounding Increment", g_cfg["rounding_increment"], "R", CURRENCY_FORMAT, 18),
        ("Price Ending Deduction", g_cfg["price_ending_deduction"], "R", CURRENCY_FORMAT, 19),
        ("Minimum Listing Margin", g_cfg["minimum_listing_margin"], "% of selling price", PERCENT_FORMAT, 20),
        ("Minimum Listing ROI", g_cfg["minimum_listing_roi"], "% of total costs", PERCENT_FORMAT, 21),
        ("Global Discount Override %", g_cfg.get("global_discount_pct", 0.0), "If > 0%, overwrites all individual channel discounts", PERCENT_FORMAT, 22),
    ]

    for label, val, unit, fmt, r_idx in global_rows:
        ws_assump.cell(r_idx, 1, label).font = Font(name="Aptos", bold=True, color=NAVY)
        c = ws_assump.cell(r_idx, 2, val)
        c.fill = PatternFill("solid", fgColor=INPUT_YELLOW)
        c.font = Font(name="Aptos", bold=True, color=BLUE)
        c.number_format = fmt
        c.border = THIN_BORDER
        ws_assump.cell(r_idx, 5, unit)

    for col, width in {"A": 34, "B": 16, "C": 16, "D": 16, "E": 55}.items():
        ws_assump.column_dimensions[col].width = width

    # SHEET 2: PROFITABILITY
    ws_prof = wb.create_sheet("Profitability")
    prof_headers = [
        "SKU",
        "Product Description",
        "Single Price Excl VAT",
        "Min Pack Qty",
        "Landed Cost Excl VAT",
        "Cost Incl VAT",
    ]
    for mp in MARKETPLACES:
        prof_headers.extend([
            f"{mp} Discount %", f"{mp} Rec Price", f"{mp} Actual Price", f"{mp} Net Profit",
            f"{mp} Margin %", f"{mp} ROI %", f"{mp} Decision",
        ])

    for col, h in enumerate(prof_headers, 1):
        cell = ws_prof.cell(1, col, h)
        cell.fill = PatternFill("solid", fgColor=BLUE)
        cell.font = Font(name="Aptos", bold=True, color="FFFFFF")
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)

    last_row = len(df) + 1
    target_margin = float(g_cfg.get("target_margin_floor", 0.30))

    for r_idx, row in enumerate(df.itertuples(), 2):
        ws_prof.cell(r_idx, 1, row.sku)
        ws_prof.cell(r_idx, 2, row.description)
        ws_prof.cell(r_idx, 3, float(row.single_price_excl_vat)).number_format = CURRENCY_FORMAT
        ws_prof.cell(r_idx, 4, int(row.sold_in_min_packs_only)).number_format = COUNT_FORMAT

        ws_prof.cell(r_idx, 5, f"=(C{r_idx}*D{r_idx})*(1+'Assumptions'!$B$15)+'Assumptions'!$B$16").number_format = CURRENCY_FORMAT
        ws_prof.cell(r_idx, 6, f"=E{r_idx}*(1+'Assumptions'!$B$14)").number_format = CURRENCY_FORMAT

        col_cursor = 7
        for mp in MARKETPLACES:
            mp_col = mp_cfg[mp]["col_letter"]

            # Effective Discount % = IF(Global Override > 0, Global Override, Channel Discount)
            disc_col_let = get_column_letter(col_cursor)
            ws_prof.cell(r_idx, col_cursor, f"=IF('Assumptions'!$B$22>0,'Assumptions'!$B$22,'Assumptions'!{mp_col}$11)").number_format = PERCENT_FORMAT

            base_rec_expr = (
                f"ROUNDUP((MAX((F{r_idx}+'Assumptions'!{mp_col}$5+'Assumptions'!{mp_col}$6+'Assumptions'!{mp_col}$7+'Assumptions'!{mp_col}$8+('Assumptions'!{mp_col}$9/COUNTA($A$2:$A${last_row}))+'Assumptions'!{mp_col}$10)/(1-'Assumptions'!{mp_col}$4-{target_margin}),"
                f"(F{r_idx}+'Assumptions'!{mp_col}$5+'Assumptions'!{mp_col}$6+'Assumptions'!{mp_col}$7+'Assumptions'!{mp_col}$8+('Assumptions'!{mp_col}$9/COUNTA($A$2:$A${last_row}))+'Assumptions'!{mp_col}$10+'Assumptions'!$B$17)/(1-'Assumptions'!{mp_col}$4))+"
                f"'Assumptions'!$B$19)/'Assumptions'!$B$18,0)*'Assumptions'!$B$18-'Assumptions'!$B$19"
            )
            rec_price_formula = (
                f"=IFERROR(ROUNDUP((({base_rec_expr})*(1-{disc_col_let}{r_idx})+'Assumptions'!$B$19)/'Assumptions'!$B$18,0)*'Assumptions'!$B$18-'Assumptions'!$B$19,\"\")"
            )
            ws_prof.cell(r_idx, col_cursor + 1, rec_price_formula).number_format = CURRENCY_FORMAT

            act_cell = ws_prof.cell(r_idx, col_cursor + 2, f"={get_column_letter(col_cursor + 1)}{r_idx}")
            act_cell.number_format = CURRENCY_FORMAT
            act_cell.fill = PatternFill("solid", fgColor=INPUT_YELLOW)

            rec_col_let = get_column_letter(col_cursor + 2)
            total_cost_formula = (
                f"(F{r_idx}+{rec_col_let}{r_idx}*'Assumptions'!{mp_col}$4+'Assumptions'!{mp_col}$5+'Assumptions'!{mp_col}$6+"
                f"'Assumptions'!{mp_col}$7+'Assumptions'!{mp_col}$8+('Assumptions'!{mp_col}$9/COUNTA($A$2:$A${last_row}))+'Assumptions'!{mp_col}$10)"
            )

            ws_prof.cell(r_idx, col_cursor + 3, f"=IF({rec_col_let}{r_idx}=\"\",\"\",{rec_col_let}{r_idx}-{total_cost_formula})").number_format = CURRENCY_FORMAT

            profit_col_let = get_column_letter(col_cursor + 3)
            ws_prof.cell(r_idx, col_cursor + 4, f"=IFERROR({profit_col_let}{r_idx}/{rec_col_let}{r_idx},0)").number_format = PERCENT_FORMAT
            ws_prof.cell(r_idx, col_cursor + 5, f"=IFERROR({profit_col_let}{r_idx}/{total_cost_formula},0)").number_format = PERCENT_FORMAT

            margin_col_let = get_column_letter(col_cursor + 4)
            roi_col_let = get_column_letter(col_cursor + 5)
            decision_formula = (
                f"=IF({profit_col_let}{r_idx}<=0,\"DO NOT LIST\","
                f"IF(AND({profit_col_let}{r_idx}>='Assumptions'!$B$17,{margin_col_let}{r_idx}>='Assumptions'!$B$20,{roi_col_let}{r_idx}>='Assumptions'!$B$21),\"LIST\",\"REVIEW\"))"
            )
            ws_prof.cell(r_idx, col_cursor + 6, decision_formula)
            col_cursor += 7

    ws_prof.column_dimensions["A"].width = 16
    ws_prof.column_dimensions["B"].width = 40
    ws_prof.column_dimensions["C"].width = 22
    ws_prof.column_dimensions["D"].width = 16
    ws_prof.column_dimensions["E"].width = 22
    ws_prof.column_dimensions["F"].width = 20
    for c in range(7, col_cursor):
        ws_prof.column_dimensions[get_column_letter(c)].width = 18

    # SHEET 3: DASHBOARD
    ws_dash = wb.create_sheet("Dashboard")
    ws_dash.sheet_view.showGridLines = False

    ws_dash.merge_cells("A1:G2")
    ws_dash["A1"].value = "Multi-Marketplace Profitability Summary"
    ws_dash["A1"].fill = PatternFill("solid", fgColor=NAVY)
    ws_dash["A1"].font = Font(name="Aptos", size=18, bold=True, color="FFFFFF")
    ws_dash["A1"].alignment = Alignment(vertical="center", horizontal="left")

    dash_headers = ["Marketplace", "Listed Items", "Review Items", "Do Not List", "Avg Margin %", "Avg ROI %"]
    for col, value in enumerate(dash_headers, 1):
        cell = ws_dash.cell(4, col, value)
        cell.fill = PatternFill("solid", fgColor=BLUE)
        cell.font = Font(name="Aptos", bold=True, color="FFFFFF")
        cell.border = THIN_BORDER

    for idx, mp in enumerate(MARKETPLACES, 5):
        ws_dash.cell(idx, 1, mp).font = Font(name="Aptos", bold=True, color=NAVY)
        dec_col = get_column_letter(13 + (idx - 5) * 7)
        margin_col = get_column_letter(11 + (idx - 5) * 7)
        roi_col = get_column_letter(12 + (idx - 5) * 7)

        ws_dash.cell(idx, 2, f"=COUNTIF('Profitability'!${dec_col}$2:${dec_col}${last_row},\"LIST\")").number_format = COUNT_FORMAT
        ws_dash.cell(idx, 3, f"=COUNTIF('Profitability'!${dec_col}$2:${dec_col}${last_row},\"REVIEW\")").number_format = COUNT_FORMAT
        ws_dash.cell(idx, 4, f"=COUNTIF('Profitability'!${dec_col}$2:${dec_col}${last_row},\"DO NOT LIST\")").number_format = COUNT_FORMAT
        ws_dash.cell(idx, 5, f"=AVERAGE('Profitability'!${margin_col}$2:${margin_col}${last_row})").number_format = PERCENT_FORMAT
        ws_dash.cell(idx, 6, f"=AVERAGE('Profitability'!${roi_col}$2:${roi_col}${last_row})").number_format = PERCENT_FORMAT

        for c in range(1, 7):
            ws_dash.cell(idx, c).border = THIN_BORDER

    for col, w in {"A": 20, "B": 15, "C": 15, "D": 15, "E": 15, "F": 15}.items():
        ws_dash.column_dimensions[col].width = w

    out_buf = io.BytesIO()
    wb.save(out_buf)
    return out_buf.getvalue()