from __future__ import annotations

import io
from typing import Any, Dict, List

import numpy as np
import openpyxl
import pandas as pd

from core.database import run_query


def parse_qty(val: Any) -> float:
    if pd.isna(val):
        return np.nan
    s = str(val).strip()
    if not s or s.lower() in ("nan", "none", "null"):
        return np.nan
    if "over 500" in s.lower():
        return 501.0
    try:
        return float(s)
    except ValueError:
        return np.nan


def is_excluded_consumable(row: pd.Series) -> bool:
    sku = str(row.get("Item", "")).strip()
    desc = str(row.get("Description", "")).upper()

    if sku.startswith(("903-", "904-", "905-")):
        return True
    if sku == "072-020373":
        return False

    sweet_candy_kws = [
        "SWEET", "CANDY", "CHOC", "LOLLY", "LOLLIPOP", "TOFFEE",
        "GUMMY", "GUMMIES", "JELLY BEAN", "MARSHMALLOW", "SHERBET",
        "LICORICE", "LIQUORICE", "FUDGE", "CARAMEL", "NOUGAT",
        "MINT ", "MINTS", "BUBBLEGUM", "CHEWING GUM", "SNACK ",
    ]
    if any(k in desc for k in sweet_candy_kws):
        return True

    pet_food_kws = [
        "DOG FOOD", "CAT FOOD", "PET FOOD", "DOG TREAT", "CAT TREAT",
        "PET TREAT", "CHEW COW", "RAWHIDE", "DENTAL STICK", "DOG BISCUIT",
        "CATNIP", "CAT GRASS", "BIRD SEED", "FISH FOOD", "KIBBLE",
        "PET SNACK", "DOG SNACK", "CAT SNACK",
    ]
    if any(k in desc for k in pet_food_kws):
        return True

    return False


