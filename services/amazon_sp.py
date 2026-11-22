from datetime import datetime, timedelta, timezone

import pandas as pd
import requests
from sqlalchemy import text

from core.database import engine, execute_sql, get_cached_master_skus, log_audit, run_query
from core.secrets import get_db_secret


def get_amazon_sp_access_token(client_id: str, client_secret: str, refresh_token: str) -> str:
    resp = requests.post(
        "https://api.amazon.com/auth/o2/token",
        data={
            "grant_type": "refresh_token",
            "refresh_token": refresh_token.strip(),
            "client_id": client_id.strip(),
            "client_secret": client_secret.strip(),
        },
        timeout=20,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"Amazon LwA Token Error ({resp.status_code}): {resp.text}")
    return resp.json()["access_token"]


def clear_amazon_financial_data(triggered_by: str = "admin"):
    with engine.begin() as conn:
        conn.execute(text("TRUNCATE TABLE amazon_financial_events"))
        conn.execute(text("DELETE FROM financial_ledger WHERE UPPER(channel) IN ('AMAZON', 'AMAZON_ZA', 'AMAZON ZA')"))
        conn.execute(text("DELETE FROM payout_settlements WHERE UPPER(channel) IN ('AMAZON', 'AMAZON_ZA', 'AMAZON ZA')"))
    log_audit(
        triggered_by,
        "AMAZON_FINANCE_RESET",
        "Cleared Amazon ZA Financial Tables",
        "Truncated amazon_financial_events and deleted AMAZON / AMAZON_ZA ledger rows (stock deductions preserved in inventory_ledger)",
    )


