import streamlit as st
import streamlit.components.v1 as components
from sqlalchemy import text

from core.database import engine, execute_sql, get_cached_active_bins, get_cached_master_skus, log_audit, run_query
from core.secrets import get_db_secret
from core.zebra import ZEBRA_LABEL_PRESETS, build_label_canvas_and_png, generate_zebra_zpl, render_client_browser_printer_widget


def lookup_barcode_item(raw_scan: str):
    cleaned = "".join(ch for ch in str(raw_scan or "").strip() if ch.isprintable()).strip()
    if not cleaned:
        return None, ""

    cands = tuple({
        cleaned,
        cleaned.upper(),
        cleaned.lstrip("0") or cleaned,
        cleaned.zfill(13) if cleaned.isdigit() and len(cleaned) in (11, 12) else cleaned,
    })

    df = run_query(
        """
        SELECT sku, item_code, barcode AS item_barcode, title, quantity, cost_price, single_price_excl_vat
        FROM inventory_items
        WHERE sku IN :cands OR item_code IN :cands OR barcode IN :cands OR inner_barcode IN :cands OR case_barcode IN :cands
        LIMIT 1
        """,
        {"cands": cands},
        expanding_keys=["cands"],
    )

    if df.empty:
        df = run_query(
            """
            SELECT i.sku, i.item_code, i.barcode AS item_barcode, i.title, i.quantity, i.cost_price, i.single_price_excl_vat
            FROM inventory_items i
            LEFT JOIN takealot_listings t ON t.sku = i.sku
            LEFT JOIN amazon_active_listings a ON a.sku = i.sku
            WHERE t.barcode IN :cands OR a.asin IN :cands
            LIMIT 1
            """,
            {"cands": cands},
            expanding_keys=["cands"],
        )

    return (None, cleaned) if df.empty else (df.iloc[0], cleaned)


