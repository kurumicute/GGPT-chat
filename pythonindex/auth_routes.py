"""一般帳號、Google 登入與工作階段 API。"""

import uuid

import mysql.connector
from flask import Blueprint, current_app, jsonify, request, session
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token
from werkzeug.security import generate_password_hash

from config import GOOGLE_CLIENT_ID, PASSWORD_MAX_LENGTH
from database import ensure_user_has_conversation, get_conn
from extensions import limiter
from security import (
    DUMMY_PASSWORD_HASH,
    constant_time_password_check,
    hash_password,
    normalize_username,
    validate_credentials,
)


bp = Blueprint("auth", __name__)

@bp.route("/register", methods=["POST"])
@limiter.limit("5 per 10 minutes")
def register():
    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        return jsonify({"error": "請求格式不正確。"}), 400
    password = data.get("password")
    username, validation_error = validate_credentials(data.get("username"), password)

    if validation_error:
        return jsonify({"error": validation_error}), 400

    conn = None
    cur = None
    try:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO users (username,password_hash,display_name,auth_provider) VALUES (%s,%s,%s,%s)",
            (username, hash_password(password), username, "local"),
        )
        conn.commit()
        return jsonify({"success": True})
    except mysql.connector.IntegrityError:
        return jsonify({"error": "此帳號無法使用，請換一個帳號。"}), 409
    except Exception:
        current_app.logger.exception("register failed")
        return jsonify({"error": "註冊暫時失敗，請稍後再試。"}), 500
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()


@bp.route("/login", methods=["POST"])
@limiter.limit("10 per minute; 50 per hour")
def login():
    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        return jsonify({"error": "帳號或密碼錯誤！"}), 401
    username = normalize_username(data.get("username"))
    password = data.get("password")

    if not username or not isinstance(password, str) or len(password) > PASSWORD_MAX_LENGTH:
        # 維持相同回應，避免協助判斷帳號是否存在。
        constant_time_password_check(DUMMY_PASSWORD_HASH, str(password or ""))
        return jsonify({"error": "帳號或密碼錯誤！"}), 401

    conn = None
    cur = None
    try:
        conn = get_conn()
        cur = conn.cursor(dictionary=True)
        cur.execute(
            "SELECT id,username,password_hash FROM users WHERE username=%s LIMIT 1",
            (username,),
        )
        user = cur.fetchone()
        stored_hash = user["password_hash"] if user else DUMMY_PASSWORD_HASH
        if not constant_time_password_check(stored_hash, password) or not user:
            return jsonify({"error": "帳號或密碼錯誤！"}), 401

        session.clear()
        session.permanent = True
        session["user_id"] = user["id"]
        session["username"] = user["username"]
        ensure_user_has_conversation(user["id"])
        return jsonify({"success": True, "username": user["username"]})
    except Exception:
        current_app.logger.exception("login failed")
        return jsonify({"error": "登入暫時失敗，請稍後再試。"}), 500
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()


@bp.route("/check_session")
def check_session():
    if "user_id" not in session:
        return jsonify({"logged_in": False})

    conn = None
    cur = None
    try:
        conn = get_conn()
        cur = conn.cursor(dictionary=True)
        cur.execute(
            "SELECT username,display_name,email,avatar_url,auth_provider FROM users WHERE id=%s",
            (session["user_id"],),
        )
        user = cur.fetchone()
        if not user:
            session.clear()
            return jsonify({"logged_in": False})
        return jsonify({
            "logged_in": True,
            "username": user["username"],
            "display_name": user.get("display_name") or user["username"],
            "email": user.get("email"),
            "avatar_url": user.get("avatar_url"),
            "auth_provider": user.get("auth_provider") or "local",
        })
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()


# ---------- Google Login ----------
@bp.route("/auth/google/config")
def google_config():
    return jsonify({
        "enabled": bool(GOOGLE_CLIENT_ID),
        "client_id": GOOGLE_CLIENT_ID if GOOGLE_CLIENT_ID else None,
    })


def make_google_username(cur, google_sub, email):
    base = "google_" + google_sub[:24]
    candidate = base
    suffix = 1
    while True:
        cur.execute("SELECT id FROM users WHERE username=%s", (candidate,))
        if cur.fetchone() is None:
            return candidate
        candidate = f"{base}_{suffix}"
        suffix += 1