def sync_amazon_financial_transactions(
    posted_after_iso: str,
    posted_before_iso: str,
    triggered_by: str = "admin",
    deduct_inventory: bool = True,
) -> dict:
    client_id = get_db_secret(
        "AMAZON_LWA_CLIENT_ID",
        fallback_keys=["AMAZON_ZA_API_KEY", "AMAZON_CLIENT_ID", "SP_API_CLIENT_ID", "AMAZON_API_KEY"],
    )
    client_secret = get_db_secret(
        "AMAZON_LWA_CLIENT_SECRET",
        fallback_keys=["AMAZON_ZA_API_SECRET", "AMAZON_CLIENT_SECRET", "SP_API_CLIENT_SECRET", "AMAZON_API_SECRET"],
    )
    refresh_token = get_db_secret(
        "AMAZON_REFRESH_TOKEN",
        fallback_keys=["AMAZON_ZA_REFRESH_TOKEN", "SP_API_REFRESH_TOKEN", "AMAZON_SP_REFRESH_TOKEN"],
    )
    sp_endpoint = get_db_secret(
        "AMAZON_SP_API_URL",
        fallback_keys=["AMAZON_ZA_API_URL", "AMAZON_API_URL"],
        default="https://sellingpartnerapi-eu.amazon.com",
    ).rstrip("/")
    if not sp_endpoint.startswith("http"):
        sp_endpoint = "https://sellingpartnerapi-eu.amazon.com"

    if not (client_id and client_secret and refresh_token):
        raise ValueError(
            "Missing Amazon SP-API credentials (`AMAZON_LWA_CLIENT_ID`, `AMAZON_LWA_CLIENT_SECRET`, `AMAZON_REFRESH_TOKEN`)."
        )

    access_token = get_amazon_sp_access_token(client_id, client_secret, refresh_token)
    headers = {"x-amz-access-token": access_token, "Accept": "application/json"}

    start_dt = pd.to_datetime(posted_after_iso, utc=True)
    end_dt = pd.to_datetime(posted_before_iso, utc=True)
    url = f"{sp_endpoint}/finances/v0/financialEvents"

    shipment_events, refund_events, service_fee_events = [], [], []
    chunk_start = start_dt
    while chunk_start < end_dt:
        chunk_end = min(chunk_start + timedelta(days=175), end_dt)
        next_token = None
        while True:
            params = (
                {"NextToken": next_token}
                if next_token
                else {
                    "PostedAfter": chunk_start.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "PostedBefore": chunk_end.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "MaxResultsPerPage": 100,
                }
            )
            resp = requests.get(url, headers=headers, params=params, timeout=30)
            if resp.status_code != 200:
                execute_sql(
                    "INSERT INTO channel_sync_logs (channel, job_type, status, items_processed, error_message) VALUES ('AMAZON_ZA', 'FINANCES_V0_SYNC', 'ERROR', 0, :err)",
                    {"err": f"HTTP {resp.status_code}: {resp.text[:500]}"},
                )
                raise RuntimeError(f"Amazon Finances v0 Error ({resp.status_code}): {resp.text}")

            payload = resp.json().get("payload", {})
            fe = payload.get("FinancialEvents", {})
            shipment_events.extend(fe.get("ShipmentEventList", []))
            refund_events.extend(fe.get("RefundEventList", []))
            service_fee_events.extend(fe.get("ServiceFeeEventList", []))
            next_token = payload.get("NextToken")
            if not next_token:
                break
        chunk_start = chunk_end

    inv_df = run_query("SELECT sku, cost_price, quantity FROM inventory_items")
    sku_cost_map = dict(zip(inv_df["sku"].astype(str), inv_df["cost_price"].astype(float))) if not inv_df.empty else {}
    sku_qty_map = dict(zip(inv_df["sku"].astype(str), inv_df["quantity"].fillna(0).astype(int))) if not inv_df.empty else {}

    # Load already-deducted Amazon order-SKU keys so re-syncing never double-deducts stock
    deducted_df = run_query("""
        SELECT DISTINCT reference_id
        FROM inventory_ledger
        WHERE channel = 'AMAZON_ZA' AND reason = 'AMAZON_SALE_DEDUCTION'
    """)
    already_deducted_refs = set(deducted_df["reference_id"].astype(str).tolist()) if not deducted_df.empty else set()

    event_rows, order_rollups = [], {}
    sold_items_to_deduct = {}  # ref_key -> {"oid": oid, "sku": sku, "qty": qty, "posted_dt": posted_dt}

    def _money_v0(m: dict) -> tuple[float, str]:
        if not isinstance(m, dict):
            return 0.0, "ZAR"
        return float(m.get("CurrencyAmount") or 0.0), str(m.get("CurrencyCode") or "ZAR")

    def _is_amazon_za(ev: dict) -> bool:
        return str(ev.get("MarketplaceName") or "").strip().lower() in ("", "amazon.co.za", "amazon za")

    for ev in shipment_events:
        if not _is_amazon_za(ev):
            continue
        oid = str(ev.get("AmazonOrderId") or "")
        posted_dt = (
            pd.to_datetime(str(ev.get("PostedDate") or datetime.now(timezone.utc).isoformat()))
            .tz_localize(None)
            .strftime("%Y-%m-%d %H:%M:%S")
        )
        for item in ev.get("ShipmentItemList") or []:
            sku = str(item.get("SellerSKU") or "").strip()
            qty = int(item.get("QuantityShipped") or 0)
            o_rec = order_rollups.setdefault(
                oid,
                {
                    "sku": sku,
                    "gross_incl_vat": 0.0,
                    "principal_excl_vat": 0.0,
                    "refunds_excl_vat": 0.0,
                    "fees": 0.0,
                    "cogs": 0.0,
                    "date": posted_dt,
                },
            )
            if sku and not o_rec["sku"]:
                o_rec["sku"] = sku
            if sku and qty > 0:
                o_rec["cogs"] += sku_cost_map.get(sku, 0.0) * qty
                if oid:
                    ref_key = f"{oid}:{sku}"
                    if ref_key not in already_deducted_refs:
                        rec_d = sold_items_to_deduct.setdefault(
                            ref_key, {"oid": oid, "sku": sku, "qty": 0, "posted_dt": posted_dt}
                        )
                        rec_d["qty"] += qty

            for ch in item.get("ItemChargeList") or []:
                ctype = str(ch.get("ChargeType") or "Charge")
                amt, cur = _money_v0(ch.get("ChargeAmount", {}))
                if cur == "ZAR":
                    event_rows.append(
                        {"oid": oid, "pdate": posted_dt, "ttype": "Shipment", "sku": sku or None, "qty": qty, "ctype": ctype, "amt": round(amt, 2)}
                    )
                    if ctype == "Principal":
                        o_rec["principal_excl_vat"] += amt
                        o_rec["gross_incl_vat"] += amt
                    elif ctype == "Tax":
                        o_rec["gross_incl_vat"] += amt

            for fee in item.get("ItemFeeList") or []:
                ftype = str(fee.get("FeeType") or "Fee")
                amt, cur = _money_v0(fee.get("FeeAmount", {}))
                if cur == "ZAR":
                    event_rows.append(
                        {"oid": oid, "pdate": posted_dt, "ttype": "ShipmentFee", "sku": sku or None, "qty": qty, "ctype": ftype, "amt": round(amt, 2)}
                    )
                    o_rec["fees"] += abs(amt)

    for ev in refund_events:
        if not _is_amazon_za(ev):
            continue
        oid = str(ev.get("AmazonOrderId") or "")
        posted_dt = (
            pd.to_datetime(str(ev.get("PostedDate") or datetime.now(timezone.utc).isoformat()))
            .tz_localize(None)
            .strftime("%Y-%m-%d %H:%M:%S")
        )
        for item in ev.get("ShipmentItemAdjustmentList") or []:
            sku = str(item.get("SellerSKU") or "")
            qty = int(item.get("QuantityShipped") or 0)
            o_rec = order_rollups.setdefault(
                oid,
                {
                    "sku": sku,
                    "gross_incl_vat": 0.0,
                    "principal_excl_vat": 0.0,
                    "refunds_excl_vat": 0.0,
                    "fees": 0.0,
                    "cogs": 0.0,
                    "date": posted_dt,
                },
            )
            for ch in item.get("ItemChargeAdjustmentList") or []:
                ctype = str(ch.get("ChargeType") or "RefundCharge")
                amt, cur = _money_v0(ch.get("ChargeAmount", {}))
                if cur == "ZAR":
                    event_rows.append(
                        {"oid": oid, "pdate": posted_dt, "ttype": "Refund", "sku": sku or None, "qty": qty, "ctype": f"Refund_{ctype}", "amt": round(amt, 2)}
                    )
                    if ctype == "Principal":
                        o_rec["refunds_excl_vat"] += abs(amt)
            for fee in item.get("ItemFeeAdjustmentList") or []:
                ftype = str(fee.get("FeeType") or "RefundFee")
                amt, cur = _money_v0(fee.get("FeeAmount", {}))
                if cur == "ZAR":
                    event_rows.append(
                        {"oid": oid, "pdate": posted_dt, "ttype": "RefundFee", "sku": sku or None, "qty": qty, "ctype": f"Refund_{ftype}", "amt": round(amt, 2)}
                    )
                    o_rec["fees"] -= amt

    for ev in service_fee_events:
        oid = str(ev.get("AmazonOrderId") or f"SVC-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}")
        sku = str(ev.get("SellerSKU") or "")
        posted_dt = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        for fee in ev.get("FeeList") or []:
            ftype = str(fee.get("FeeType") or "ServiceFee")
            amt, cur = _money_v0(fee.get("FeeAmount", {}))
            if cur == "ZAR":
                event_rows.append(
                    {"oid": oid, "pdate": posted_dt, "ttype": "ServiceFee", "sku": sku or None, "qty": 0, "ctype": ftype, "amt": round(amt, 2)}
                )

    ledger_rows = [
        {
            "tid": oid,
            "sku": rec["sku"] or None,
            "gross": round(rec["gross_incl_vat"], 2),
            "fees": round(rec["fees"] + rec["refunds_excl_vat"], 2),
            "cogs": round(rec["cogs"], 2),
            "net": round((rec["principal_excl_vat"] - rec["refunds_excl_vat"]) - rec["fees"] - rec["cogs"], 2),
            "tdate": rec["date"],
        }
        for oid, rec in order_rollups.items()
    ]

    units_deducted_total = 0
    skus_deducted_count = 0
    events_inserted = 0

    with engine.begin() as conn:
        conn.execute(text("UPDATE financial_ledger SET channel = 'AMAZON_ZA' WHERE UPPER(channel) = 'AMAZON'"))
        conn.execute(text("UPDATE payout_settlements SET channel = 'AMAZON_ZA' WHERE UPPER(channel) = 'AMAZON'"))
        if event_rows:
            res = conn.execute(
                text("""
                    INSERT IGNORE INTO amazon_financial_events
                        (amazon_order_id, posted_date, transaction_type, sku, quantity_shipped, charge_type, amount, currency, financial_event_group_id)
                    VALUES (:oid, :pdate, :ttype, :sku, :qty, :ctype, :amt, 'ZAR', NULL)
                """),
                event_rows,
            )
            events_inserted = res.rowcount or len(event_rows)
        if ledger_rows:
            conn.execute(
                text("""
                    INSERT INTO financial_ledger
                        (channel, transaction_id, sku, gross_sales, channel_fees, cogs, net_profit, currency, order_status, transaction_date)
                    VALUES ('AMAZON_ZA', :tid, :sku, :gross, :fees, :cogs, :net, 'ZAR', 'completed', :tdate)
                    ON DUPLICATE KEY UPDATE
                        sku = COALESCE(NULLIF(VALUES(sku), ''), sku),
                        gross_sales = VALUES(gross_sales),
                        channel_fees = VALUES(channel_fees),
                        cogs = VALUES(cogs),
                        net_profit = VALUES(net_profit),
                        transaction_date = VALUES(transaction_date)
                """),
                ledger_rows,
            )

        if deduct_inventory and sold_items_to_deduct:
            latest_qty_by_sku = {}
            inv_ledger_rows = []
            for ref_key, item_d in sold_items_to_deduct.items():
                sku = item_d["sku"]
                qty_sold = int(item_d["qty"])
                if sku not in sku_qty_map or qty_sold <= 0:
                    continue

                prev_q = sku_qty_map[sku]
                new_q = max(0, prev_q - qty_sold)
                sku_qty_map[sku] = new_q
                latest_qty_by_sku[sku] = new_q

                units_deducted_total += qty_sold
                skus_deducted_count += 1

                inv_ledger_rows.append(
                    {
                        "sku": sku,
                        "delta": -qty_sold,
                        "nq": new_q,
                        "cost": sku_cost_map.get(sku, 0.0),
                        "pq": prev_q,
                        "ref": ref_key,
                        "uid": triggered_by,
                        "notes": f"Amazon.co.za Order {item_d['oid']} sold {qty_sold} unit(s) on {item_d['posted_dt']}",
                    }
                )

            inv_updates = [{"sku": sku, "nq": nq} for sku, nq in latest_qty_by_sku.items()]

            if inv_updates:
                conn.execute(
                    text("UPDATE inventory_items SET quantity = :nq WHERE sku = :sku"),
                    inv_updates,
                )
                conn.execute(
                    text("""
                        INSERT INTO channel_stock_levels (sku, total_wms_stock)
                        VALUES (:sku, :nq)
                        ON DUPLICATE KEY UPDATE total_wms_stock = VALUES(total_wms_stock)
                    """),
                    inv_updates,
                )
                conn.execute(
                    text("""
                        INSERT INTO inventory_ledger
                            (sku, channel, quantity_change, reason, quantity, cost_price,
                             previous_quantity, new_quantity, reference_id, user_id, notes)
                        VALUES
                            (:sku, 'AMAZON_ZA', :delta, 'AMAZON_SALE_DEDUCTION', :nq, :cost,
                             :pq, :nq, :ref, :uid, :notes)
                    """),
                    inv_ledger_rows,
                )

        conn.execute(
            text("INSERT INTO channel_sync_logs (channel, job_type, status, items_processed) VALUES ('AMAZON_ZA', 'FINANCES_V0_SYNC', 'SUCCESS', :cnt)"),
            {"cnt": events_inserted},
        )

    if units_deducted_total > 0:
        get_cached_master_skus.clear()

    log_audit(
        triggered_by,
        "AMAZON_ZA_FINANCE_SYNC",
        "Synced Amazon.co.za Financial Events",
        f"Events: {events_inserted}, Orders: {len(order_rollups)}, Units Deducted: {units_deducted_total}",
        events_inserted,
    )
    return {
        "transactions_fetched": len(shipment_events) + len(refund_events) + len(service_fee_events),
        "events_inserted": events_inserted,
        "orders_rolled_up": len(order_rollups),
        "units_deducted": units_deducted_total,
        "skus_deducted": skus_deducted_count,
    }