def classify_sa_annual_wave(desc: str) -> tuple[str, str, str]:
    d = str(desc).upper()

    # W01 (Jan): Back-to-School & Term 1 Prep
    bts_kws = [
        "SCHOOL", "STATIONERY", "LUNCH BOX", "LUNCH-BOX", "LUNCHBOX",
        "WATER BOTTLE", "BOTTLE ", "BACKPACK", "SCHOOL BAG", "PENCIL CASE",
        "PENCIL-CASE", "PENCIL", "PEN ", "PENS ", "CRAYON", "MARKER",
        "HIGHLIGHTER", "ERASER", "SHARPENER", "RULER", "SCISSOR", "GLUE",
        "PRITT", "BIC", "NOTEBOOK", "EXERCISE BOOK", "EXAM PAD", "FILE ",
        "DISPLAY-BOOK", "FOLDER", "BINDER", "CALCULATOR", "GEOMETRY",
        "COMPASS", "PROTRACTOR", "CHALK", "BOOK COVER", "LABELS SELF ADHESIVE",
        "NAME TAG", "LANYARD", "WHITEBOARD", "WHITE BOARD", "CLIPBOARD",
        "BOOK HARD-COVER", "BOOK SOFT-COVER", "BOOK NOTE", "BOOK DRAWING",
        "ENVELOPES",
    ]
    if "GLUE DOTS" not in d and "BALLOON" not in d and any(k in d for k in bts_kws):
        return (
            "W01 (Jan): Back-to-School & Term 1 Prep",
            "Dec 26 - Jan 25",
            "The Crazy Store / Makro / Takealot",
        )

    # W02 (Feb): Valentine's Day & Romantic Gifting
    if any(k in d for k in ["VALENTINE", "HEART ", "ROMANTIC", "LOVE ", "ROSE ", "TEDDY", "COUPLE", "DATE NIGHT"]):
        return (
            "W02 (Feb): Valentine's Day & Romantic Gifting",
            "Jan 25 - Feb 14",
            "The Crazy Store / Takealot",
        )

    # W03 (Mar-Apr): Easter & Autumn School Holidays
    if any(k in d for k in ["EASTER", "BUNNY", "RABBIT", "EGG ", "EGGS", "BASKET", "HUNT"]):
        return (
            "W03 (Mar-Apr): Easter & Autumn School Holidays",
            "Mar 1 - Apr 15",
            "The Crazy Store / Makro",
        )

    # W04 (May-Jun): Mother's/Father's Day & SA Winter
    if any(
        k in d
        for k in [
            "MOTHER'S DAY", "MOTHERS DAY", "FATHER'S DAY", "FATHERS DAY",
            "MUG", "COFFEE", "TEA ", "BLANKET", "FLEECE", "GLOVES", "BEANIE",
            "SCARF", "SOCKS", "SLIPPER", "HOT WATER", "FLASK", "THERMOS",
            "SOUP", "BEARD", "SHAVING", "TOOL", "SCREWDRIVER", "WRENCH", "TORCH",
        ]
    ):
        return (
            "W04 (May-Jun): Mother's/Father's Day & SA Winter",
            "Apr 25 - Jul 15",
            "Makro / The Crazy Store / Takealot",
        )

    # W05 (Jul-Aug): Lighting/Power & Women's Month (9 Aug)
    if any(
        k in d
        for k in [
            "SOLAR", "LED", "LANTERN", "EMERGENCY", "BATTERY", "BATTERIES",
            "EXTENSION", "PLUG", "ADAPTOR", "COSMETIC", "MAKEUP", "MIRROR",
            "HAIR", "NAIL", "FRAGRANCE", "LIP BALM", "JEWELLERY", "SPA ",
        ]
    ):
        return (
            "W05 (Jul-Aug): Lighting/Power & Women's Month",
            "Jul 1 - Aug 31",
            "Takealot / Makro / The Crazy Store",
        )

    # W06 (Sep-Oct): Spring Clean, Heritage Day Braai & Halloween
    if any(
        k in d
        for k in [
            "HALLOWEEN", "SPOOKY", "PUMPKIN", "SKELETON", "WITCH", "COSTUME",
            "MASK ", "FACE PAINT", "BRAAI", "BBQ", "SKEWER", "TONGS", "STORAGE",
            "ORGANISER", "ORGANIZER", "HANGER", "PEGS", "CLEANING", "MICROFIBRE",
            "SPONGE", "BRUSH", "GARDEN", "PLANT", "POT ",
        ]
    ):
        return (
            "W06 (Sep-Oct): Spring Clean, Braai & Halloween",
            "Sep 1 - Oct 31",
            "Makro / The Crazy Store / Takealot",
        )

    # W07 (Nov): Black Friday Tech & Kitchen Upgrades
    if any(
        k in d
        for k in [
            "USB", "CABLE", "EARPHONE", "HEADPHONE", "SPEAKER", "PHONE",
            "MOBILE ", "HOLDER", "HILLHOUSE", "AIR FRYER", "KITCHEN-GADGET",
            "KITCHEN-TOOL", "CUTTING-BOARD", "FOODCOVER", "GRATER", "PEELER",
        ]
    ):
        return (
            "W07 (Nov): Black Friday Tech & Kitchen Upgrades",
            "Nov 1 - Nov 30",
            "Takealot / Makro / The Crazy Store",
        )

    # W08 (Dec): Christmas Decor, Wrap & Kids Gifting
    if any(
        k in d
        for k in [
            "XMAS", "SANTA", "REINDEER", "BAUBLE", "TINSEL", "CRACKER",
            "STOCKING", "ELF", "NATIVITY", "WREATH", "GIFT-BAG", "GIFT BAG",
            "PAPER TISSUE", "CARD & ENVELOPE", "GIFT TAG", "LEGO", "HOT WHEELS",
            "BARBIE", "DISNEY", "MARVEL", "PLAY-TENT", "DOLL", "PUZZLE", "GAME",
            "DRESS UP", "SLIME", "PLUSH", "TOY", "VEHICLE", "CAR ",
        ]
    ):
        return (
            "W08 (Dec): Christmas Decor, Wrap & Kids Gifting",
            "Nov 20 - Dec 24",
            "The Crazy Store / Takealot / Makro",
        )

    # W09 (Dec-Jan): SA Summer Pool, Beach & Holiday Travel
    if any(
        k in d
        for k in [
            "INTEX", "BESTWAY", "POOL", "SWIM", "GOGGLES", "ARMBAND",
            "ARM BAND", "INFLATABLE", "BEACH", "FRISBEE", "BUBBLE", "PARTY",
            "BALLOON", "GLOW STICK", "SUNGLASSES", "COOLER", "PICNIC",
            "BOOK ACTIVITY", "BOOK COLOURING", "ART+CRAFT", "ART&CRAFT",
            "STICKER", "TRAVEL",
        ]
    ):
        return (
            "W09 (Dec-Jan): SA Summer Pool, Beach & Holiday Travel",
            "Dec 1 - Jan 20",
            "Makro / Takealot / The Crazy Store",
        )

    # W10 (All Year): Evergreen Home, Pet & Party Staples
    return (
        "W10 (All Year): Evergreen Home, Pet & Party Staples",
        "Jan - Dec (Year-Round)",
        "The Crazy Store / Makro / Takealot",
    )


