from datetime import datetime
import streamlit as st
from core.database import execute_sql, run_query


def render(auth_user: dict):
    current_user = auth_user["username"]
    st.title("🔍 Receiving (GRN), Pick Lists & Cycle Counts")
    op1, op2, op3 = st.tabs(["Goods Received Notes (GRN)", "Pick Lists", "Cycle Counts"])
    with op1:
        with st.form("new_grn"):
            g1, g2, g3 = st.columns(3)
            grn_no = g1.text_input("GRN Number", value=f"GRN-{datetime.now().strftime('%Y%m%d-%H%M')}")
            supp = g2.text_input("Supplier Name")
            po = g3.text_input("PO Number")
            if st.form_submit_button("Create GRN"):
                execute_sql(
                    "INSERT INTO goods_received_notes (grn_number, supplier_name, po_number, received_by) VALUES (:g, :s, :p, :u)",
                    {"g": grn_no, "s": supp, "p": po, "u": current_user},
                )
                st.success(f"Created {grn_no}")
        st.dataframe(run_query("SELECT * FROM goods_received_notes ORDER BY created_at DESC LIMIT 250"), use_container_width=True)
    with op2:
        st.dataframe(run_query("SELECT * FROM pick_lists ORDER BY created_at DESC LIMIT 250"), use_container_width=True)
    with op3:
        st.dataframe(run_query("SELECT * FROM cycle_counts ORDER BY created_at DESC LIMIT 250"), use_container_width=True)
