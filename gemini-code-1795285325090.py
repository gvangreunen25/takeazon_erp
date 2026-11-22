#!/usr/bin/env python3
"""Adds `🧭 Multi-Channel Wave Research` to `ALL_MODULES` in `core/auth.py`, `system_permissions`, and `app_roles`."""

import json
from pathlib import Path
import re
import shutil
import sys

ROOT = Path("/Users/gjvangreunen/workspace/takeazon_erp")
sys.path.insert(0, str(ROOT))

from core.database import execute_sql, run_query

AUTH_PATH = ROOT / "core" / "auth.py"
RBAC_PATH = ROOT / "modules" / "users_rbac.py"
PAGE_LABEL = "🧭 Multi-Channel Wave Research"
PERM_KEY = "view_ecomm_research"


def patch_files_and_db():
    # 1. Patch ALL_MODULES and any slug dictionary in core/auth.py
    auth_txt = AUTH_PATH.read_text(encoding="utf-8")
    auth_updated = auth_txt

    if PAGE_LABEL not in auth_updated.split("def load_user_permissions")[0]:
        m_all = re.search(
            r'^([ \t]*)(["\'](?:📊 Executive & Financial Analytics|🛒 Channel Profitability & Listings)["\']\s*,?[ \t]*)$',
            auth_updated,
            flags=re.MULTILINE,
        )
        if m_all:
            indent, line = m_all.groups()
            line_comma = line if line.rstrip().endswith(",") else line.rstrip() + ","
            new_line = f'{indent}"{PAGE_LABEL}",'
            auth_updated = (
                auth_updated[: m_all.start()]
                + f"{indent}{line_comma}\n{new_line}"
                + auth_updated[m_all.end() :]
            )
            print(f"Added `{PAGE_LABEL}` to `ALL_MODULES` in `core/auth.py`.")
        else:
            print("[WARN] Could not auto-locate `ALL_MODULES` in `core/auth.py`. Top 65 lines:")
            for idx, l in enumerate(auth_txt.splitlines()[:65], 1):
                print(f"  L{idx}: {l}")

    m_slug = re.search(
        r'^([ \t]*)(["\']profitability["\']\s*:\s*["\']🛒 Channel Profitability & Listings["\']\s*,?[ \t]*)$',
        auth_updated,
        flags=re.MULTILINE,
    )
    if m_slug and '"ecomm_research"' not in auth_updated:
        s_indent, s_line = m_slug.groups()
        s_comma = s_line if s_line.rstrip().endswith(",") else s_line.rstrip() + ","
        new_slug_line = f'{s_indent}"ecomm_research": "{PAGE_LABEL}",'
        auth_updated = (
            auth_updated[: m_slug.start()]
            + f"{s_indent}{s_comma}\n{new_slug_line}"
            + auth_updated[m_slug.end() :]
        )
        print('Added `"ecomm_research"` slug mapping in `core/auth.py`.')

    if auth_updated != auth_txt:
        shutil.copy2(AUTH_PATH, AUTH_PATH.with_suffix(".py.bak"))
        AUTH_PATH.write_text(auth_updated, encoding="utf-8")

    # 2. Check if modules/users_rbac.py has its own hardcoded module list
    rbac_txt = RBAC_PATH.read_text(encoding="utf-8")
    if "Channel Profitability & Listings" in rbac_txt and PAGE_LABEL not in rbac_txt:
        m_rbac = re.search(
            r'^([ \t]*)(["\']🛒 Channel Profitability & Listings["\']\s*,?[ \t]*)$',
            rbac_txt,
            flags=re.MULTILINE,
        )
        if m_rbac:
            r_indent, r_line = m_rbac.groups()
            r_comma = r_line if r_line.rstrip().endswith(",") else r_line.rstrip() + ","
            rbac_updated = (
                rbac_txt[: m_rbac.start()]
                + f'{r_indent}{r_comma}\n{r_indent}"{PAGE_LABEL}",'
                + rbac_txt[m_rbac.end() :]
            )
            shutil.copy2(RBAC_PATH, RBAC_PATH.with_suffix(".py.bak"))
            RBAC_PATH.write_text(rbac_updated, encoding="utf-8")
            print(f"Added `{PAGE_LABEL}` to `modules/users_rbac.py`.")

    # 3. Ensure `view_ecomm_research` is in `system_permissions` and `role_permissions`
    try:
        execute_sql("""
            INSERT IGNORE INTO system_permissions (permission_key, module, description)
            VALUES ('view_ecomm_research', 'channels', 'Access Multi-Channel Wave Research & 10-Wave Planner')
        """)
        execute_sql("""
            INSERT IGNORE INTO role_permissions (role_name, permission_key)
            SELECT role_name, 'view_ecomm_research'
            FROM role_permissions
            WHERE permission_key = 'view_profitability'
        """)
        print("Registered `view_ecomm_research` in `system_permissions` and `role_permissions`.")
    except Exception as exc:
        print(f"[Note] `system_permissions` update: {exc}")

    # 4. Update `app_roles.allowed_tabs` (handles string role_id like 'admin' and JSON arrays)
    roles_df = run_query("SELECT role_id, role_name, allowed_tabs FROM app_roles")
    for _, r in roles_df.iterrows():
        rid = str(r["role_id"])
        raw = str(r.get("allowed_tabs") or "").strip()
        if raw == "*":
            continue
        if raw.startswith("["):
            try:
                arr = json.loads(raw)
                changed = False
                if "profitability" in arr and "ecomm_research" not in arr:
                    arr.append("ecomm_research")
                    changed = True
                if "🛒 Channel Profitability & Listings" in arr and PAGE_LABEL not in arr:
                    arr.append(PAGE_LABEL)
                    changed = True
                if changed:
                    execute_sql(
                        "UPDATE app_roles SET allowed_tabs = :t WHERE role_id = :rid",
                        {"t": json.dumps(arr), "rid": rid},
                    )
                    print(f"Updated JSON `allowed_tabs` in `app_roles` for `{r['role_name']}` ({rid}).")
            except Exception as e:
                print(f"[Warn] JSON parse for {rid}: {e}")
        elif PAGE_LABEL not in raw and raw:
            new_csv = raw.rstrip(",") + "," + PAGE_LABEL
            execute_sql(
                "UPDATE app_roles SET allowed_tabs = :t WHERE role_id = :rid",
                {"t": new_csv, "rid": rid},
            )
            print(f"Updated CSV `allowed_tabs` in `app_roles` for `{r['role_name']}` ({rid}).")


if __name__ == "__main__":
    patch_files_and_db()