def assign_multichannel_strategy(row: pd.Series) -> tuple[str, str, str, str]:
    qty = float(row.get("Qty_num") if pd.notna(row.get("Qty_num")) else 0.0)
    min_pack_val = row.get("Sold in Min Pack Only")
    has_min_pack = pd.notna(min_pack_val) and str(min_pack_val).strip() not in ("", "0", "1", "None", "nan")

    if has_min_pack:
        return (
            "Takealot Multi-Pack + Takeazon Case-Pack (Amazon Bundle Only)",
            "No (Unless Bundled)",
            "Yes (Bulk Multi-Pack)",
            "Yes (Case/Bundle)",
        )
    if qty > 500:
        return (
            "ALL 3 CHANNELS: Amazon (Seller Flex + FBA), Takealot (DC + Leadtime) & Takeazon",
            "Yes (Seller Flex + FBA)",
            "Yes (DC Stock + Leadtime)",
            "Yes (Single + Bundle)",
        )
    if qty > 250:
        return (
            "Takealot Leadtime + Takeazon Direct + Amazon FBA Small-Batch",
            "FBA Test Batch Only",
            "Yes (Leadtime)",
            "Yes (Single + Bundle)",
        )
    return (
        "Takeazon Direct + Takealot Leadtime (Monitor Stock)",
        "Hold (Below 250 Buffer)",
        "Yes (Leadtime)",
        "Yes (Direct)",
    )


def priority_score(row: pd.Series) -> int:
    w = str(row.get("SA_Annual_Wave", ""))
    d = str(row.get("Description", "")).upper()
    if w.startswith(("W07", "W08", "W09", "W01")):
        return 1
    if any(b in d for b in ["HILLHOUSE", "BEST BUDS", "INTEX", "BESTWAY", "LEGO", "HOT WHEELS", "BARBIE", "DISNEY"]):
        return 2
    return 3


