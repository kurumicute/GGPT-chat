"""一般帳號、Google 登入與工作階段 API。"""

import uuid

import mysql.connector
from flask import Blueprint, jsonify, request, session
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token
from werkzeug.security import check_password_hash, generate_password_hash

from config import GOOGLE_CLIENT_ID
from database import ensure_user_has_conversation, get_conn


bp = Blueprint("auth", __name__)

@bp.route("/register", methods=["POST"])
def register():
    data = request.get_json(silent=True) or {}
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""

    if not username or not password:
        return jsonify({"error": "需要帳號和密碼！"}), 400
    if len(username) > 100 or len(password) < 4:
        return jsonify({"error": "帳號長度或密碼不符合要求。"}), 400

    conn = None
    cur = None
    try:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO users (username,password_hash,display_name,auth_provider) VALUES (%s,%s,%s,%s)",
            (username, generate_password_hash(password), username, "local"),
        )
        conn.commit()
        return jsonify({"success": True})
    except mysql.connector.IntegrityError:
        return jsonify({"error": "帳號已存在！"}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()


@bp.route("/login", methods=["POST"])
def login():
    data = request.get_json(silent=True) or {}
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""

    conn = None
    cur = None
    try:
        conn = get_conn()
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT * FROM users WHERE username=%s", (username,))
        user = cur.fetchone()
        if not user or not check_password_hash(user["password_hash"], password):
            return jsonify({"error": "帳號或密碼錯誤！"}), 401

        session.clear()
        session["user_id"] = user["id"]
        session["username"] = user["username"]
        ensure_user_has_conversation(user["id"])
        return jsonify({"success": True, "username": user["username"]})
    except Exception as e:
        return jsonify({"error": str(e)}), 500
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
def google_login():
    if not GOOGLE_CLIENT_ID:
        return jsonify({"error": "Google 登入尚未在伺服器設定 GOOGLE_CLIENT_ID。"}), 503

    data = request.get_json(silent=True) or {}
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
    except Exception as e:
        return jsonify({"error": f"Google 登入驗證失敗：{e}"}), 500


@bp.route("/logout")
def logout():
    session.clear()
    return jsonify({"success": True})
