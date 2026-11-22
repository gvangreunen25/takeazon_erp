#!/usr/bin/env python3
"""Applies whole-app UX consistency (schema-safe SellerFlex helpers + clean index-free tables) across all modules."""

from pathlib import Path
import re
import shutil

ROOT = Path("/Users/gjvangreunen/workspace/takeazon_erp")
MODULES_DIR = ROOT / "modules"


def polish_all_modules():
    for py_file in sorted(MODULES_DIR.glob("*.py")):
        if py_file.name.startswith("__"):
            continue
        original = py_file.read_text(encoding="utf-8")
        updated = original

        # 1. Ensure `hide_index=True` on st.dataframe calls that don't already specify it
        def _add_hide_index(match: re.Match) -> str:
            call_body = match.group(1)
            if "hide_index" in call_body:
                return match.group(0)
            stripped = call_body.rstrip()
            if stripped.endswith(","):
                return f"st.dataframe({stripped} hide_index=True)"
            return f"st.dataframe({stripped}, hide_index=True)"

        updated = re.sub(r"st\.dataframe\(([^()]+)\)", _add_hide_index, updated)

        # 2. Replace hardcoded `available_qty` / `sync_status` SQL in ecomm_research.py & profitability.py
        if py_file.name == "ecomm_research.py" and "stage_items_to_sellerflex" not in updated:
            updated = "from core.ui_helpers import stage_items_to_sellerflex\n" + updated
            sf_insert_pattern = re.compile(
                r'with engine\.begin\(\) as conn:\s*'
                r'rows_to_insert = \[\s*'
                r'\{"sku": str\(r\["SKU"\]\)\.strip\(\), "qty": int\(r\["Qty"\]\)\}\s*'
                r'for _, r in (?:updated_flex|flex_df)\.iterrows\(\)\s*'
                r'\]\s*'
                r'for i in range\(0, len\(rows_to_insert\), 500\):\s*'
                r'conn\.execute\(\s*'
                r'text\("""\s*'
                r'INSERT INTO sellerflex_inventory \(sku, available_qty, sync_status\)\s*'
                r'VALUES \(:sku, :qty, \'PENDING\'\)\s*'
                r'ON DUPLICATE KEY UPDATE available_qty = VALUES\(available_qty\), sync_status = \'PENDING\'\s*'
                r'"""\),\s*'
                r'rows_to_insert\[i : i \+ 500\],\s*'
                r'\)',
                re.DOTALL,
            )
            updated = sf_insert_pattern.sub(
                'stage_items_to_sellerflex([(str(r["SKU"]).strip(), int(r["Qty"])) for _, r in flex_df.iterrows()])',
                updated,
            )

        if py_file.name == "profitability.py" and "get_sellerflex_schema" not in updated:
            updated = "from core.ui_helpers import get_sellerflex_schema\n" + updated
            old_staged_query = re.compile(
                r'staged_df = run_query\("""\s*'
                r'SELECT\s*'
                r's\.sku,\s*'
                r'COALESCE\(s\.available_qty, 0\) AS quantity,\s*'
                r'COALESCE\(NULLIF\(p\.amazon_actual_price, 0\), NULLIF\(p\.amazon_rec_price, 0\), 0\) AS price,\s*'
                r'NULL AS list_price\s*'
                r'FROM sellerflex_inventory s\s*'
                r'LEFT JOIN inventory_item_profitability p ON TRIM\(p\.sku\) = TRIM\(s\.sku\)\s*'
                r'WHERE s\.sync_status = \'PENDING\'\s*'
                r'ORDER BY s\.sku\s*'
                r'LIMIT 2000\s*'
                r'"""\)',
                re.DOTALL,
            )
            replacement_staged = '''_sf_schema = get_sellerflex_schema()
            _sf_sku = _sf_schema["sku_col"] or "sku"
            _sf_qty = f"COALESCE(s.`{_sf_schema['qty_col']}`, 0)" if _sf_schema["qty_col"] else "0"
            _sf_where = f"WHERE UPPER(COALESCE(s.`{_sf_schema['status_col']}`, '')) = 'PENDING'" if _sf_schema["status_col"] else ""
            staged_df = run_query(f"""
                SELECT
                    s.`{_sf_sku}` AS sku,
                    {_sf_qty} AS quantity,
                    COALESCE(NULLIF(p.amazon_actual_price, 0), NULLIF(p.amazon_rec_price, 0), 0) AS price,
                    NULL AS list_price
                FROM sellerflex_inventory s
                LEFT JOIN inventory_item_profitability p ON TRIM(p.sku) = TRIM(s.`{_sf_sku}`)
                {_sf_where}
                ORDER BY s.`{_sf_sku}`
                LIMIT 2000
            """)'''
            updated = old_staged_query.sub(replacement_staged, updated, count=1)

        if updated != original:
            shutil.copy2(py_file, py_file.with_suffix(".py.bak"))
            py_file.write_text(updated, encoding="utf-8")
            print(f"Streamlined `{py_file.relative_to(ROOT)}`.")


if __name__ == "__main__":
    polish_all_modules()