def fetch_db_pricing_and_profitability(sku_list: List[str]) -> pd.DataFrame:
    """Queries `inventory_item_profitability` for Amazon & Takealot recommended/actual prices."""
    if not sku_list:
        return pd.DataFrame(columns=["SKU", "Amazon_Rec_Price", "Takealot_Rec_Price"])

    cols_df = run_query("SHOW COLUMNS FROM inventory_item_profitability")
    if cols_df.empty:
        return pd.DataFrame(columns=["SKU", "Amazon_Rec_Price", "Takealot_Rec_Price"])

    cols = cols_df["Field"].astype(str).tolist()
    sku_col = next((c for c in cols if c.lower() in ("sku", "item", "item_code", "seller_sku", "product_sku")), None)
    amz_col = next((c for c in cols if any(k in c.lower() for k in ("amazon_rec", "amazon_actual", "rec_price", "amazon_price"))), None)
    tal_col = next((c for c in cols if any(k in c.lower() for k in ("takealot_rec", "takealot_actual", "takealot_price", "tal_price"))), None)

    if not sku_col or not amz_col:
        return pd.DataFrame(columns=["SKU", "Amazon_Rec_Price", "Takealot_Rec_Price"])

    select_parts = [f"TRIM(`{sku_col}`) AS SKU", f"`{amz_col}` AS Amazon_Rec_Price"]
    if tal_col:
        select_parts.append(f"`{tal_col}` AS Takealot_Rec_Price")

    frames = []
    clean_skus = list({str(s).strip() for s in sku_list if str(s).strip()})
    for i in range(0, len(clean_skus), 500):
        batch = tuple(clean_skus[i : i + 500])
        sql = f"SELECT {', '.join(select_parts)} FROM inventory_item_profitability WHERE TRIM(`{sku_col}`) IN :skus"
        part = run_query(sql, params={"skus": batch}, expanding_keys=["skus"])
        if not part.empty:
            frames.append(part)

    if not frames:
        return pd.DataFrame(columns=["SKU", "Amazon_Rec_Price"] + (["Takealot_Rec_Price"] if tal_col else []))

    price_df = pd.concat(frames, ignore_index=True)
    price_df["SKU"] = price_df["SKU"].astype(str).str.strip()
    price_df["Amazon_Rec_Price"] = pd.to_numeric(price_df["Amazon_Rec_Price"], errors="coerce")
    if "Takealot_Rec_Price" in price_df.columns:
        price_df["Takealot_Rec_Price"] = pd.to_numeric(price_df["Takealot_Rec_Price"], errors="coerce")
    return price_df.drop_duplicates(subset=["SKU"], keep="last")


def load_mysql_as_research_inputs() -> tuple[pd.DataFrame, pd.DataFrame]:
    """Builds Seller1 (existing Amazon listings) and Seller2 (full catalog) DataFrames directly from MySQL."""
    s1_raw = run_query("""
        SELECT
            a.sku AS SKU,
            COALESCE(NULLIF(a.item_name, ''), a.sku) AS `Product Name`,
            COALESCE(a.quantity, 0) AS Qty,
            COALESCE(NULLIF(p.amazon_rec_price, 0), a.current_price, 0) AS `Amazon Price`
        FROM amazon_active_listings a
        LEFT JOIN inventory_item_profitability p ON TRIM(p.sku) = TRIM(a.sku)
    """)
    if not s1_raw.empty:
        s1_raw["20% Dicount Price"] = (pd.to_numeric(s1_raw["Amazon Price"], errors="coerce").fillna(0) * 0.8).round(2)
    else:
        s1_raw = pd.DataFrame(columns=["SKU", "Product Name", "Qty", "Amazon Price", "20% Dicount Price"])

    inv_cols = set(run_query("SHOW COLUMNS FROM inventory_items")["Field"].astype(str).tolist())
    min_pack_expr = (
        "CASE WHEN COALESCE(sold_in_min_packs_only, 1) > 1 THEN sold_in_min_packs_only ELSE NULL END"
        if "sold_in_min_packs_only" in inv_cols
        else "NULL"
    )

    s2_raw = run_query(f"""
        SELECT
            COALESCE(NULLIF(sku, ''), item_code) AS Item,
            COALESCE(NULLIF(description, ''), NULLIF(title, ''), sku) AS Description,
            CASE WHEN COALESCE(quantity, 0) > 500 THEN 'Over 500 Units' ELSE CAST(COALESCE(quantity, 0) AS CHAR) END AS Quantity,
            {min_pack_expr} AS `Sold in Min Pack Only`
        FROM inventory_items
        WHERE COALESCE(NULLIF(sku, ''), item_code) IS NOT NULL
    """)
    return s1_raw, s2_raw


