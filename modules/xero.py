from datetime import datetime, timedelta

import pandas as pd
import requests
import streamlit as st

from core.database import execute_sql, log_audit, run_query
from core.secrets import get_db_secret


def render(auth_user: dict):
    current_user = auth_user["username"]
    st.title("💼 Xero Accounting Financial Integration")

    client_id = get_db_secret("XERO_CLIENT_ID", fallback_keys=["XERO_API_KEY"])
    client_secret = get_db_secret("XERO_CLIENT_SECRET", fallback_keys=["XERO_API_SECRET"])
    tok_df = run_query("SELECT * FROM app_xero_tokens ORDER BY id DESC LIMIT 1")

    if tok_df.empty:
        st.warning("No OAuth token found in `app_xero_tokens`.")
        return

    tok = tok_df.iloc[0]
    expires_at = pd.to_datetime(tok["expires_at"])
    is_expired = datetime.utcnow() >= expires_at
    st.info(f"**Tenant ID:** `{tok['tenant_id']}` | **Expires (UTC):** `{expires_at}` ({'EXPIRED' if is_expired else 'ACTIVE'})")

    col_a, col_b = st.columns(2)
    if col_a.button("🔄 Refresh Xero Access Token"):
        resp = requests.post(
            "https://identity.xero.com/connect/token",
            data={"grant_type": "refresh_token", "refresh_token": tok["refresh_token"]},
            auth=(client_id, client_secret),
            timeout=20,
        )
        if resp.status_code == 200:
            new_t = resp.json()
            new_exp = datetime.utcnow() + timedelta(seconds=int(new_t["expires_in"]) - 60)
            execute_sql(
                "UPDATE app_xero_tokens SET access_token = :at, refresh_token = :rt, expires_at = :exp WHERE id = :id",
                {"at": new_t["access_token"], "rt": new_t["refresh_token"], "exp": new_exp, "id": int(tok["id"])},
            )
            log_audit(current_user, "XERO_TOKEN_REFRESH", "Refreshed Xero OAuth Token", f"Tenant {tok['tenant_id']}")
            st.success("Xero token refreshed in `app_xero_tokens`.")
        else:
            st.error(f"Refresh failed: {resp.text}")

    if col_b.button("📥 Pull Xero Profit & Loss Report"):
        headers = {
            "Authorization": f"Bearer {tok['access_token']}",
            "Xero-tenant-id": tok["tenant_id"],
            "Accept": "application/json",
        }
        r = requests.get("https://api.xero.com/api.xro/2.0/Reports/ProfitAndLoss", headers=headers, timeout=20)
        if r.status_code == 200:
            st.json(r.json())
        else:
            st.error(f"Xero API Error ({r.status_code}): {r.text}")