@bp.route("/auth/google", methods=["POST"])
@limiter.limit("10 per minute")
def google_login():
    if not GOOGLE_CLIENT_ID:
        return jsonify({"error": "Google 登入尚未在伺服器設定 GOOGLE_CLIENT_ID。"}), 503

    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        return jsonify({"error": "請求格式不正確。"}), 400
    credential = (data.get("credential") or "").strip()
    if not credential:
        return jsonify({"error": "缺少 Google ID Token。"}), 400

    try:
        token = id_token.verify_oauth2_token(
            credential,
            google_requests.Request(),
            GOOGLE_CLIENT_ID,
        )

        if token.get("iss") not in {"accounts.google.com", "https://accounts.google.com"}:
            return jsonify({"error": "Google ID Token issuer 不正確。"}), 401

        google_sub = (token.get("sub") or "").strip()
        email = (token.get("email") or "").strip().lower()
        email_verified = bool(token.get("email_verified"))
        display_name = (token.get("name") or email or "Google 使用者").strip()[:255]
        avatar_url = (token.get("picture") or "").strip()[:1024] or None

        if not google_sub:
            return jsonify({"error": "Google Token 缺少使用者識別碼。"}), 401
        if not email or not email_verified:
            return jsonify({"error": "Google 帳號的已驗證 Email 無法取得，無法完成登入。"}), 401

        conn = get_conn()
        cur = conn.cursor(dictionary=True)
        try:
            cur.execute("SELECT * FROM users WHERE google_sub=%s LIMIT 1", (google_sub,))
            user = cur.fetchone()

            if user is None:
                cur.execute("SELECT * FROM users WHERE email=%s LIMIT 1", (email,))
                user = cur.fetchone()

                if user is not None:
                    if user.get("email") and user.get("auth_provider") not in ("google", "local+google"):
                        return jsonify({
                            "error": "此 Email 已存在本地帳號，請先使用原本的帳號密碼登入，再進行 Google 綁定。"
                        }), 409
                    cur.execute("""
                        UPDATE users
                        SET google_sub=%s, email=%s, display_name=%s, avatar_url=%s, auth_provider=%s
                        WHERE id=%s
                    """, (google_sub, email, display_name, avatar_url, "local+google", user["id"]))
                    user["google_sub"] = google_sub
                    user["email"] = email
                    user["display_name"] = display_name
                    user["avatar_url"] = avatar_url
                    user["auth_provider"] = "local+google"
                else:
                    username = make_google_username(cur, google_sub, email)
                    cur.execute("""
                        INSERT INTO users
                        (username,password_hash,google_sub,email,display_name,avatar_url,auth_provider)
                        VALUES (%s,%s,%s,%s,%s,%s,%s)
                    """, (
                        username,
                        generate_password_hash(uuid.uuid4().hex),
                        google_sub,
                        email,
                        display_name,
                        avatar_url,
                        "google",
                    ))
                    user = {
                        "id": cur.lastrowid,
                        "username": username,
                        "email": email,
                        "display_name": display_name,
                        "avatar_url": avatar_url,
                        "auth_provider": "google",
                    }

            cur.execute("""
                UPDATE users
                SET email=%s, display_name=%s, avatar_url=%s
                WHERE id=%s
            """, (email, display_name, avatar_url, user["id"]))

            conn.commit()
            session.clear()
            session.permanent = True
            session["user_id"] = user["id"]
            session["username"] = user["username"]
            ensure_user_has_conversation(user["id"])

            return jsonify({
                "success": True,
                "username": user["username"],
                "display_name": display_name,
                "email": email,
                "avatar_url": avatar_url,
                "auth_provider": user.get("auth_provider") or "google",
            })
        finally:
            cur.close()
            conn.close()

    except ValueError:
        return jsonify({"error": "Google ID Token 無效、已過期或 audience 不符合目前網站。"}), 401
    except Exception:
        current_app.logger.exception("google login failed")
        return jsonify({"error": "Google 登入暫時失敗，請稍後再試。"}), 500


@bp.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return jsonify({"success": True})
