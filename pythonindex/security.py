"""輸入驗證與 HTTP 安全共用函式。"""

import ipaddress
import re
import unicodedata
from urllib.parse import urlsplit

from flask import current_app, jsonify, request
from werkzeug.security import check_password_hash, generate_password_hash

from config import (
    ALLOWED_ORIGINS,
    PASSWORD_MAX_LENGTH,
    PASSWORD_MIN_LENGTH,
    USERNAME_MAX_LENGTH,
    USERNAME_MIN_LENGTH,
)


COMMON_USERNAMES = {
    "admin", "administrator", "root", "system", "user", "username",
    "test", "tester", "demo", "guest", "support", "webmaster",
    "管理員", "系統", "測試", "訪客",
}

COMMON_PASSWORDS = {
    "12345678", "123456789", "1234567890", "00000000", "11111111",
    "password", "password1", "passw0rd", "qwerty123", "qwertyuiop",
    "abc12345", "admin123", "administrator", "iloveyou", "letmein",
    "welcome1", "changeme", "test1234", "user1234",
}

USERNAME_PATTERN = re.compile(r"^[\w.-]+$", re.UNICODE)
EMAIL_PATTERN = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")

# 無論帳號是否存在都執行一次相近成本的雜湊比對，降低帳號探測的時間差。
DUMMY_PASSWORD_HASH = generate_password_hash("not-a-real-user-password", method="scrypt")


def normalize_username(value):
    return unicodedata.normalize("NFKC", str(value or "")).strip()


def validate_username(value):
    username = normalize_username(value)
    if not username:
        return username, "請輸入帳號。"
    if not USERNAME_MIN_LENGTH <= len(username) <= USERNAME_MAX_LENGTH:
        return username, f"帳號長度需為 {USERNAME_MIN_LENGTH}～{USERNAME_MAX_LENGTH} 個字元。"
    if not USERNAME_PATTERN.fullmatch(username):
        return username, "帳號只能使用中英文字、數字、底線、句點與連字號。"
    if not username[0].isalnum() or not username[-1].isalnum():
        return username, "帳號開頭與結尾必須是文字或數字。"
    if username.casefold() in COMMON_USERNAMES:
        return username, "此帳號過於常見，請換一個較不容易猜到的帳號。"
    return username, None


def _simplified_secret(value):
    return re.sub(r"[^a-z0-9]", "", value.casefold())


def validate_password(password, username=""):
    if not isinstance(password, str):
        return "密碼格式不正確。"
    if not PASSWORD_MIN_LENGTH <= len(password) <= PASSWORD_MAX_LENGTH:
        return f"密碼長度需為 {PASSWORD_MIN_LENGTH}～{PASSWORD_MAX_LENGTH} 個字元。"
    if any(unicodedata.category(char).startswith("C") for char in password):
        return "密碼不能包含控制字元。"

    simplified = _simplified_secret(password)
    if password.casefold() in COMMON_PASSWORDS or simplified in COMMON_PASSWORDS:
        return "這組密碼太常見，請換一組較不容易猜到的密碼。"
    if username and simplified == _simplified_secret(normalize_username(username)):
        return "密碼不能與帳號相同。"
    if len(set(password)) == 1:
        return "密碼不能全部使用相同字元。"
    return None


def validate_credentials(username, password):
    normalized_username, error = validate_username(username)
    if error:
        return normalized_username, error
    return normalized_username, validate_password(password, normalized_username)


def validate_email(value):
    email = str(value or "").strip().lower()
    if not email:
        return None, None
    if len(email) > 254 or not EMAIL_PATTERN.fullmatch(email):
        return email, "Email 格式不正確。"
    return email, None


def hash_password(password):
    return generate_password_hash(password, method="scrypt")


def constant_time_password_check(stored_hash, password):
    return check_password_hash(stored_hash or DUMMY_PASSWORD_HASH, password)


def client_ip():
    """Cloudflare Tunnel 可使用 CF-Connecting-IP；無效值一律忽略。"""
    candidates = [
        request.headers.get("CF-Connecting-IP", ""),
        request.remote_addr or "",
    ]
    for candidate in candidates:
        try:
            return str(ipaddress.ip_address(candidate.strip()))
        except ValueError:
            continue
    return "unknown"


def protect_cross_site_request():
    """攔截跨站寫入，降低 Cookie Session 遭 CSRF 利用的風險。"""
    if request.method not in {"POST", "PUT", "PATCH", "DELETE"}:
        return None

    if request.headers.get("Sec-Fetch-Site", "").lower() == "cross-site":
        return jsonify({"error": "已拒絕跨網站請求。"}), 403

    origin = (request.headers.get("Origin") or "").rstrip("/")
    if not origin:
        return None

    try:
        parsed = urlsplit(origin)
        origin_host = parsed.netloc.casefold()
        request_host = request.host.casefold()
    except ValueError:
        return jsonify({"error": "請求來源格式不正確。"}), 403

    if origin_host != request_host and origin not in ALLOWED_ORIGINS:
        return jsonify({"error": "此網站來源未獲允許。"}), 403
    return None


def apply_security_headers(response):
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    response.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; base-uri 'self'; object-src 'none'; frame-ancestors 'none'; "
        "script-src 'self' https://accounts.google.com/gsi/client; "
        "frame-src https://accounts.google.com/gsi/; "
        "style-src 'self' 'unsafe-inline' https://accounts.google.com/gsi/style; "
        "img-src 'self' data: blob: https:; connect-src 'self' https://accounts.google.com/gsi/",
    )
    if request.is_secure:
        response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    if request.path.startswith(("/login", "/register", "/check_session", "/api/admin")):
        response.headers.setdefault("Cache-Control", "no-store")
    return response


def safe_server_error(message="伺服器暫時無法處理請求，請稍後再試。"):
    current_app.logger.exception("request failed")
    return jsonify({"error": message}), 500
