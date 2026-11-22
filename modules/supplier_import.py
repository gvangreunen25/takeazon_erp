import re
import pandas as pd
import streamlit as st
from sqlalchemy import text

from core.auth import has_perm
from core.database import (
    engine,
    execute_sql,
    get_cached_master_skus,
    get_excel_sheet_names,
    load_uploaded_spreadsheet,
    log_audit,
    run_query,
)


def _parse_stock_series(s: pd.Series) -> pd.Series:
    str_s = s.fillna("0").astype(str).str.strip()
    over_mask = str_s.str.lower().str.contains("over 500", na=False)
    nums = pd.to_numeric(str_s, errors="coerce").fillna(0).where(~over_mask, 500)
    return nums.clip(lower=0).astype(int)


def _parse_min_pack_series(s: pd.Series) -> pd.Series:
    str_s = s.fillna("1").astype(str).str.strip().str.replace(r"[^\d.]", "", regex=True)
    nums = pd.to_numeric(str_s, errors="coerce").fillna(1).astype(int)
    return nums.clip(lower=1)


def _clean_barcode_series(s: pd.Series) -> pd.Series:
    out = s.fillna("").astype(str).str.strip().str.replace(r"\.0$", "", regex=True)
    return out.where(~out.str.lower().isin(["nan", "none", "null", "0"]), "")


def _auto_detect_col(columns: list, candidates: list, allow_none: bool = False) -> int:
    norm_cols = {c.strip().lower(): idx for idx, c in enumerate(columns)}
    for cand in candidates:
        c_low = cand.strip().lower()
        if c_low in norm_cols:
            return norm_cols[c_low] + (1 if allow_none else 0)
    for cand in candidates:
        c_low = cand.strip().lower()
        for idx, col_name in enumerate(columns):
            if c_low in col_name.strip().lower():
                return idx + (1 if allow_none else 0)
    return 0


def _ensure_min_pack_schema():
    try:
        if run_query("SHOW COLUMNS FROM inventory_items LIKE 'sold_in_min_packs_only'").empty:
            execute_sql("ALTER TABLE inventory_items ADD COLUMN sold_in_min_packs_only INT NOT NULL DEFAULT 1")
    except Exception:
        pass


