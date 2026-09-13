"""管理員操作稽核紀錄。"""

import json
import logging
import os
from datetime import datetime
from logging.handlers import RotatingFileHandler

from flask import Blueprint, g, request, session

from config import BASE_DIR


bp = Blueprint("audit", __name__)
ADMIN_LOG_DIR = os.path.join(BASE_DIR, "admin_logs")
os.makedirs(ADMIN_LOG_DIR, exist_ok=True)
ADMIN_LOG_PATH = os.path.join(ADMIN_LOG_DIR, "admin_activity.log")

admin_logger = logging.getLogger("admin_activity")
admin_logger.setLevel(logging.INFO)
admin_logger.propagate = False
handler = None
if not admin_logger.handlers:
    handler = RotatingFileHandler(
        ADMIN_LOG_PATH,
        maxBytes=10 * 1024 * 1024,
        backupCount=10,
        encoding="utf-8",
    )
    handler.setFormatter(logging.Formatter("%(message)s"))
    admin_logger.addHandler(handler)

def _admin_client_ip():
    forwarded = request.headers.get("X-Forwarded-For", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.remote_addr or "unknown"


def _admin_safe_request_data():
    """保留稽核需要的資料，但絕不將密碼、token、secret 寫入 log。"""
    try:
        data = request.get_json(silent=True)
    except Exception:
        data = None
    if not isinstance(data, dict):
        return {}

    hidden_keys = {
        "password", "password_hash", "new_password", "old_password",
        "token", "access_token", "id_token", "credential",
        "secret", "api_key", "authorization",
    }
    safe = {}
    for key, value in data.items():
        key_text = str(key).lower()
        if key_text in hidden_keys or "password" in key_text or "token" in key_text:
            safe[str(key)] = "[REDACTED]"
        elif isinstance(value, str):
            safe[str(key)] = value[:500]
        elif isinstance(value, (int, float, bool)) or value is None:
            safe[str(key)] = value
        elif isinstance(value, list):
            safe[str(key)] = f"[list:{len(value)}]"
        elif isinstance(value, dict):
            safe[str(key)] = "[object]"
        else:
            safe[str(key)] = str(value)[:500]
    return safe


def _admin_operation_name(method, path):
    if path == "/api/admin/login":
        return "管理員登入"
    if path == "/api/admin/logout":
        return "管理員登出"
    if path == "/api/admin/dashboard":
        return "查看管理儀表板"
    if path == "/api/admin/traffic":
        return "查看訪客流量"
    if path == "/api/admin/users" and method == "POST":
        return "新增使用者"
    if path.startswith("/api/admin/users/") and method == "PATCH":
        return "修改使用者"
    if path.startswith("/api/admin/users/") and method == "DELETE":
        return "刪除使用者"
    return f"管理員 API {method}"


def _write_admin_log(status_code):
    path = request.path or ""
    if not path.startswith("/api/admin/") or path == "/api/admin/check":
        return

    actor_id = getattr(g, "admin_log_actor_id", None)
    actor_username = getattr(g, "admin_log_actor_username", None)
    if session.get("is_admin") and session.get("admin_id"):
        actor_id = session.get("admin_id")
        actor_username = session.get("admin_username")

    request_data = _admin_safe_request_data()
    details = {"request": request_data}

    if path.startswith("/api/admin/users/"):
        details["target_user_id"] = path.rsplit("/", 1)[-1]
        if request.method == "PATCH":
            details["changed_fields"] = list(request_data.keys())
    elif path == "/api/admin/users" and request.method == "POST":
        if request_data.get("username"):
            details["target_username"] = request_data["username"]

    if path == "/api/admin/login" and not actor_username:
        actor_username = request_data.get("username") or None

    event = {
        "timestamp": datetime.now().astimezone().isoformat(timespec="seconds"),
        "admin_id": int(actor_id) if str(actor_id).isdigit() else None,
        "admin_username": actor_username or "anonymous",
        "operation": _admin_operation_name(request.method, path),
        "method": request.method,
        "path": path,
        "status": int(status_code),
        "success": 200 <= int(status_code) < 400,
        "ip": _admin_client_ip(),
        "user_agent": request.headers.get("User-Agent", "")[:500],
        "details": details,
    }
    try:
        admin_logger.info(json.dumps(event, ensure_ascii=False, separators=(",", ":")))
    except Exception:
        pass



@bp.before_app_request
def capture_admin_actor():
    if request.path.startswith("/api/admin/"):
        g.admin_log_actor_id = session.get("admin_id")
        g.admin_log_actor_username = session.get("admin_username")


@bp.after_app_request
def audit_admin_request(response):
    _write_admin_log(response.status_code)
    return response
