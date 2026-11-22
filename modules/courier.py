from datetime import datetime

import requests
import streamlit as st

from core.database import execute_sql, log_audit, run_query
from core.secrets import get_db_secret


def render(auth_user: dict):
    current_user = auth_user["username"]
    st.title("🚚 The Courier Guy (ShipLogic API) Integration")

    tcg_token = get_db_secret(
        "TCG_API_TOKEN",
        fallback_keys=["THE_COURIER_GUY_API_KEY", "COURIER_GUY_API_KEY", "SHIPLOGIC_API_KEY", "TCG_API_KEY"],
    )
    tcg_base = get_db_secret(
        "TCG_API_URL",
        fallback_keys=["THE_COURIER_GUY_API_URL", "COURIER_GUY_API_URL", "SHIPLOGIC_API_URL"],
        default="https://api.shiplogic.com",
    )

    if tcg_token:
        st.success(f"Loaded Courier Guy credentials from database (`{tcg_base}`).")
    else:
        st.warning("No active Courier Guy token found in `app_secrets` or `channel_credentials`.")

    tab_create, tab_track = st.tabs(["📦 Book Shipment & Create Waybill", "📍 Track Existing Waybills"])
    with tab_create:
        with st.form("tcg_shipment_form"):
            c1, c2 = st.columns(2)
            order_id = c1.text_input("Marketplace Order ID *")
            service_level = c2.selectbox("Service Level", ["ECO", "OVN", "LOX", "SDX"])

            d1, d2, d3 = st.columns(3)
            rec_name = d1.text_input("Recipient Name *")
            rec_phone = d2.text_input("Recipient Mobile *")
            rec_email = d3.text_input("Recipient Email")

            d4, d5, d6 = st.columns(3)
            street = d4.text_input("Street Address *")
            suburb = d5.text_input("Suburb / City *")
            postal_code = d6.text_input("Postal Code *", max_chars=4)

            p1, p2, p3, p4 = st.columns(4)
            weight_kg = p1.number_input("Weight (kg)", min_value=0.1, value=1.5)
            length_cm = p2.number_input("Length (cm)", min_value=1, value=30)
            width_cm = p3.number_input("Width (cm)", min_value=1, value=20)
            height_cm = p4.number_input("Height (cm)", min_value=1, value=15)

            book_btn = st.form_submit_button("🚚 Create Waybill", type="primary")

        if book_btn and order_id and rec_name and postal_code:
            payload = {
                "collection_address": {
                    "street_address": get_db_secret("WAREHOUSE_STREET", default="1 Warehouse Way"),
                    "local_area": get_db_secret("WAREHOUSE_SUBURB", default="Montague Gardens"),
                    "city": get_db_secret("WAREHOUSE_CITY", default="Cape Town"),
                    "code": get_db_secret("WAREHOUSE_POSTAL", default="7441"),
                    "country": "ZA",
                },
                "delivery_address": {
                    "street_address": street,
                    "local_area": suburb,
                    "city": suburb,
                    "code": postal_code,
                    "country": "ZA",
                },
                "delivery_contact": {"name": rec_name, "mobile_number": rec_phone, "email": rec_email},
                "parcels": [{
                    "submitted_length_cm": length_cm,
                    "submitted_width_cm": width_cm,
                    "submitted_height_cm": height_cm,
                    "submitted_weight_kg": weight_kg,
                }],
                "service_level_code": service_level,
                "custom_tracking_reference": order_id,
            }

            if tcg_token:
                resp = requests.post(
                    f"{tcg_base.rstrip('/')}/shipments",
                    headers={"Authorization": f"Bearer {tcg_token}", "Content-Type": "application/json"},
                    json=payload,
                    timeout=20,
                )
                if resp.status_code in (200, 201):
                    data = resp.json()
                    waybill = data.get("short_tracking_reference") or data.get("waybill_number") or f"TCG-{order_id}"
                    label_url = data.get("waybill_url", "")
                    status = data.get("status", "manifested")
                else:
                    st.error(f"ShipLogic API Error ({resp.status_code}): {resp.text}")
                    st.stop()
            else:
                waybill = f"TCG-SIM-{datetime.now().strftime('%y%m%d%H%M%S')}"
                label_url = ""
                status = "manifested"

            execute_sql(
                "INSERT INTO courier_waybills (order_id, courier_name, waybill_number, shipping_label_url, status) VALUES (:oid, 'The Courier Guy', :wb, :url, :st)",
                {"oid": order_id, "wb": waybill, "url": label_url, "st": status},
            )
            execute_sql(
                "INSERT INTO courier_shipments (waybill_number, courier_name, order_id, status) VALUES (:wb, 'The Courier Guy', :oid, :st)",
                {"wb": waybill, "oid": order_id, "st": status},
            )
            log_audit(current_user, "WAYBILL_CREATED", f"Waybill {waybill}", f"Order {order_id} ({service_level})")
            st.success(f"Waybill Created: **{waybill}**")

    with tab_track:
        st.dataframe(run_query("SELECT * FROM courier_waybills ORDER BY created_at DESC LIMIT 250"), use_container_width=True)