def render(auth_user: dict):
    current_user = auth_user["username"]
    _ensure_min_pack_schema()

    st.title("📥 Supplier Excel Importer & Advanced Rule Engine")
    st.caption("Auto-Column Mapping, Batched Database Commits, Discontinued Overrides, Min Pack Multipliers & Category Restrictions")

    can_commit = has_perm(auth_user, "upsert_supplier_items") or auth_user["flags"].get("can_edit_table_settings", False)
    if not can_commit:
        st.info("Read-only preview mode: You require `upsert_supplier_items` permission to write to `inventory_items`.")

    tab_import, tab_rules = st.tabs(["📄 Import & Parse File", "⚙️ Exclusion Rules & Detailed Controls Matrix"])

    with tab_rules:
        st.subheader("🛑 Operational Rules, Discontinued Overrides & Stock Cutoffs")
        r_left, r_right = st.columns(2)

        with r_left:
            st.markdown("#### 📂 Option A: Upload Excluded SKUs File")
            excl_file = st.file_uploader("Upload Excluded SKUs List (.csv or .xlsx)", type=["csv", "xlsx", "xls"], key="rule_excl_file")
            file_excluded_skus = set()
            if excl_file is not None:
                try:
                    edf = load_uploaded_spreadsheet(excl_file.getvalue(), excl_file.name)
                    if not edf.empty:
                        first_col = edf.columns[0]
                        file_excluded_skus = {str(x).strip().upper() for x in edf[first_col].dropna().tolist() if str(x).strip()}
                        st.caption(f"Loaded **{len(file_excluded_skus):,}** excluded SKUs from `{excl_file.name}`.")
                except Exception as ex:
                    st.error(f"Could not read Excluded SKUs file: {ex}")

            st.divider()
            st.markdown("#### ✍️ Option B: Manual Excluded SKUs List")
            manual_excl_raw = st.text_area("Manual SKUs (One per line or comma-separated)", placeholder="SKU-OLD-01\nSKU-DISCONTINUED", height=115, key="rule_manual_excl")

            st.divider()
            st.markdown("#### 🚫 Granular Category Restrictions & Safe-Words")
            blk_food = st.checkbox("Block Consumable Food & Sweets (Consumables Only)", value=True, key="rule_blk_food")
            blk_pet = st.checkbox("Block Pet Food & Treats (Consumables Only)", value=True, key="rule_blk_pet")
            blk_haz = st.checkbox("Block Hazardous / Flammable Goods (Batteries, Aerosols)", value=True, key="rule_blk_haz")
            blk_perish = st.checkbox("Block Short-Expiry & Perishables (Fresh, Frozen)", value=True, key="rule_blk_perish")
            custom_restricted_raw = st.text_input("Custom Restricted Exact Words (comma-separated)", placeholder="CANDY, GUMMY, LIQUID, FLAMMABLE, ALCOHOL", key="rule_custom_words")
            safe_words_raw = st.text_input(
                "Allowed Keywords (Bypasses Category Block)",
                value="TOY, UTENSIL, BOWL, LEASH, COLLAR, FEEDER, PROCESSOR, CONTAINER, MAT, BED, HARNESS, CUTLERY, KNIFE, FORK, SPOON",
                key="rule_safe_words",
            )

        with r_right:
            st.markdown("#### 📦 Quantity Cutoffs & Discontinued Overrides")
            zero_qty_below = st.number_input("Zero-out Stock if Available Qty Below", min_value=0, value=5, step=1, key="rule_zero_below")
            enable_disc_override = st.checkbox("Enable Discontinued Item Stock Override Rules", value=True, key="rule_enable_disc")
            disc_keywords_raw = st.text_input("Discontinued Keywords in Info Column", value="DISC, DISCONTINUED, NO MORE ORDERS, OBSOLETE, END OF LIFE", key="rule_disc_kw")
            disc_qty_floor = st.number_input("Discontinued Qty Floor Cutoff", min_value=0, value=100, step=5, key="rule_disc_floor")

            st.divider()
            st.markdown("#### 🔤 Granular SKU Formatting & Cleaning")
            strip_prefixes_raw = st.text_input("Prefixes to Strip (comma-separated)", placeholder="VEND-, SUP-, IMP-", key="rule_strip_pref")
            strip_suffixes_raw = st.text_input("Suffixes to Strip (comma-separated)", placeholder="-ZA, -BOX, -PACK", key="rule_strip_suff")
            remove_special_chars = st.checkbox("Remove Special Characters (Keep only Alphanumeric & Hyphens)", value=True, key="rule_clean_chars")
            force_uppercase_skus = st.checkbox("Force Upper-case SKUs", value=True, key="rule_upper_sku")
            exclude_rejected_matrix = st.checkbox("Also Exclude SKUs marked 'REJECT' or 'SKIP' in `inventory_item_profitability`", value=True, key="rule_excl_matrix")

    with tab_import:
        uploaded = st.file_uploader("Upload Supplier Invoice / Price List (.xlsx or .csv)", type=["xlsx", "xls", "csv"], key="supplier_main_upload")
        if uploaded is not None:
            fbytes = uploaded.getvalue()
            if uploaded.name.lower().endswith(".csv"):
                sheet_label = "CSV"
            else:
                sheets = get_excel_sheet_names(fbytes)
                sheet_label = st.selectbox("Select Worksheet", sheets) if len(sheets) > 1 else sheets[0]

            raw_df = load_uploaded_spreadsheet(fbytes, uploaded.name, sheet_label)
            st.success(f"Loaded `{uploaded.name}` (**{len(raw_df):,}** rows). Auto-mapping column headers...")

            raw_cols = list(raw_df.columns)
            cols_with_none = ["None"] + raw_cols

            st.subheader("🔗 Column Mapping (Auto-Detected)")
            m1, m2, m3, m4, m5, m6 = st.columns(6)
            col_sku = m1.selectbox("SKU / Item Code", raw_cols, index=_auto_detect_col(raw_cols, ["Item", "SKU", "Item Code", "Product Code"]))
            col_title = m2.selectbox("Title / Description", cols_with_none, index=_auto_detect_col(raw_cols, ["Description", "Title", "Product Name"], allow_none=True))
            col_cost = m3.selectbox("Cost Price Excl VAT", raw_cols, index=_auto_detect_col(raw_cols, ["Classic Single Price Excl Vat Each", "Cost Price", "Cost", "Price Excl"]))
            col_qty = m4.selectbox("Quantity Column", cols_with_none, index=_auto_detect_col(raw_cols, ["NAT", "Qty", "Quantity", "Stock"], allow_none=True))
            col_info = m5.selectbox("Remarks/Info", cols_with_none, index=_auto_detect_col(raw_cols, ["Important Information", "Remarks", "Info", "Status"], allow_none=True))
            col_min_pack = m6.selectbox("Min Pack Qty", cols_with_none, index=_auto_detect_col(raw_cols, ["Sold in Min Pack Only", "Sold in Min Packs Only", "Min Pack", "Inner"], allow_none=True))

            with st.expander("📦 Regional Warehouse Stock & Barcode Columns (`inventory_items`)", expanded=True):
                b1, b2, b3, b4, b5, b6, b7 = st.columns(7)
                col_ct = b1.selectbox("Stock CT", cols_with_none, index=_auto_detect_col(raw_cols, ["CT", "Cape Town"], allow_none=True))
                col_dbn = b2.selectbox("Stock DBN", cols_with_none, index=_auto_detect_col(raw_cols, ["DBN", "Durban"], allow_none=True))
                col_jhb = b3.selectbox("Stock JHB", cols_with_none, index=_auto_detect_col(raw_cols, ["JHB", "Johannesburg"], allow_none=True))
                col_pe = b4.selectbox("Stock PE", cols_with_none, index=_auto_detect_col(raw_cols, ["PE", "Gqeberha"], allow_none=True))
                col_bc_single = b5.selectbox("Single Barcode (EAN)", cols_with_none, index=_auto_detect_col(raw_cols, ["Single Barcode", "Barcode", "EAN"], allow_none=True))
                col_bc_inner = b6.selectbox("Inner Barcode", cols_with_none, index=_auto_detect_col(raw_cols, ["Inner Barcode"], allow_none=True))
                col_bc_case = b7.selectbox("Case Barcode", cols_with_none, index=_auto_detect_col(raw_cols, ["Case Barcode"], allow_none=True))

            commit_mode = st.radio(
                "Database Commit Mode (`inventory_items`)",
                ["Full Upsert (Stock, Costs, Min Packs, Regional Stock & Barcodes)", "Update Stock Quantities Only", "Update Costs, Min Packs & Barcodes Only"],
                horizontal=True,
            )

            if st.button("⚡ Apply Operational Rules & Process Sheet", type="primary"):
                st.session_state["supplier_processed_ready"] = True

            if st.session_state.get("supplier_processed_ready", False):
                df = raw_df.copy()
                sku_s = df[col_sku].fillna("").astype(str).str.strip()
                sku_s = sku_s.where(~sku_s.str.lower().isin(["nan", "none", "null"]), "")
                for p in [x.strip() for x in strip_prefixes_raw.split(",") if x.strip()]:
                    sku_s = sku_s.str.replace(rf"^{re.escape(p)}", "", case=False, regex=True)
                for sf in [x.strip() for x in strip_suffixes_raw.split(",") if x.strip()]:
                    sku_s = sku_s.str.replace(rf"{re.escape(sf)}$", "", case=False, regex=True)
                if remove_special_chars:
                    sku_s = sku_s.str.replace(r"[^A-Za-z0-9\-]", "", regex=True)
                if force_uppercase_skus:
                    sku_s = sku_s.str.upper()

                df["_sku"] = sku_s.str.strip()
                df["_title"] = df[col_title].fillna("").astype(str).str.strip() if col_title != "None" else ""
                df["_cost"] = pd.to_numeric(df[col_cost], errors="coerce").fillna(0.0)
                df["_info"] = df[col_info].fillna("").astype(str).str.strip() if col_info != "None" else ""
                df["_min_pack"] = _parse_min_pack_series(df[col_min_pack]) if col_min_pack != "None" else 1

                df["_ct"] = _parse_stock_series(df[col_ct]) if col_ct != "None" else 0
                df["_dbn"] = _parse_stock_series(df[col_dbn]) if col_dbn != "None" else 0
                df["_jhb"] = _parse_stock_series(df[col_jhb]) if col_jhb != "None" else 0
                df["_pe"] = _parse_stock_series(df[col_pe]) if col_pe != "None" else 0

                if col_qty != "None":
                    df["_raw_qty"] = _parse_stock_series(df[col_qty])
                elif any(c != "None" for c in (col_ct, col_dbn, col_jhb, col_pe)):
                    df["_raw_qty"] = df["_ct"] + df["_dbn"] + df["_jhb"] + df["_pe"]
                else:
                    df["_raw_qty"] = 0

                df["_bc_single"] = _clean_barcode_series(df[col_bc_single]) if col_bc_single != "None" else ""
                df["_bc_inner"] = _clean_barcode_series(df[col_bc_inner]) if col_bc_inner != "None" else ""
                df["_bc_case"] = _clean_barcode_series(df[col_bc_case]) if col_bc_case != "None" else ""

                df["_final_qty"] = df["_raw_qty"].copy()
                df["_stock_note"] = ""

                if zero_qty_below > 0:
                    low_mask = (df["_final_qty"] > 0) & (df["_final_qty"] < int(zero_qty_below))
                    df.loc[low_mask, "_final_qty"] = 0
                    df.loc[low_mask, "_stock_note"] += f"Zeroed (<{zero_qty_below}); "

                if enable_disc_override and col_info != "None":
                    disc_kws = [k.strip().upper() for k in disc_keywords_raw.split(",") if k.strip()]
                    if disc_kws:
                        is_disc = df["_info"].str.upper().str.contains("|".join(re.escape(k) for k in disc_kws), regex=True, na=False)
                        disc_zero_mask = is_disc & (df["_final_qty"] < int(disc_qty_floor))
                        df.loc[disc_zero_mask, "_final_qty"] = 0
                        df.loc[disc_zero_mask, "_stock_note"] += f"Discontinued (<{disc_qty_floor} floor); "
                        df.loc[is_disc & ~disc_zero_mask, "_stock_note"] += "Discontinued (Above floor); "

                df.loc[df["_final_qty"] == 0, ["_ct", "_dbn", "_jhb", "_pe"]] = 0

                reasons = pd.Series("", index=df.index)
                reasons[df["_sku"] == ""] += "Blank SKU; "
                reasons[df["_cost"] <= 0] += "Zero/Invalid Cost; "

                manual_set = {re.sub(r"[^A-Za-z0-9\-]", "", x.strip()).upper() for x in re.split(r"[\n,;]+", manual_excl_raw) if x.strip()}
                all_manual_excl = file_excluded_skus.union(manual_set)
                if all_manual_excl:
                    reasons[df["_sku"].isin(all_manual_excl)] += "Excluded SKU List; "

                restricted_words = []
                if blk_food:
                    restricted_words.extend(["CHOCOLATE", "SWEET", "CANDY", "GUMMY", "BISCUIT", "SNACK", "COFFEE", "TEA", "SUGAR", "SPICE", "SAUCE", "SYRUP", "JUICE", "FOOD"])
                if blk_pet:
                    restricted_words.extend(["DOG FOOD", "CAT FOOD", "PET FOOD", "PET TREAT", "KIBBLE", "CATNIP"])
                if blk_haz:
                    restricted_words.extend(["FLAMMABLE", "AEROSOL", "LITHIUM", "BATTERY", "BATTERIES", "BUTANE", "LIGHTER GAS", "ACID", "BLEACH", "SOLVENT"])
                if blk_perish:
                    restricted_words.extend(["PERISHABLE", "FRESH", "FROZEN", "DAIRY", "YOGHURT", "CHEESE", "MEAT"])
                if custom_restricted_raw.strip():
                    restricted_words.extend([w.strip().upper() for w in custom_restricted_raw.split(",") if w.strip()])

                safe_words = [w.strip().upper() for w in safe_words_raw.split(",") if w.strip()]
                if restricted_words and col_title != "None":
                    title_upper = df["_title"].str.upper()
                    hit_restricted = title_upper.str.contains(r"\b(?:" + "|".join(re.escape(w) for w in set(restricted_words)) + r")\b", regex=True, na=False)
                    if safe_words:
                        hit_safe = title_upper.str.contains(r"\b(?:" + "|".join(re.escape(w) for w in set(safe_words)) + r")\b", regex=True, na=False)
                        reasons[hit_restricted & ~hit_safe] += "Category Restriction; "
                    else:
                        reasons[hit_restricted] += "Category Restriction; "

                if exclude_rejected_matrix:
                    blocked_df = run_query("""
                        SELECT DISTINCT UPPER(TRIM(sku)) AS sku FROM inventory_item_profitability
                        WHERE UPPER(COALESCE(amazon_decision,'')) IN ('REJECT','NO','SKIP')
                          AND UPPER(COALESCE(takealot_decision,'')) IN ('REJECT','NO','SKIP')
                    """)
                    if not blocked_df.empty:
                        reasons[df["_sku"].isin(set(blocked_df["sku"].tolist()))] += "Rejected in Profitability Matrix; "

                df["exclusion_reason"] = reasons
                valid_df = df[df["exclusion_reason"] == ""].copy()
                excluded_df = df[df["exclusion_reason"] != ""].copy()
                disc_zeroed_count = int((valid_df["_stock_note"].str.contains(r"Discontinued \(<", regex=True)).sum())
                barcodes_ready_count = int((valid_df["_bc_single"] != "").sum())
                multi_pack_count = int((valid_df["_min_pack"] > 1).sum())

                s1, s2, s3, s4, s5, s6 = st.columns(6)
                s1.metric("Total Sheet Rows", f"{len(df):,}")
                s2.metric("Accepted SKUs", f"{len(valid_df):,}")
                s3.metric("Excluded Rows", f"{len(excluded_df):,}")
                s4.metric("Multi-Pack SKUs (>1)", f"{multi_pack_count:,}")
                s5.metric("Discontinued Zeroed (<Floor)", f"{disc_zeroed_count:,}")
                s6.metric("EAN Barcodes Ready", f"{barcodes_ready_count:,}")

                tab_ok, tab_ex = st.tabs(["✅ Accepted Items & Stock Overrides", "🚫 Excluded Rows"])
                with tab_ok:
                    st.dataframe(
                        valid_df[["_sku", "_title", "_cost", "_min_pack", "_raw_qty", "_final_qty", "_ct", "_dbn", "_jhb", "_bc_single", "_bc_inner", "_info", "_stock_note"]].head(300),
                        use_container_width=True,
                    )
                with tab_ex:
                    st.dataframe(
                        excluded_df[["_sku", "_title", "_cost", "_min_pack", "_raw_qty", "_info", "exclusion_reason"]].head(300),
                        use_container_width=True,
                    )

                if can_commit and st.button(f"💾 Commit {len(valid_df):,} Accepted SKUs to `inventory_items` ({commit_mode})", type="primary"):
                    records = [
                        {
                            "sku": str(r["_sku"]),
                            "bc": str(r["_bc_single"]) or None,
                            "inner_bc": str(r["_bc_inner"]) or None,
                            "case_bc": str(r["_bc_case"]) or None,
                            "title": str(r["_title"])[:255],
                            "cost": float(r["_cost"]),
                            "min_pack": int(r["_min_pack"]),
                            "qty": int(r["_final_qty"]),
                            "ct": int(r["_ct"]),
                            "dbn": int(r["_dbn"]),
                            "jhb": int(r["_jhb"]),
                        }
                        for _, r in valid_df.iterrows()
                    ]

                    with st.spinner(f"Bulk-committing {len(records):,} rows to `inventory_items`..."):
                        with engine.begin() as conn:
                            for i in range(0, len(records), 1000):
                                batch = records[i : i + 1000]
                                if commit_mode == "Full Upsert (Stock, Costs, Min Packs, Regional Stock & Barcodes)":
                                    conn.execute(
                                        text("""
                                            INSERT INTO inventory_items
                                                (item_code, sku, barcode, inner_barcode, case_barcode, description, title,
                                                 single_price_excl_vat, cost_price, sold_in_min_packs_only, quantity, stock_ct, stock_dbn, stock_jhb)
                                            VALUES (:sku, :sku, :bc, :inner_bc, :case_bc, :title, :title, :cost, :cost, :min_pack, :qty, :ct, :dbn, :jhb)
                                            ON DUPLICATE KEY UPDATE
                                                barcode = COALESCE(NULLIF(VALUES(barcode), ''), barcode),
                                                inner_barcode = COALESCE(NULLIF(VALUES(inner_barcode), ''), inner_barcode),
                                                case_barcode = COALESCE(NULLIF(VALUES(case_barcode), ''), case_barcode),
                                                description = COALESCE(NULLIF(VALUES(description), ''), description),
                                                title = COALESCE(NULLIF(VALUES(title), ''), title),
                                                single_price_excl_vat = VALUES(single_price_excl_vat),
                                                cost_price = VALUES(cost_price),
                                                sold_in_min_packs_only = VALUES(sold_in_min_packs_only),
                                                quantity = VALUES(quantity),
                                                stock_ct = VALUES(stock_ct),
                                                stock_dbn = VALUES(stock_dbn),
                                                stock_jhb = VALUES(stock_jhb)
                                        """),
                                        batch,
                                    )
                                elif commit_mode == "Update Stock Quantities Only":
                                    conn.execute(
                                        text("UPDATE inventory_items SET quantity = :qty, stock_ct = :ct, stock_dbn = :dbn, stock_jhb = :jhb WHERE sku = :sku"),
                                        batch,
                                    )
                                else:
                                    conn.execute(
                                        text("""
                                            UPDATE inventory_items
                                            SET single_price_excl_vat = :cost,
                                                cost_price = :cost,
                                                sold_in_min_packs_only = :min_pack,
                                                barcode = COALESCE(NULLIF(:bc, ''), barcode),
                                                inner_barcode = COALESCE(NULLIF(:inner_bc, ''), inner_barcode),
                                                case_barcode = COALESCE(NULLIF(:case_bc, ''), case_bc)
                                            WHERE sku = :sku
                                        """),
                                        batch,
                                    )

                    get_cached_master_skus.clear()
                    log_audit(
                        current_user,
                        "SUPPLIER_UPLOAD",
                        f"Imported {uploaded.name} ({commit_mode})",
                        f"Committed: {len(records)}, Multi-Pack (>1): {multi_pack_count}, Barcodes: {barcodes_ready_count}",
                        len(records),
                    )
                    st.success(f"Bulk-committed **{len(records):,}** SKUs (including **{multi_pack_count:,}** multi-pack items) and bound **{barcodes_ready_count:,}** EAN barcodes into `inventory_items`!")