def run_multichannel_research_pipeline(
    df1_in: pd.DataFrame,
    df2_in: pd.DataFrame,
    min_stock_threshold: int = 100,
    amazon_flex_target_count: int = 1000,
    discount_pct: float = 0.20,
    exclude_consumables: bool = True,
) -> Dict[str, Any]:
    """Executes the full 10-wave retail research, Seller Flex 1000 selection, and stock audit pipeline."""
    df1 = df1_in.copy()
    df2 = df2_in.copy()

    df1.columns = [str(c).strip() for c in df1.columns]
    df2.columns = [str(c).strip() for c in df2.columns]

    # Drop any pre-existing Qty_num to avoid duplicate column conflicts
    df1 = df1.drop(columns=["Qty_num"], errors="ignore")
    df2 = df2.drop(columns=["Qty_num"], errors="ignore")

    if "SKU" not in df1.columns and len(df1.columns) > 0:
        df1 = df1.rename(columns={df1.columns[0]: "SKU"})
    if "Item" not in df2.columns:
        sku_alt = next((c for c in df2.columns if c.lower() in ("sku", "item_code", "product_code")), df2.columns[0])
        df2 = df2.rename(columns={sku_alt: "Item"})
    if "Description" not in df2.columns:
        desc_alt = next(
            (c for c in df2.columns if "desc" in c.lower() or "title" in c.lower() or "name" in c.lower()),
            df2.columns[min(1, len(df2.columns) - 1)],
        )
        df2 = df2.rename(columns={desc_alt: "Description"})
    if "Quantity" not in df2.columns:
        qty_alt = next(
            (c for c in df2.columns if "qty" in c.lower() or "quant" in c.lower() or "stock" in c.lower() or "nat" in c.lower()),
            None,
        )
        df2["Quantity"] = df2[qty_alt] if qty_alt else 0
    if "Sold in Min Pack Only" not in df2.columns:
        mp_alt = next((c for c in df2.columns if "min pack" in c.lower() or "min_pack" in c.lower()), None)
        df2["Sold in Min Pack Only"] = df2[mp_alt] if mp_alt else np.nan

    df1["SKU"] = df1["SKU"].astype(str).str.strip()
    df2["Item"] = df2["Item"].astype(str).str.strip()
    df2["Description"] = df2["Description"].astype(str).str.strip()
    df2["Qty_num"] = pd.Series([parse_qty(v) for v in df2["Quantity"]], index=df2.index, dtype="float64")

    df2["Is_Excluded"] = df2.apply(is_excluded_consumable, axis=1) if exclude_consumables else False
    df2_clean = df2[~df2["Is_Excluded"]].copy()

    if df2_clean.empty:
        return {
            "annual_wave_summary": pd.DataFrame(),
            "amazon_flex_import": pd.DataFrame(),
            "multichannel_master": pd.DataFrame(),
            "audit": pd.DataFrame(),
            "excluded_count": int(df2["Is_Excluded"].sum()),
            "discount_factor": 1.0 - discount_pct,
        }

    wave_info = df2_clean["Description"].apply(lambda x: pd.Series(classify_sa_annual_wave(x)))
    df2_clean[["SA_Annual_Wave", "Peak_Window", "Retail_Backup"]] = wave_info

    channel_info = df2_clean.apply(lambda r: pd.Series(assign_multichannel_strategy(r)), axis=1)
    df2_clean[["Channel_Strategy", "Amazon_Fit", "Takealot_Fit", "Takeazon_Fit"]] = channel_info
    df2_clean["Already_In_Seller1"] = df2_clean["Item"].isin(set(df1["SKU"].tolist()))

    # Eligible items above stock threshold
    eligible_all = df2_clean[df2_clean["Qty_num"].fillna(0) > float(min_stock_threshold)].copy()
    db_prices_df = fetch_db_pricing_and_profitability(eligible_all["Item"].tolist())
    if not db_prices_df.empty:
        eligible_all = eligible_all.merge(db_prices_df, left_on="Item", right_on="SKU", how="left")
    else:
        eligible_all["Amazon_Rec_Price"] = np.nan

    disc_mult = max(0.0, 1.0 - float(discount_pct))
    disc_col_label = f"Amazon_{int(round(discount_pct * 100))}pct_Discount"
    eligible_all[disc_col_label] = (pd.to_numeric(eligible_all["Amazon_Rec_Price"], errors="coerce") * disc_mult).round(2)

    # Build Amazon Seller Flex Import Sheet
    flex_candidates = eligible_all[
        (~eligible_all["Already_In_Seller1"]) & (eligible_all["Sold in Min Pack Only"].isna())
    ].copy()
    flex_candidates["Priority"] = flex_candidates.apply(priority_score, axis=1) if not flex_candidates.empty else pd.Series(dtype=int)

    tier1_500 = flex_candidates[
        (flex_candidates["Quantity"].astype(str).str.strip() == "Over 500 Units")
        & (flex_candidates["Priority"] == 1)
    ].sort_values(by=["Item"])

    needed_from_tier2 = max(0, int(amazon_flex_target_count) - len(tier1_500))
    tier2_fill = (
        flex_candidates[~flex_candidates["Item"].isin(tier1_500["Item"])]
        .sort_values(by=["Priority", "Qty_num", "Item"], ascending=[True, False, True])
        .head(needed_from_tier2)
    )

    selected_flex = pd.concat([tier1_500, tier2_fill], ignore_index=True).head(int(amazon_flex_target_count))

    amazon_flex_import = pd.DataFrame({
        "SKU": selected_flex["Item"].values,
        "Product Name": selected_flex["Description"].values,
        "Qty": np.minimum(selected_flex["Qty_num"].fillna(0).astype(int), 500),
        "Sold In Min Pack Sizes Only": selected_flex["Sold in Min Pack Only"].values,
        "Amazon Price": selected_flex["Amazon_Rec_Price"].values,
        "20% Dicount Price": selected_flex[disc_col_label].values,
        "SA_Annual_Wave (Reference)": selected_flex["SA_Annual_Wave"].values,
        "Peak_Window (Reference)": selected_flex["Peak_Window"].values,
        "Channel_Strategy (Reference)": selected_flex["Channel_Strategy"].values,
        "Retail_Backup (Reference)": selected_flex["Retail_Backup"].values,
    })

    # Full-Year 10-Wave Summary (computed cleanly via helper flags without index lookups)
    if not eligible_all.empty:
        eligible_all["_is_over_500_single"] = (
            (eligible_all["Quantity"].astype(str).str.strip() == "Over 500 Units")
            & (eligible_all["Sold in Min Pack Only"].isna())
        ).astype(int)
        eligible_all["_is_mid_single"] = (
            (eligible_all["Qty_num"].fillna(0) <= 500)
            & (eligible_all["Sold in Min Pack Only"].isna())
        ).astype(int)
        eligible_all["_is_min_pack"] = eligible_all["Sold in Min Pack Only"].notna().astype(int)
        eligible_all["_is_priced"] = eligible_all["Amazon_Rec_Price"].notna().astype(int)
        eligible_all["_is_in_s1"] = eligible_all["Already_In_Seller1"].astype(int)

        annual_wave_summary = (
            eligible_all.groupby(["SA_Annual_Wave", "Peak_Window"], as_index=False)
            .agg(
                Total_Eligible_Over_Threshold=("Item", "count"),
                Over_500_SingleUnit_AllChannels=("_is_over_500_single", "sum"),
                Stock_Threshold_to_500_SingleUnit=("_is_mid_single", "sum"),
                Min_Pack_Case_SKUs=("_is_min_pack", "sum"),
                Priced_In_DB=("_is_priced", "sum"),
                Already_In_Seller1=("_is_in_s1", "sum"),
            )
        )
    else:
        annual_wave_summary = pd.DataFrame()

    master_cols = [
        "Item", "Description", "SA_Annual_Wave", "Peak_Window", "Quantity",
        "Sold in Min Pack Only", "Channel_Strategy", "Amazon_Fit", "Takealot_Fit",
        "Takeazon_Fit", "Amazon_Rec_Price", disc_col_label, "Retail_Backup", "Already_In_Seller1",
    ]
    if "Takealot_Rec_Price" in eligible_all.columns:
        master_cols.insert(12, "Takealot_Rec_Price")

    eligible_sorted = eligible_all.sort_values(by=["SA_Annual_Wave", "Qty_num", "Item"], ascending=[True, False, True])
    multichannel_master = eligible_sorted[[c for c in master_cols if c in eligible_sorted.columns]].copy()

    # Audit existing Seller1 stock levels
    df2_lookup = df2[["Item", "Quantity", "Qty_num", "Is_Excluded"]].drop_duplicates(subset=["Item"], keep="first")
    audit = df1.merge(
        df2_lookup,
        left_on="SKU",
        right_on="Item",
        how="left",
    )
    qty_num_audit = pd.to_numeric(audit.get("Qty_num"), errors="coerce").fillna(0.0)
    is_excl_audit = audit.get("Is_Excluded", pd.Series(False, index=audit.index)).fillna(False).astype(bool)

    audit["Stock_Status"] = np.where(
        is_excl_audit,
        "EXCLUDED CONSUMABLE (Review)",
        np.where(
            qty_num_audit <= float(min_stock_threshold),
            f"ALERT: <={min_stock_threshold} Units (Restock/Backup Needed)",
            f"OK (>{min_stock_threshold} Units)",
        ),
    )
    audit["_sort_qty"] = qty_num_audit
    audit = audit.sort_values(by=["_sort_qty", "SKU"], na_position="first")

    desired_audit_cols = ["SKU", "Product Name", "Qty", "Quantity", "Stock_Status", "Amazon Price", "20% Dicount Price"]
    audit_view = audit[[c for c in desired_audit_cols if c in audit.columns]].copy()

    return {
        "annual_wave_summary": annual_wave_summary,
        "amazon_flex_import": amazon_flex_import,
        "multichannel_master": multichannel_master,
        "audit": audit_view,
        "excluded_count": int(df2["Is_Excluded"].sum()),
        "discount_factor": disc_mult,
    }


