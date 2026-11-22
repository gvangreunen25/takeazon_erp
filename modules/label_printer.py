import streamlit as st

from core.database import execute_sql, get_cached_active_bins, get_cached_master_skus, log_audit
from core.secrets import get_db_secret, load_all_db_configs
from core.zebra import (
    ZEBRA_LABEL_PRESETS,
    build_label_canvas_and_png,
    generate_zebra_zpl,
    render_client_browser_printer_widget,
    send_zpl_to_network_zebra,
)


def render(auth_user: dict):
    current_user = auth_user["username"]
    st.title("🏷️ Zebra ZD220 / ZD230 Thermal Label Printer (Browser-Detected Local Printers)")

    saved_mode = get_db_secret("ZEBRA_PRINT_MODE", default="Client Browser Local Printer (Zebra USB / OS Dialog)")
    saved_ip = get_db_secret("ZEBRA_PRINTER_IP", default="192.168.1.200")
    saved_preset = get_db_secret("ZEBRA_LABEL_PRESET", default="50 x 25 mm (2\" x 1\" Product Sticker)")
    if saved_preset not in ZEBRA_LABEL_PRESETS:
        saved_preset = "50 x 25 mm (2\" x 1\" Product Sticker)"

    with st.expander("🖨️ Printer Connection Mode & Label Size", expanded=True):
        zc1, zc2, zc3 = st.columns(3)
        print_modes = [
            "Client Browser Local Printer (Zebra USB / OS Dialog)",
            "Direct Network IP (Zebra ZD230 TCP 9100)",
        ]
        default_mode_idx = print_modes.index(saved_mode) if saved_mode in print_modes else 0
        zebra_mode = zc1.selectbox("Connection Method", print_modes, index=default_mode_idx)

        if zebra_mode == "Direct Network IP (Zebra ZD230 TCP 9100)":
            zebra_ip = zc2.text_input("Zebra ZD230 IP Address", value=saved_ip)
            zebra_port = zc3.number_input("Raw TCP Port", min_value=1, max_value=65535, value=9100)
        else:
            zc2.info("Printers are detected live from **your browser's workstation** below.")
            zebra_ip = saved_ip
            zebra_port = 9100

        preset_list = list(ZEBRA_LABEL_PRESETS.keys())
        label_preset = (
            zc3.selectbox("Thermal Label Stock Size (203 DPI)", preset_list, index=preset_list.index(saved_preset))
            if zebra_mode != "Direct Network IP (Zebra ZD230 TCP 9100)"
            else st.selectbox("Thermal Label Stock Size (203 DPI)", preset_list, index=preset_list.index(saved_preset))
        )

        if st.button("💾 Save Label Size & Default Mode to Database (`system_settings`)"):
            for k, v in [
                ("ZEBRA_PRINT_MODE", zebra_mode),
                ("ZEBRA_PRINTER_IP", zebra_ip),
                ("ZEBRA_LABEL_PRESET", label_preset),
            ]:
                execute_sql(
                    "INSERT INTO system_settings (setting_key, setting_value) VALUES (:k, :v) ON DUPLICATE KEY UPDATE setting_value = :v",
                    {"k": k, "v": str(v)},
                )
            load_all_db_configs.clear()
            st.success("Saved printer defaults to `system_settings`.")

    st.divider()
    label_mode = st.radio(
        "Label Content",
        ["Product SKU Barcode (Code128)", "Warehouse Bin Location Label", "Custom / Batch Text Barcode"],
        horizontal=True,
    )

    barcode_val, label_title, label_sub = "", "", ""

    if label_mode == "Product SKU Barcode (Code128)":
        items = get_cached_master_skus()
        selected_sku = st.selectbox("Select SKU", items["sku"].tolist() if not items.empty else ["DEMO-SKU-001"])
        row = items[items["sku"] == selected_sku].iloc[0] if not items.empty else {"title": "Sample Product", "single_price_excl_vat": 99.0}

        barcode_val = selected_sku
        label_title = st.text_input("Top Line Text", value=str(row["title"])[:42])
        include_price = st.checkbox("Include Retail Price (Incl. 15% VAT)", value=False)
        if include_price:
            incl = float(row["single_price_excl_vat"]) * 1.15
            label_sub = f"R {incl:,.2f} incl. VAT"

    elif label_mode == "Warehouse Bin Location Label":
        bin_choices = get_cached_active_bins()
        sel_bin = st.selectbox("Select Bin Code", bin_choices)
        barcode_val = sel_bin
        label_title = st.text_input("Bin Header", value=f"WAREHOUSE BIN: {sel_bin}")
        label_sub = "SCAN TO PICK / PUTAWAY"

    else:
        barcode_val = st.text_input("Barcode Value (Code128)", value="GRN-2026-001")
        label_title = st.text_input("Top Line Text", value="TAKEAZON WMS")
        label_sub = st.text_input("Bottom Line Text", value="")

    c_p1, c_p2 = st.columns(2)
    copies = c_p1.number_input("Number of Copies", min_value=1, max_value=500, value=1)
    darkness = c_p2.slider("Zebra Printhead Darkness (`^MD`)", min_value=0, max_value=30, value=15)

    zpl_output = generate_zebra_zpl(
        barcode_value=barcode_val,
        title=label_title,
        subtitle=label_sub,
        preset_name=label_preset,
        copies=copies,
        darkness=darkness,
    )

    preset_dims = ZEBRA_LABEL_PRESETS[label_preset]
    canvas, png_bytes, png_b64 = build_label_canvas_and_png(barcode_val, label_title, label_sub, label_preset)

    col_prev, col_act = st.columns([1.1, 1.3])
    with col_prev:
        st.image(canvas, caption=f"203 DPI Preview ({preset_dims['width_mm']}×{preset_dims['height_mm']} mm)")
        with st.expander("View Raw Zebra ZPL Code (`^XA ... ^XZ`)"):
            st.code(zpl_output, language="text")

    with col_act:
        st.subheader("Print from Your Browser Workstation")
        if zebra_mode == "Client Browser Local Printer (Zebra USB / OS Dialog)":
            render_client_browser_printer_widget(
                zpl_data=zpl_output,
                label_png_b64=png_b64,
                barcode_val=barcode_val,
                width_mm=preset_dims["width_mm"],
                height_mm=preset_dims["height_mm"],
                copies=copies,
                auto_trigger=False,
            )
        else:
            if st.button(f"🖨️ Send {copies}x ZPL to Network Zebra ({zebra_ip}:{zebra_port})", type="primary", use_container_width=True):
                ok, msg = send_zpl_to_network_zebra(zpl_output, printer_ip=zebra_ip, printer_port=zebra_port)
                if ok:
                    log_audit(current_user, "ZEBRA_PRINT", f"Printed {copies}x {barcode_val}", msg, copies)
                    st.success(msg)
                else:
                    st.error(msg)

        st.download_button(
            "⬇️ Download Raw `.zpl` File",
            data=zpl_output.encode("utf-8"),
            file_name=f"zebra_{barcode_val}.zpl",
            mime="text/plain",
            use_container_width=True,
        )
        st.download_button(
            "⬇️ Download 203-DPI Thermal PNG",
            data=png_bytes,
            file_name=f"label_{barcode_val}.png",
            mime="image/png",
            use_container_width=True,
        )
