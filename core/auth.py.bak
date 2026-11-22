import hashlib
import hmac
import json
import os

from core.database import execute_sql, run_query

try:
    import bcrypt
    HAS_BCRYPT = True
except Exception:
    HAS_BCRYPT = False

ALL_MODULES = [
    "📊 Executive & Financial Analytics",
    "🧭 Multi-Channel Wave Research",
    "📥 Supplier Excel Import & Exclusions",
    "🔫 Barcode Scan In / Out & Bins",
    "🏷️ Barcode & Shipping Label Printer",
    "🚚 The Courier Guy (Waybills & Tracking)",
    "💼 Xero Accounting Sync",
    "🛒 Channel Profitability & Listings",
    "🔍 Warehouse Operations (GRN, Picks, Cycle Counts)",
    "⚙️ DB Secrets, Credentials & Requirements",
    "🔐 Users, Roles & Granular Permissions",
]


def hash_password(plain_password: str) -> str:
    salt = os.urandom(16).hex()
    dk = hashlib.pbkdf2_hmac("sha256", plain_password.encode("utf-8"), salt.encode("utf-8"), 200_000).hex()
    return f"pbkdf2_sha256${salt}${dk}"


def verify_password(plain_password: str, stored_hash: str) -> bool:
    if not stored_hash or not plain_password:
        return False
    stored_hash = str(stored_hash).strip()

    if stored_hash.startswith("pbkdf2_sha256$"):
        try:
            _, salt, expected_dk = stored_hash.split("$", 2)
            dk = hashlib.pbkdf2_hmac("sha256", plain_password.encode("utf-8"), salt.encode("utf-8"), 200_000).hex()
            return hmac.compare_digest(dk, expected_dk)
        except Exception:
            return False

    if stored_hash.startswith(("$2a$", "$2b$", "$2y$")) and HAS_BCRYPT:
        try:
            return bcrypt.checkpw(plain_password.encode("utf-8"), stored_hash.encode("utf-8"))
        except Exception:
            return False

    if stored_hash.startswith("pbkdf2:sha256:"):
        try:
            header, salt, expected_dk = stored_hash.split("$", 2)
            iters = int(header.split(":")[2])
            dk = hashlib.pbkdf2_hmac("sha256", plain_password.encode("utf-8"), salt.encode("utf-8"), iters).hex()
            return hmac.compare_digest(dk, expected_dk)
        except Exception:
            return False

    if len(stored_hash) == 64:
        sha = hashlib.sha256(plain_password.encode("utf-8")).hexdigest()
        return hmac.compare_digest(sha, stored_hash.lower())

    return hmac.compare_digest(plain_password, stored_hash)


def load_user_permissions(username: str, role_id: str, role_name: str) -> dict:
    perms = set()
    allowed_tabs = []
    flags = {"can_save_profitability": False, "can_live_sync": False, "can_edit_table_settings": False}

    is_super_admin = str(role_id).lower() in ("admin", "superadmin", "super_admin") or str(role_name).upper() in (
        "ADMIN", "SUPERADMIN", "SUPER_ADMIN", "ADMINISTRATOR",
    )

    ar_df = run_query(
        "SELECT * FROM app_roles WHERE role_id = :rid OR UPPER(role_name) = UPPER(:rname) LIMIT 1",
        {"rid": role_id or "", "rname": role_name or ""},
    )
    if not ar_df.empty:
        ar = ar_df.iloc[0]
        raw_tabs = str(ar["allowed_tabs"] or "").strip()
        if raw_tabs == "*" or is_super_admin:
            allowed_tabs = list(ALL_MODULES)
        elif raw_tabs.startswith("["):
            try:
                allowed_tabs = json.loads(raw_tabs)
            except Exception:
                allowed_tabs = [t.strip() for t in raw_tabs.split(",") if t.strip()]
        else:
            allowed_tabs = [t.strip() for t in raw_tabs.split(",") if t.strip()]

        flags["can_save_profitability"] = bool(ar["can_save_profitability"])
        flags["can_live_sync"] = bool(ar["can_live_sync"])
        flags["can_edit_table_settings"] = bool(ar["can_edit_table_settings"])

    if is_super_admin:
        allowed_tabs = list(ALL_MODULES)
        flags = {k: True for k in flags}
        all_sp = run_query("SELECT permission_key FROM system_permissions")
        perms.update(all_sp["permission_key"].tolist())
        perms.add("*")

    rp_df = run_query(
        "SELECT permission_key FROM role_permissions WHERE UPPER(role_name) IN (UPPER(:rid), UPPER(:rname))",
        {"rid": role_id or "", "rname": role_name or ""},
    )
    if not rp_df.empty:
        perms.update(rp_df["permission_key"].astype(str).tolist())

    up_df = run_query(
        "SELECT permission FROM user_permissions WHERE UPPER(role) IN (UPPER(:rid), UPPER(:rname)) AND granted = 1",
        {"rid": role_id or "", "rname": role_name or ""},
    )
    if not up_df.empty:
        perms.update(up_df["permission"].astype(str).tolist())

    if not allowed_tabs:
        perm_to_module = {
            "view_analytics": "📊 Executive & Financial Analytics",
            "view_financials": "📊 Executive & Financial Analytics",
            "sync_amazon_finances": "📊 Executive & Financial Analytics",
            "upload_supplier_excel": "📥 Supplier Excel Import & Exclusions",
            "scan_inventory": "🔫 Barcode Scan In / Out & Bins",
            "print_labels": "🏷️ Barcode & Shipping Label Printer",
            "manage_courier": "🚚 The Courier Guy (Waybills & Tracking)",
            "sync_xero": "💼 Xero Accounting Sync",
            "view_profitability": "🛒 Channel Profitability & Listings",
            "view_ecomm_research": "🧭 Multi-Channel Wave Research",
            "warehouse_ops": "🔍 Warehouse Operations (GRN, Picks, Cycle Counts)",
            "manage_secrets": "⚙️ DB Secrets, Credentials & Requirements",
            "manage_users": "🔐 Users, Roles & Granular Permissions",
        }
        for pk, mod in perm_to_module.items():
            if pk in perms and mod not in allowed_tabs:
                allowed_tabs.append(mod)

    return {
        "is_super_admin": is_super_admin,
        "allowed_tabs": allowed_tabs,
        "permissions": sorted(list(perms)),
        "flags": flags,
    }