import time
from urllib.parse import quote


def _get_sp_api_headers_and_endpoint() -> tuple[dict, str]:
    client_id = get_db_secret(
        "AMAZON_LWA_CLIENT_ID",
        fallback_keys=["AMAZON_ZA_API_KEY", "AMAZON_CLIENT_ID", "SP_API_CLIENT_ID", "AMAZON_API_KEY"],
    )
    client_secret = get_db_secret(
        "AMAZON_LWA_CLIENT_SECRET",
        fallback_keys=["AMAZON_ZA_API_SECRET", "AMAZON_CLIENT_SECRET", "SP_API_CLIENT_SECRET", "AMAZON_API_SECRET"],
    )
    refresh_token = get_db_secret(
        "AMAZON_REFRESH_TOKEN",
        fallback_keys=["AMAZON_ZA_REFRESH_TOKEN", "SP_API_REFRESH_TOKEN", "AMAZON_SP_REFRESH_TOKEN"],
    )
    sp_endpoint = get_db_secret(
        "AMAZON_SP_API_URL",
        fallback_keys=["AMAZON_ZA_API_URL", "AMAZON_API_URL"],
        default="https://sellingpartnerapi-eu.amazon.com",
    ).rstrip("/")
    if not sp_endpoint.startswith("http"):
        sp_endpoint = "https://sellingpartnerapi-eu.amazon.com"

    if not (client_id and client_secret and refresh_token):
        raise ValueError(
            "Missing Amazon SP-API credentials (`AMAZON_LWA_CLIENT_ID`, `AMAZON_LWA_CLIENT_SECRET`, `AMAZON_REFRESH_TOKEN`)."
        )

    access_token = get_amazon_sp_access_token(client_id, client_secret, refresh_token)
    headers = {
        "x-amz-access-token": access_token,
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    return headers, sp_endpoint


def update_sellerflex_inventory_batch(
    location_id: str,
    items: list[dict],
    batch_size: int = 20,
    triggered_by: str = "admin",
) -> list[dict]:
    """
    Updates Amazon Seller Flex warehouse inventory in batches of up to 20 SKUs
    via POST /externalFulfillment/inventory/2024-09-11/inventory/batch
    and mirrors results into `sellerflex_inventory`.
    """
    if not location_id.strip():
        raise ValueError("Seller Flex Node / Location ID (`AMAZON_SELLERFLEX_LOCATION_ID`) is required.")

    headers, sp_endpoint = _get_sp_api_headers_and_endpoint()
    url = f"{sp_endpoint}/externalFulfillment/inventory/2024-09-11/inventory/batch"
    results = []

    for i in range(0, len(items), batch_size):
        batch = items[i : i + batch_size]
        requests_payload = [
            {
                "method": "POST",
                "uri": "/inventory/update",
                "body": {
                    "locationId": location_id.strip(),
                    "sku": str(item["sku"]).strip(),
                    "quantity": int(item["quantity"]),
                },
            }
            for item in batch
        ]

        resp = requests.post(url, headers=headers, json={"requests": requests_payload}, timeout=30)
        if resp.status_code not in (200, 207):
            for item in batch:
                results.append({
                    "sku": item["sku"],
                    "quantity": item["quantity"],
                    "status_code": resp.status_code,
                    "message": resp.text[:300],
                })
        else:
            payload = resp.json()
            responses = payload.get("responses", [])
            for idx, item in enumerate(batch):
                res_obj = responses[idx] if idx < len(responses) else {}
                st_code = res_obj.get("status", {}).get("statusCode", resp.status_code)
                body = res_obj.get("body", {})
                results.append({
                    "sku": body.get("sku", item["sku"]),
                    "quantity": body.get("quantity", item["quantity"]),
                    "status_code": st_code,
                    "message": "OK" if int(st_code) in (200, 201, 204) else str(body)[:300],
                })

        if i + batch_size < len(items):
            time.sleep(0.5)

    # Mirror successfully pushed quantities into local `sellerflex_inventory` table
    ok_rows = [
        {"sku": r["sku"], "loc": location_id.strip(), "qty": int(r["quantity"])}
        for r in results
        if int(r["status_code"]) in (200, 201, 204)
    ]
    if ok_rows:
        with engine.begin() as conn:
            conn.execute(
                text("""
                    INSERT INTO sellerflex_inventory (sku, location_id, quantity, last_synced_at)
                    VALUES (:sku, :loc, :qty, NOW())
                    ON DUPLICATE KEY UPDATE
                        location_id = VALUES(location_id),
                        quantity = VALUES(quantity),
                        last_synced_at = NOW()
                """),
                ok_rows,
            )

    log_audit(
        triggered_by,
        "SELLERFLEX_INV_SYNC",
        f"Seller Flex Inventory Batch Push ({location_id})",
        f"Submitted: {len(items)}, Succeeded: {len(ok_rows)}",
        len(ok_rows),
    )
    return results


def get_amazon_product_type(
    headers: dict,
    sp_endpoint: str,
    seller_id: str,
    sku: str,
    marketplace_id: str,
) -> str:
    """Fetches the required Amazon productType via Listings Items API v2021-08-01."""
    encoded_sku = quote(str(sku).strip(), safe="")
    url = f"{sp_endpoint}/listings/2021-08-01/items/{seller_id.strip()}/{encoded_sku}"
    try:
        resp = requests.get(
            url,
            headers=headers,
            params={"marketplaceIds": marketplace_id, "includedData": "summaries"},
            timeout=20,
        )
        if resp.status_code == 200:
            summaries = resp.json().get("summaries", [])
            if summaries and summaries[0].get("productType"):
                return str(summaries[0]["productType"])
    except Exception:
        pass
    return "PRODUCT"


def update_amazon_listing_prices_batch(
    seller_id: str,
    marketplace_id: str,
    currency: str,
    items: list[dict],
    triggered_by: str = "admin",
) -> list[dict]:
    """
    Updates selling price (`purchasable_offer` -> `value_with_tax`) and optional MRP (`list_price`)
    via PATCH /listings/2021-08-01/items/{sellerId}/{sku}.
    """
    if not seller_id.strip():
        raise ValueError("Amazon Seller / Merchant ID (`AMAZON_SELLER_ID`) is required.")

    headers, sp_endpoint = _get_sp_api_headers_and_endpoint()
    results = []

    for idx, item in enumerate(items):
        sku = str(item["sku"]).strip()
        price_val = item.get("price")
        if price_val is None or float(price_val) <= 0:
            continue

        product_type = get_amazon_product_type(headers, sp_endpoint, seller_id, sku, marketplace_id)
        patches = [
            {
                "op": "replace",
                "path": "/attributes/purchasable_offer",
                "value": [
                    {
                        "currency": currency,
                        "our_price": [
                            {
                                "schedule": [
                                    {"value_with_tax": round(float(price_val), 2)}
                                ]
                            }
                        ],
                    }
                ],
            }
        ]

        if item.get("list_price") is not None and float(item["list_price"]) > 0:
            patches.append({
                "op": "replace",
                "path": "/attributes/list_price",
                "value": [
                    {
                        "currency": currency,
                        "value": round(float(item["list_price"]), 2),
                    }
                ],
            })

        encoded_sku = quote(sku, safe="")
        url = f"{sp_endpoint}/listings/2021-08-01/items/{seller_id.strip()}/{encoded_sku}"
        resp = requests.patch(
            url,
            headers=headers,
            params={"marketplaceIds": marketplace_id},
            json={"productType": product_type, "patches": patches},
            timeout=25,
        )

        if resp.status_code in (200, 202):
            data = resp.json()
            results.append({
                "sku": sku,
                "product_type": product_type,
                "price": float(price_val),
                "list_price": item.get("list_price"),
                "status": data.get("status", "ACCEPTED"),
                "issues": str(data.get("issues", [])),
            })
        else:
            results.append({
                "sku": sku,
                "product_type": product_type,
                "price": float(price_val),
                "list_price": item.get("list_price"),
                "status": f"HTTP {resp.status_code}",
                "issues": resp.text[:300],
            })

        if idx + 1 < len(items):
            time.sleep(0.3)

    log_audit(
        triggered_by,
        "AMAZON_PRICE_PATCH",
        f"Patched Amazon Listing Prices ({marketplace_id})",
        f"SKUs processed: {len(results)}",
        len(results),
    )
    return results

import io
import json
import re
from typing import Any, Dict, List, Optional, Tuple


def fetch_products_for_amazon_feed(
    skus: Optional[List[str]] = None,
    min_stock_cutoff: int = 75,
    max_stock_cap: int = 500,
    stock_source: str = "stock_ct",
) -> List[Dict[str, Any]]:
    """
    Retrieves listing attributes from MySQL (`inventory_item_profitability`, `inventory_items`, `amazon_active_listings`)
    and applies configurable stock buffer rules (e.g. <= 75 -> 0, > 500 -> 500).
    """
    stock_col = stock_source if stock_source in ("stock_ct", "stock_dbn", "stock_jhb", "quantity") else "stock_ct"

    # Check optional columns on amazon_active_listings so query never fails on schema variations
    existing_cols = set(
        run_query("SHOW COLUMNS FROM amazon_active_listings")["Field"].astype(str).tolist()
    )
    has_prod_type = "product_type" in existing_cols
    has_brand = "brand" in existing_cols
    has_full_attr = "full_attributes_json" in existing_cols

    prof_cols = set(
        run_query("SHOW COLUMNS FROM inventory_item_profitability")["Field"].astype(str).tolist()
    )
    if "amazon_actual_price" in prof_cols and "amazon_rec_price" in prof_cols:
        price_expr = "COALESCE(NULLIF(p.amazon_actual_price, 0), NULLIF(p.amazon_rec_price, 0), al.current_price, 0)"
    elif "amazon_selling_price" in prof_cols:
        price_expr = "COALESCE(NULLIF(p.amazon_selling_price, 0), al.current_price, 0)"
    else:
        price_expr = "COALESCE(al.current_price, 0)"

    base_query = f"""
        SELECT
            COALESCE(NULLIF(al.sku, ''), p.sku) AS sku,
            COALESCE(al.asin, '') AS asin,
            COALESCE(NULLIF(al.item_name, ''), NULLIF(i.title, ''), i.description, '') AS item_name,
            {"COALESCE(NULLIF(al.product_type, ''), 'PRODUCT')" if has_prod_type else "'PRODUCT'"} AS product_type,
            {"COALESCE(al.brand, '')" if has_brand else "''"} AS brand,
            {price_expr} AS current_price,
            i.{stock_col} AS raw_stock,
            {"al.full_attributes_json" if has_full_attr else "NULL"} AS full_attributes_json
        FROM inventory_item_profitability p
        LEFT JOIN inventory_items i ON (p.sku = i.sku OR p.sku = i.item_code)
        LEFT JOIN amazon_active_listings al ON p.sku = al.sku
        WHERE ((p.sku IS NOT NULL AND p.sku != '') OR (al.sku IS NOT NULL AND al.sku != ''))
    """

    params: Dict[str, Any] = {}
    expanding: List[str] = []
    if skus:
        clean_skus = tuple({str(s).strip() for s in skus if str(s).strip()})
        if not clean_skus:
            return []
        base_query += " AND (p.sku IN :skus OR al.sku IN :skus)"
        params["skus"] = clean_skus
        expanding.append("skus")

    df = run_query(base_query, params=params, expanding_keys=expanding)
    if df.empty:
        return []

    rows: List[Dict[str, Any]] = []
    for _, r in df.iterrows():
        row = r.to_dict()
        raw_stock = row.get("raw_stock")
        if raw_stock is None or pd.isna(raw_stock):
            row["quantity"] = 0
        else:
            s = str(raw_stock).lower().strip()
            if "over 500" in s or ("500" in s and "unit" in s):
                row["quantity"] = max_stock_cap
            else:
                try:
                    num = float(re.sub(r"[^\d.]", "", s))
                    if num <= min_stock_cutoff:
                        row["quantity"] = 0
                    elif num > max_stock_cap:
                        row["quantity"] = max_stock_cap
                    else:
                        row["quantity"] = int(num)
                except (ValueError, TypeError):
                    row["quantity"] = 0

        row["current_price"] = float(row.get("current_price") or 0.0)
        row["currency"] = "ZAR"
        row["product_type"] = str(row.get("product_type") or "PRODUCT")

        raw_attr_json = row.get("full_attributes_json")
        if raw_attr_json and isinstance(raw_attr_json, str):
            try:
                row["parsed_attributes"] = json.loads(raw_attr_json)
            except json.JSONDecodeError:
                row["parsed_attributes"] = {}
        else:
            row["parsed_attributes"] = {}

        rows.append(row)

    return rows


def fetch_live_amazon_listing_snapshot(
    headers: dict,
    sp_endpoint: str,
    seller_id: str,
    marketplace_id: str,
    sku: str,
) -> Dict[str, Any]:
    """Fetches current live price, quantity, and productType from Amazon Listings Items API v2021-08-01."""
    encoded_sku = quote(str(sku).strip(), safe="")
    url = f"{sp_endpoint}/listings/2021-08-01/items/{seller_id.strip()}/{encoded_sku}"
    try:
        resp = requests.get(
            url,
            headers=headers,
            params={
                "marketplaceIds": marketplace_id,
                "includedData": "offers,fulfillmentAvailability,summaries",
            },
            timeout=20,
        )
        if resp.status_code == 429:
            time.sleep(2.0)
            resp = requests.get(
                url,
                headers=headers,
                params={
                    "marketplaceIds": marketplace_id,
                    "includedData": "offers,fulfillmentAvailability,summaries",
                },
                timeout=20,
            )

        if resp.status_code != 200:
            return {
                "live_price": 0.0,
                "live_qty": 0,
                "live_product_type": "",
                "status": f"HTTP {resp.status_code}",
            }

        payload = resp.json()
        live_price = 0.0
        live_qty = 0
        live_ptype = ""

        offers = payload.get("offers", [])
        if offers:
            live_price = float(offers[0].get("price", {}).get("amount", 0.0))

        fulfillment = payload.get("fulfillmentAvailability", [])
        if fulfillment:
            live_qty = int(fulfillment[0].get("quantity", 0))

        summaries = payload.get("summaries", [])
        if summaries and summaries[0].get("productType"):
            live_ptype = str(summaries[0]["productType"])

        return {
            "live_price": live_price,
            "live_qty": live_qty,
            "live_product_type": live_ptype,
            "status": "FOUND",
        }
    except Exception as exc:
        return {"live_price": 0.0, "live_qty": 0, "live_product_type": "", "status": f"ERROR: {exc}"}


def audit_mysql_vs_live_amazon(
    products: List[Dict[str, Any]],
    seller_id: str,
    marketplace_id: str,
    dry_run: bool = True,
    update_type: str = "both",
    progress_callback=None,
) -> Tuple[List[Dict[str, Any]], pd.DataFrame]:
    """Audits MySQL target values against live SP-API listing values and builds a delta report DataFrame."""
    if not seller_id.strip():
        raise ValueError("Amazon Seller / Merchant ID (`AMAZON_SELLER_ID`) is required.")

    headers, sp_endpoint = _get_sp_api_headers_and_endpoint()
    report_rows = []
    items_to_update = []
    total = len(products)

    for idx, prod in enumerate(products, start=1):
        sku = prod["sku"]
        target_price = float(prod.get("current_price", 0.0))
        target_qty = int(prod.get("quantity", 0))

        live_data = fetch_live_amazon_listing_snapshot(headers, sp_endpoint, seller_id, marketplace_id, sku)
        live_price = live_data["live_price"]
        live_qty = live_data["live_qty"]

        if live_data.get("live_product_type") and prod.get("product_type") in ("", "PRODUCT", None):
            prod["product_type"] = live_data["live_product_type"]

        price_changed = (
            (abs(target_price - live_price) > 0.01 and target_price > 0)
            if update_type in ("price", "both", "all")
            else False
        )
        qty_changed = (target_qty != live_qty) if update_type in ("quantity", "both", "all") else False
        attr_changed = update_type in ("title", "bullets", "description", "dimensions", "weight", "all")
        is_modified = price_changed or qty_changed or attr_changed

        report_rows.append({
            "SKU": sku,
            "ASIN": prod.get("asin", ""),
            "Product_Type": prod.get("product_type", "PRODUCT"),
            "Live_Listing_Status": live_data["status"],
            "Live_Price_ZAR": live_price,
            "New_Target_Price_ZAR": target_price if update_type in ("price", "both", "all") else None,
            "Price_Delta_ZAR": round(target_price - live_price, 2) if update_type in ("price", "both", "all") else 0.0,
            "Live_Quantity": live_qty,
            "New_Target_Quantity": target_qty if update_type in ("quantity", "both", "all") else None,
            "Quantity_Delta": (target_qty - live_qty) if update_type in ("quantity", "both", "all") else 0,
            "Update_Scope": update_type.upper(),
            "Status": "PENDING_UPDATE" if is_modified else "NO_CHANGE",
            "Execution_Mode": "DRY_RUN (Simulated)" if dry_run else "LIVE_SUBMISSION",
        })

        if is_modified:
            items_to_update.append(prod)

        if progress_callback:
            progress_callback(idx, total, sku)

        if idx < total:
            time.sleep(0.2)

    return items_to_update, pd.DataFrame(report_rows)


def build_universal_listings_feed(
    products: List[Dict[str, Any]],
    seller_id: str,
    marketplace_id: str = "AE08WJ6YNG13I",
    update_type: str = "both",
) -> Dict[str, Any]:
    """Constructs a JSON_LISTINGS_FEED (v2.0 PARTIAL_UPDATE) payload supporting any attribute scope."""
    messages = []

    for idx, prod in enumerate(products, start=1):
        sku = prod["sku"]
        price = float(prod.get("current_price", 0.0))
        qty = int(prod.get("quantity", 0))
        product_type = prod.get("product_type") or "PRODUCT"
        currency = prod.get("currency") or "ZAR"
        parsed_attr = prod.get("parsed_attributes", {})

        attributes: Dict[str, Any] = {}

        if update_type in ("price", "both", "all") and price > 0:
            attributes["purchasable_offer"] = [
                {
                    "currency": currency,
                    "our_price": [{"schedule": [{"value_with_tax": round(price, 2)}]}],
                    "marketplace_id": marketplace_id,
                }
            ]

        if update_type in ("quantity", "both", "all"):
            attributes["fulfillment_availability"] = [
                {
                    "fulfillment_channel_code": "DEFAULT",
                    "quantity": qty,
                }
            ]

        if update_type in ("title", "all") and prod.get("item_name"):
            attributes["item_name"] = [{"value": prod["item_name"], "language_tag": "en_ZA"}]

        if update_type in ("bullets", "all") and "bullet_point" in parsed_attr:
            attributes["bullet_point"] = parsed_attr["bullet_point"]

        if update_type in ("description", "all") and "description" in parsed_attr:
            attributes["description"] = parsed_attr["description"]

        if update_type in ("dimensions", "all"):
            if "item_dimensions" in parsed_attr:
                attributes["item_dimensions"] = parsed_attr["item_dimensions"]
            if "package_dimensions" in parsed_attr:
                attributes["package_dimensions"] = parsed_attr["package_dimensions"]

        if update_type in ("weight", "all") and "package_weight" in parsed_attr:
            attributes["package_weight"] = parsed_attr["package_weight"]

        if update_type == "all" and parsed_attr:
            for k, v in parsed_attr.items():
                if k not in attributes:
                    attributes[k] = v

        if attributes:
            messages.append({
                "messageId": idx,
                "sku": sku,
                "operationType": "PARTIAL_UPDATE",
                "productType": product_type,
                "attributes": attributes,
            })

    return {
        "header": {"sellerId": seller_id.strip(), "version": "2.0"},
        "messages": messages,
    }


def submit_bulk_json_listings_feed(
    feed_content: Dict[str, Any],
    marketplace_id: str = "AE08WJ6YNG13I",
    triggered_by: str = "admin",
    max_retries: int = 3,
) -> Dict[str, Any]:
    """
    Uploads and submits a JSON_LISTINGS_FEED via Amazon SP-API Feeds v2021-06-30:
      1. POST /feeds/2021-06-30/documents
      2. PUT presigned upload URL
      3. POST /feeds/2021-06-30/feeds
    """
    if not feed_content.get("messages"):
        return {"feed_id": None, "feed_document_id": None, "message_count": 0}

    headers, sp_endpoint = _get_sp_api_headers_and_endpoint()
    content_type = "application/json; charset=UTF-8"
    feed_json_bytes = json.dumps(feed_content).encode("utf-8")

    for attempt in range(1, max_retries + 1):
        try:
            doc_resp = requests.post(
                f"{sp_endpoint}/feeds/2021-06-30/documents",
                headers=headers,
                json={"contentType": content_type},
                timeout=30,
            )
            if doc_resp.status_code == 429:
                time.sleep(10)
                continue
            if doc_resp.status_code not in (200, 201):
                raise RuntimeError(f"Create Feed Document failed ({doc_resp.status_code}): {doc_resp.text[:400]}")

            doc_data = doc_resp.json()
            feed_document_id = doc_data["feedDocumentId"]
            upload_url = doc_data["url"]

            up_resp = requests.put(
                upload_url,
                data=feed_json_bytes,
                headers={"Content-Type": content_type},
                timeout=45,
            )
            up_resp.raise_for_status()

            sub_resp = requests.post(
                f"{sp_endpoint}/feeds/2021-06-30/feeds",
                headers=headers,
                json={
                    "feedType": "JSON_LISTINGS_FEED",
                    "marketplaceIds": [marketplace_id],
                    "inputFeedDocumentId": feed_document_id,
                },
                timeout=30,
            )
            if sub_resp.status_code == 429:
                time.sleep(10)
                continue
            if sub_resp.status_code not in (200, 202):
                raise RuntimeError(f"Create Feed failed ({sub_resp.status_code}): {sub_resp.text[:400]}")

            feed_id = sub_resp.json().get("feedId")
            msg_count = len(feed_content["messages"])

            execute_sql(
                "INSERT INTO channel_sync_logs (channel, job_type, status, items_processed) VALUES ('AMAZON_ZA', 'JSON_LISTINGS_FEED', 'SUCCESS', :cnt)",
                {"cnt": msg_count},
            )
            log_audit(
                triggered_by,
                "AMAZON_BULK_FEED_SUBMITTED",
                f"Submitted JSON_LISTINGS_FEED {feed_id}",
                f"Messages: {msg_count}, Document: {feed_document_id}",
                msg_count,
            )
            return {
                "feed_id": feed_id,
                "feed_document_id": feed_document_id,
                "message_count": msg_count,
            }
        except RuntimeError:
            raise
        except Exception as exc:
            if attempt == max_retries:
                raise RuntimeError(f"Feed submission failed after {max_retries} attempts: {exc}") from exc
            time.sleep(5)

    raise RuntimeError("Amazon Feeds API throttled repeatedly (HTTP 429).")