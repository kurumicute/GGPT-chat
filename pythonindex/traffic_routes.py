"""健康檢查與匿名訪客流量紀錄。"""

import hashlib
import threading

from flask import Blueprint, jsonify, request

from config import TRACKED_TRAFFIC_PATHS, TRAFFIC_SALT
from database import get_conn
from security import client_ip


bp = Blueprint("traffic", __name__)

def _request_client_ip():
    return client_ip()


def _visitor_hash():
    raw = f"{TRAFFIC_SALT}|{_request_client_ip()}|{request.headers.get('User-Agent','')}"
    return hashlib.sha256(raw.encode("utf-8", errors="ignore")).hexdigest()


def _detect_device(ua):
    u = (ua or "").lower()
    if any(x in u for x in ("bot", "spider", "crawler", "headless")):
        return "bot"
    if "ipad" in u or "tablet" in u or ("android" in u and "mobile" not in u):
        return "平板"
    if "mobile" in u or "iphone" in u or "android" in u:
        return "手機"
    return "PC"


def _detect_browser(ua):
    u = (ua or "").lower()
    if "edg/" in u:
        return "Edge"
    if "chrome/" in u and "chromium" not in u:
        return "Chrome"
    if "firefox/" in u:
        return "Firefox"
    if "safari/" in u and "chrome/" not in u and "edg/" not in u:
        return "Safari"
    if "opera" in u or "opr/" in u:
        return "Opera"
    return "Other"


def _detect_os(ua):
    u = (ua or "").lower()
    if "windows" in u:
        return "Windows"
    if "mac os" in u or "macintosh" in u:
        return "macOS"
    if "android" in u:
        return "Android"
    if "iphone" in u or "ipad" in u or "ios" in u:
        return "iOS"
    if "linux" in u:
        return "Linux"
    return "Other"


def _write_visitor_event(visitor_hash, path, referrer, ua, device_type, browser, os_name):
    conn = None
    cur = None
    try:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO visitor_events
            (visitor_hash,path,referrer,user_agent,device_type,browser,os)
            VALUES (%s,%s,%s,%s,%s,%s,%s)
        """, (visitor_hash, path, referrer, ua, device_type, browser, os_name))
        conn.commit()
    except Exception:
        if conn:
            conn.rollback()
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()


def _record_visitor_event():
    if request.method not in {"GET", "HEAD"}:
        return
    path = request.path or "/"
    if path.startswith("/admin") or path.startswith("/api/") or path.startswith("/uploads/"):
        return
    if path.endswith((".js", ".css", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico")):
        return
    if path not in TRACKED_TRAFFIC_PATHS and not path.startswith("/page/"):
        return

    ua = request.headers.get("User-Agent", "")[:1024]
    referrer = request.referrer[:1024] if request.referrer else None
    visitor_hash = _visitor_hash()
    device_type = _detect_device(ua)
    browser = _detect_browser(ua)
    os_name = _detect_os(ua)

    threading.Thread(
        target=_write_visitor_event,
        args=(visitor_hash, path, referrer, ua, device_type, browser, os_name),
        daemon=True,
    ).start()


@bp.before_app_request
def track_visitor():
    _record_visitor_event()


# ---------- app ----------
@bp.route("/health")
def health():
    return jsonify({"success": True, "service": "GGPT Flask API"})