def has_perm(auth_user: dict, perm_key: str) -> bool:
    if not auth_user:
        return False
    if auth_user.get("is_super_admin") or "*" in auth_user.get("permissions", []):
        return True
    return perm_key in auth_user.get("permissions", [])


def authenticate_user(username_or_email: str, password: str):
    ident = username_or_email.strip()

    df = run_query(
        """
        SELECT id, username, full_name, email, password_hash, role_id, role, is_active, status
        FROM app_users
        WHERE LOWER(username) = LOWER(:ident) OR LOWER(email) = LOWER(:ident)
        LIMIT 1
        """,
        {"ident": ident},
    )
    if not df.empty:
        u = df.iloc[0]
        if int(u["is_active"] or 0) == 1 and str(u["status"] or "Active").lower() == "active":
            if verify_password(password, u["password_hash"]):
                rbac = load_user_permissions(u["username"], u["role_id"], u["role"])
                return {
                    "source_table": "app_users",
                    "id": int(u["id"]),
                    "username": u["username"],
                    "full_name": u["full_name"] or u["username"],
                    "email": u["email"] or "",
                    "role_id": u["role_id"],
                    "role_name": u["role"] or u["role_id"],
                    **rbac,
                }

    df_sys = run_query(
        """
        SELECT id, username, email, password_hash, role, is_active
        FROM system_users
        WHERE LOWER(username) = LOWER(:ident) OR LOWER(email) = LOWER(:ident)
        LIMIT 1
        """,
        {"ident": ident},
    )
    if not df_sys.empty:
        u = df_sys.iloc[0]
        if int(u["is_active"] or 0) == 1 and verify_password(password, u["password_hash"]):
            execute_sql("UPDATE system_users SET last_login = NOW() WHERE id = :id", {"id": int(u["id"])})
            rbac = load_user_permissions(u["username"], u["role"], u["role"])
            return {
                "source_table": "system_users",
                "id": int(u["id"]),
                "username": u["username"],
                "full_name": u["username"],
                "email": u["email"] or "",
                "role_id": u["role"],
                "role_name": u["role"],
                **rbac,
            }

    df_usr = run_query(
        """
        SELECT id, username, email, password_hash, role_id, role, active
        FROM users
        WHERE LOWER(username) = LOWER(:ident) OR LOWER(email) = LOWER(:ident)
        LIMIT 1
        """,
        {"ident": ident},
    )
    if not df_usr.empty:
        u = df_usr.iloc[0]
        if int(u["active"] or 0) == 1 and verify_password(password, u["password_hash"]):
            rbac = load_user_permissions(u["username"], u["role_id"] or u["role"], u["role"] or u["role_id"])
            return {
                "source_table": "users",
                "id": int(u["id"]),
                "username": u["username"],
                "full_name": u["username"],
                "email": u["email"] or "",
                "role_id": u["role_id"] or u["role"],
                "role_name": u["role"] or u["role_id"],
                **rbac,
            }

    return None