def build_research_excel_bytes(results: Dict[str, Any]) -> bytes:
    """Exports the 4-tab research workbook in memory with live discount formulas and auto-width columns."""
    out_buf = io.BytesIO()
    with pd.ExcelWriter(out_buf, engine="openpyxl") as writer:
        results["annual_wave_summary"].to_excel(writer, sheet_name="SA_Annual_10_Waves", index=False)
        results["amazon_flex_import"].to_excel(writer, sheet_name="Amazon_Flex_1000_Import", index=False)
        results["multichannel_master"].to_excel(writer, sheet_name="All_Channels_Master", index=False)
        results["audit"].to_excel(writer, sheet_name="Seller1_Stock_Audit", index=False)

    out_buf.seek(0)
    wb = openpyxl.load_workbook(out_buf)
    disc_factor = float(results.get("discount_factor", 0.8))

    if "Amazon_Flex_1000_Import" in wb.sheetnames:
        ws_flex = wb["Amazon_Flex_1000_Import"]
        for r in range(2, ws_flex.max_row + 1):
            if ws_flex[f"E{r}"].value is not None:
                ws_flex[f"F{r}"] = f"=E{r}*{disc_factor:.4f}"

    for name in wb.sheetnames:
        sheet = wb[name]
        for col in sheet.columns:
            width = max(len(str(c.value)) if c.value is not None else 0 for c in col)
            sheet.column_dimensions[col[0].column_letter].width = min(width + 4, 52)

    final_buf = io.BytesIO()
    wb.save(final_buf)
    return final_buf.getvalue()