def render(auth_user: dict):
    current_user = auth_user["username"]
    st.title("🔫 Handheld Barcode Scanner (Rapid Scan In / Scan Out)")
    st.caption(
        "Matches scanned barcodes across indexed `inventory_items` (`sku`, `item_code`, `barcode`, `inner_barcode`, `case_barcode`), "
        "`takealot_listings.barcode`, and `amazon_active_listings.asin`."
    )

    bin_list = get_cached_active_bins()

    @st.fragment
    def render_scanner_fragment():
        c1, c2, c3, c4 = st.columns([1.2, 1.2, 0.8, 1.1])
        c1.radio("Scan Mode", ["SCAN IN (+)", "SCAN OUT (-)"], horizontal=True, key="scan_dir")
        c2.selectbox("Active Warehouse Bin", bin_list, key="scan_bin")
        rapid_one_unit = c3.checkbox("1-Scan = 1 Unit", value=True, key="scan_rapid_1")
        if not rapid_one_unit:
            c3.number_input("Qty per Scan", min_value=1, value=1, step=1, key="scan_qty")
        auto_print_on_scan = c4.checkbox("Auto-Print Zebra Label on Scan In", value=False, key="scan_auto_print")

        c5, c6 = st.columns(2)
        c5.text_input("PO / GRN / Order Ref (Locked)", placeholder="e.g., GRN-20261119 or AMZ-408...", key="scan_ref")
        c6.text_input("Lot / Batch Number (Optional)", key="scan_lot")

        st.session_state.setdefault("last_scan_feedback", None)
        st.session_state.setdefault("unmapped_barcode", "")

        def execute_scan_for_code(raw_code_input: str):
            row_item, cleaned_code = lookup_barcode_item(raw_code_input)
            if not cleaned_code:
                return

            if row_item is None:
                st.session_state["unmapped_barcode"] = cleaned_code
                st.session_state["last_scan_feedback"] = {
                    "ok": False,
                    "msg": f"Scanned barcode **`{cleaned_code}`** was not found in `inventory_items` (`sku`, `item_code`, `barcode`, `inner_barcode`, `case_barcode`), Takealot Barcode, or Amazon ASIN. Link it below.",
                }
                return

            st.session_state["unmapped_barcode"] = ""
            sku = str(row_item["sku"])
            title = str(row_item["title"] or sku)
            prev_qty = int(row_item["quantity"] or 0)
            cost_price = float(row_item["cost_price"] or 0.0)
            cur_dir = st.session_state.get("scan_dir", "SCAN IN (+)")
            cur_bin = st.session_state.get("scan_bin", bin_list[0])
            cur_qty = 1 if st.session_state.get("scan_rapid_1", True) else int(st.session_state.get("scan_qty", 1))
            cur_ref = st.session_state.get("scan_ref", "")
            cur_lot = st.session_state.get("scan_lot", "")

            signed_qty = cur_qty if cur_dir == "SCAN IN (+)" else -cur_qty
            new_qty = max(0, prev_qty + signed_qty)

            with engine.begin() as conn:
                conn.execute(text("UPDATE inventory_items SET quantity = :nq WHERE sku = :sku"), {"nq": new_qty, "sku": sku})
                conn.execute(
                    text("""
                        INSERT INTO bin_inventory (bin_code, sku, qty_on_hand, lot_number)
                        VALUES (:bin, :sku, GREATEST(0, :delta), :lot)
                        ON DUPLICATE KEY UPDATE qty_on_hand = GREATEST(0, qty_on_hand + :delta)
                    """),
                    {"bin": cur_bin, "sku": sku, "delta": signed_qty, "lot": cur_lot or None},
                )
                conn.execute(
                    text("""
                        INSERT INTO channel_stock_levels (sku, total_wms_stock, primary_bin)
                        VALUES (:sku, :nq, :bin)
                        ON DUPLICATE KEY UPDATE total_wms_stock = :nq, primary_bin = :bin
                    """),
                    {"sku": sku, "nq": new_qty, "bin": cur_bin},
                )
                conn.execute(
                    text("""
                        INSERT INTO inventory_ledger
                            (sku, channel, quantity_change, reason, quantity, cost_price,
                             previous_quantity, new_quantity, reference_id, user_id, notes)
                        VALUES (:sku, 'WMS_LOCAL', :delta, :reason, :nq, :cost, :pq, :nq, :ref, :uid, :notes)
                    """),
                    {
                        "sku": sku,
                        "delta": signed_qty,
                        "reason": "BARCODE_SCAN_IN" if signed_qty > 0 else "BARCODE_SCAN_OUT",
                        "nq": new_qty,
                        "cost": cost_price,
                        "pq": prev_qty,
                        "ref": cur_ref,
                        "uid": current_user,
                        "notes": f"Scanned: {cleaned_code} | Bin {cur_bin} | Lot {cur_lot}",
                    },
                )

            st.session_state["last_scan_feedback"] = {
                "ok": True,
                "sku": sku,
                "title": title,
                "bin": cur_bin,
                "qty": cur_qty,
                "signed_qty": signed_qty,
                "msg": f"Matched `{cleaned_code}` → **{sku}** ({title[:40]}): {prev_qty} → **{new_qty}** in `{cur_bin}`",
            }

        def process_handheld_scan():
            raw_code = st.session_state.get("handheld_scan_input", "")
            st.session_state["handheld_scan_input"] = ""
            execute_scan_for_code(raw_code)

        sc_col1, sc_col2 = st.columns([4, 1])
        with sc_col1:
            st.text_input(
                "🔫 ACTIVE SCANNER INPUT (Pull Trigger or Type Barcode + Enter)",
                key="handheld_scan_input",
                on_change=process_handheld_scan,
                placeholder="Scan barcode here (auto-submits on Enter)...",
            )
        with sc_col2:
            st.markdown("<br>", unsafe_allow_html=True)
            if st.button("⚡ Process Scan", use_container_width=True):
                process_handheld_scan()

        fb = st.session_state.get("last_scan_feedback")
        beep_js = ""
        if fb:
            if fb["ok"]:
                st.success(fb["msg"])
                beep_js = "try{const c=new(window.AudioContext||window.webkitAudioContext)(),o=c.createOscillator();o.frequency.setValueAtTime(1046.5,c.currentTime);o.connect(c.destination);o.start();o.stop(c.currentTime+0.12);}catch(e){}"
                if fb.get("signed_qty", 0) > 0 and auto_print_on_scan:
                    z_preset = get_db_secret("ZEBRA_LABEL_PRESET", default="50 x 25 mm (2\" x 1\" Product Sticker)")
                    if z_preset not in ZEBRA_LABEL_PRESETS:
                        z_preset = "50 x 25 mm (2\" x 1\" Product Sticker)"
                    p_dims = ZEBRA_LABEL_PRESETS[z_preset]
                    auto_zpl = generate_zebra_zpl(fb["sku"], fb["title"], f"Bin: {fb['bin']}", preset_name=z_preset, copies=fb["qty"])
                    _, _, png_b64 = build_label_canvas_and_png(fb["sku"], fb["title"], f"Bin: {fb['bin']}", z_preset)
                    render_client_browser_printer_widget(auto_zpl, png_b64, fb["sku"], p_dims["width_mm"], p_dims["height_mm"], fb["qty"], auto_trigger=True)
            else:
                st.error(fb["msg"])
                beep_js = "try{const c=new(window.AudioContext||window.webkitAudioContext)(),o=c.createOscillator();o.type='sawtooth';o.frequency.setValueAtTime(180,c.currentTime);o.connect(c.destination);o.start();o.stop(c.currentTime+0.35);}catch(e){}"

        if st.session_state.get("unmapped_barcode"):
            unmapped = st.session_state["unmapped_barcode"]
            with st.expander(f"🔗 Link Unrecognized Barcode `{unmapped}` to `inventory_items`", expanded=True):
                all_skus_df = get_cached_master_skus()
                if not all_skus_df.empty:
                    sku_labels = dict(zip(all_skus_df["sku"], all_skus_df["title"]))
                    target_sku = st.selectbox("Select Master SKU in `inventory_items`", list(sku_labels.keys()), format_func=lambda s: f"{s} — {sku_labels[s][:60]}")
                    if st.button(f"💾 Bind `{unmapped}` → `{target_sku}` in `inventory_items` & Re-Scan Now", type="primary"):
                        execute_sql("UPDATE inventory_items SET barcode = :bc WHERE sku = :sku", {"sku": target_sku, "bc": unmapped})
                        log_audit(current_user, "BARCODE_BOUND", f"Bound barcode {unmapped} to {target_sku} in inventory_items", "")
                        execute_scan_for_code(unmapped)
                        st.rerun()

        components.html(
            f"""
            <script>
            {beep_js}
            function attachScannerHooks() {{
                const doc = window.parent.document;
                const inputs = doc.querySelectorAll('input[aria-label*="ACTIVE SCANNER INPUT"]');
                if (inputs.length > 0) {{
                    const inp = inputs[0];
                    if (doc.activeElement !== inp && (!doc.activeElement || doc.activeElement.tagName === 'BODY')) {{ inp.focus(); }}
                    if (!inp.dataset.scannerHooked) {{
                        inp.dataset.scannerHooked = "1";
                        inp.addEventListener("keydown", function(e) {{
                            if (e.key === "Enter" || e.keyCode === 13) {{
                                e.preventDefault();
                                inp.dispatchEvent(new Event("change", {{ bubbles: true }}));
                                inp.blur();
                                setTimeout(() => inp.focus(), 180);
                            }}
                        }});
                    }}
                }}
            }}
            setTimeout(attachScannerHooks, 100);
            setTimeout(attachScannerHooks, 350);
            </script>
            """,
            height=0,
        )

        st.subheader("Live Scan Feed (`inventory_ledger`)")
        st.dataframe(run_query("SELECT created_at, sku, reason, quantity_change, previous_quantity, new_quantity, reference_id, user_id, notes FROM inventory_ledger ORDER BY id DESC LIMIT 25"), use_container_width=True)

    render_scanner_fragment()
