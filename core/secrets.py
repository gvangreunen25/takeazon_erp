import pandas as pd
import streamlit as st
from core.database import run_query


def _clean_db_str(val) -> str:
    if val is None:
        return ""
    try:
        if pd.isna(val):
            return ""
    except Exception:
        pass
    s = str(val).strip()
    return "" if s.lower() in ("none", "nan", "null") else s


@st.cache_data(ttl=120, show_spinner=False)
def load_all_db_configs() -> dict:
    cfg = {}

    for _, r in run_query("SELECT setting_key, setting_value FROM system_settings").iterrows():
        k = _clean_db_str(r["setting_key"])
        if k:
            cfg[k] = _clean_db_str(r["setting_value"])

    for _, r in run_query("SELECT secret_key, secret_value FROM app_secrets").iterrows():
        k = _clean_db_str(r["secret_key"])
        if k:
            cfg[k] = _clean_db_str(r["secret_value"])

    ch_df = run_query("""
        SELECT channel_name, api_url, api_endpoint, api_key, api_secret,
               api_key_encrypted, api_secret_encrypted
        FROM channel_credentials
        WHERE is_active = 1
    """)
    for _, r in ch_df.iterrows():
        ch_clean = _clean_db_str(r["channel_name"])
        if not ch_clean:
            continue
        prefix = ch_clean.upper().replace(" ", "_")
        url_val = _clean_db_str(r["api_url"]) or _clean_db_str(r["api_endpoint"])
        key_val = _clean_db_str(r["api_key"]) or _clean_db_str(r["api_key_encrypted"])
        sec_val = _clean_db_str(r["api_secret"]) or _clean_db_str(r["api_secret_encrypted"])

        if url_val:
            cfg[f"{prefix}_API_URL"] = url_val
        if key_val:
            cfg[f"{prefix}_API_KEY"] = key_val
        if sec_val:
            cfg[f"{prefix}_API_SECRET"] = sec_val

    return cfg


def get_db_secret(key: str, fallback_keys: list = None, default: str = "") -> str:
    cfg = load_all_db_configs()
    val = _clean_db_str(cfg.get(key))
    if val:
        return val
    for fk in fallback_keys or []:
        fval = _clean_db_str(cfg.get(fk))
        if fval:
            return fval
    return default
