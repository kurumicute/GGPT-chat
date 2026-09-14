"""管理員驗證、儀表板、訪客分析與使用者管理 API。"""

import mysql.connector
from flask import Blueprint, current_app, jsonify, render_template, request, session

from config import WEB_SEARCH_PRICE_PER_RUN
from database import get_conn
from extensions import limiter
from security import (
    DUMMY_PASSWORD_HASH,
    constant_time_password_check,
    hash_password,
    normalize_username,
    validate_credentials,
    validate_email,
    validate_password,
    validate_username,
)


bp = Blueprint("admin", __name__)

def require_admin():
    if not session.get("is_admin") or not session.get("admin_id"):
        return False

    conn = None
    cur = None
    try:
        conn = get_conn()
        cur = conn.cursor(dictionary=True)
        cur.execute(
            "SELECT is_active FROM admin_users WHERE id=%s LIMIT 1",
            (session.get("admin_id"),),
        )
        admin = cur.fetchone()
        return bool(admin and admin.get("is_active"))
    except Exception:
        return False
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()


def admin_error(message, status=401):
    return jsonify({"success": False, "error": message}), status


@bp.route("/admin-legacy")
def admin_index_legacy():
    return render_template("adminindex.html")


@bp.route("/api/admin/login", methods=["POST"])
@limiter.limit("5 per minute; 20 per hour")
def admin_login():
    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        return admin_error("請求格式不正確。", 400)
    username = str(data.get("username") or "").strip()
    password = str(data.get("password") or "")

    if not username or not password:
        return admin_error("請輸入管理員帳號與密碼。", 400)

    conn = get_conn()
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute("""
            SELECT id, username, password_hash, is_active
            FROM admin_users
            WHERE username=%s
            LIMIT 1
        """, (username,))
        admin = cur.fetchone()

        stored_hash = admin["password_hash"] if admin else DUMMY_PASSWORD_HASH
        password_ok = constant_time_password_check(stored_hash, password)
        if not admin or not bool(admin.get("is_active")) or not password_ok:
            return admin_error("管理員帳號或密碼錯誤。", 401)

        cur.execute(
            "UPDATE admin_users SET last_login_at=CURRENT_TIMESTAMP WHERE id=%s",
            (admin["id"],),
        )
        conn.commit()

        session.clear()
        session.permanent = True
        session["is_admin"] = True
        session["admin_username"] = admin["username"]
        session["admin_id"] = int(admin["id"])

        return jsonify({"success": True, "username": admin["username"]})
    except Exception:
        conn.rollback()
        current_app.logger.exception("admin login failed")
        return admin_error("管理員登入暫時失敗，請稍後再試。", 500)
    finally:
        cur.close()
        conn.close()


@bp.route("/api/admin/check")
def admin_check():
    return jsonify({"logged_in": require_admin(), "username": session.get("admin_username")})


@bp.route("/api/admin/logout", methods=["POST"])
def admin_logout():
    session.clear()
    return jsonify({"success": True})


@bp.route("/api/admin/dashboard")
def admin_dashboard():
    if not require_admin():
        return admin_error("沒有管理員權限。", 403)

    conn = get_conn()
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute("SELECT COUNT(*) AS total_users FROM users")
        total_users = int(cur.fetchone()["total_users"] or 0)

        cur.execute("SELECT COUNT(*) AS total_conversations FROM conversations")
        total_conversations = int(cur.fetchone()["total_conversations"] or 0)

        cur.execute("SELECT COUNT(*) AS total_messages FROM chat")
        total_messages = int(cur.fetchone()["total_messages"] or 0)

        cur.execute("""
            SELECT COALESCE(SUM(total_tokens),0) AS total_tokens,
                   COALESCE(SUM(input_tokens),0) AS input_tokens,
                   COALESCE(SUM(output_tokens),0) AS output_tokens,
                   COALESCE(SUM(reasoning_tokens),0) AS reasoning_tokens,
                   COALESCE(SUM(reasoning_cost),0) AS reasoning_cost,
                   COALESCE(SUM(total_cost - web_search_cost),0) AS model_cost,
                   COALESCE(SUM(total_cost),0) AS total_cost,
                   COALESCE(SUM(web_search_calls),0) AS web_search_calls,
                   COALESCE(SUM(web_search_cost),0) AS web_search_cost,
                   COUNT(*) AS api_requests
            FROM token_usage
        """)
        overall = cur.fetchone() or {}

        cur.execute("""
            SELECT COUNT(*) AS api_requests,
                   COALESCE(SUM(total_tokens),0) AS total_tokens,
                   COALESCE(SUM(total_cost),0) AS total_cost,
                   COALESCE(SUM(reasoning_tokens),0) AS reasoning_tokens,
                   COALESCE(SUM(reasoning_cost),0) AS reasoning_cost,
                   COALESCE(SUM(total_cost - web_search_cost),0) AS model_cost,
                   COALESCE(SUM(web_search_calls),0) AS web_search_calls,
                   COALESCE(SUM(web_search_cost),0) AS web_search_cost
            FROM token_usage
            WHERE created_at >= DATE_FORMAT(CURRENT_DATE, '%Y-%m-01')
        """)
        month = cur.fetchone() or {}

        # 使用者清單：回傳 Web Search 次數與金額，方便後台拆分成本。
        cur.execute("""
            SELECT
                u.id,
                u.username,
                COALESCE(u.display_name,u.username) AS display_name,
                u.email,
                u.auth_provider,
                COALESCE(c.conversation_count,0) AS conversation_count,
                COALESCE(m.message_count,0) AS message_count,
                COALESCE(t.api_requests,0) AS api_requests,
                COALESCE(t.total_tokens,0) AS total_tokens,
                COALESCE(t.input_tokens,0) AS input_tokens,
                COALESCE(t.output_tokens,0) AS output_tokens,
                COALESCE(t.reasoning_tokens,0) AS reasoning_tokens,
                COALESCE(t.reasoning_cost,0) AS reasoning_cost,
                COALESCE(t.model_cost,0) AS model_cost,
                COALESCE(t.total_cost,0) AS total_cost,
                COALESCE(t.web_search_calls,0) AS web_search_calls,
                COALESCE(t.web_search_cost,0) AS web_search_cost
            FROM users u
            LEFT JOIN (
                SELECT user_id, COUNT(*) AS conversation_count
                FROM conversations GROUP BY user_id
            ) c ON c.user_id=u.id
            LEFT JOIN (
                SELECT user_id, COUNT(*) AS message_count
                FROM chat GROUP BY user_id
            ) m ON m.user_id=u.id
            LEFT JOIN (
                SELECT user_id,
                       COUNT(*) AS api_requests,
                       SUM(total_tokens) AS total_tokens,
                       SUM(input_tokens) AS input_tokens,
                       SUM(output_tokens) AS output_tokens,
                       SUM(reasoning_tokens) AS reasoning_tokens,
                       SUM(reasoning_cost) AS reasoning_cost,
                       SUM(total_cost - web_search_cost) AS model_cost,
                       SUM(total_cost) AS total_cost,
                       SUM(web_search_calls) AS web_search_calls,
                       SUM(web_search_cost) AS web_search_cost
                FROM token_usage GROUP BY user_id
            ) t ON t.user_id=u.id
            ORDER BY total_tokens DESC, u.id ASC
        """)
        users = cur.fetchall()

        cur.execute("""
            SELECT model, COUNT(*) AS requests,
                   COALESCE(SUM(total_tokens),0) AS total_tokens,
                   COALESCE(SUM(total_cost),0) AS total_cost,
                   COALESCE(SUM(reasoning_tokens),0) AS reasoning_tokens,
                   COALESCE(SUM(reasoning_cost),0) AS reasoning_cost,
                   COALESCE(SUM(total_cost - web_search_cost),0) AS model_cost,
                   COALESCE(SUM(web_search_calls),0) AS web_search_calls,
                   COALESCE(SUM(web_search_cost),0) AS web_search_cost
            FROM token_usage
            GROUP BY model
            ORDER BY total_tokens DESC, requests DESC
            LIMIT 10
        """)
        models = cur.fetchall()

        cur.execute("""
            SELECT DATE_FORMAT(created_at,'%Y-%m-%d') AS day,
                   COUNT(*) AS requests,
                   COALESCE(SUM(total_tokens),0) AS total_tokens,
                   COALESCE(SUM(reasoning_tokens),0) AS reasoning_tokens,
                   COALESCE(SUM(reasoning_cost),0) AS reasoning_cost,
                   COALESCE(SUM(total_cost),0) AS total_cost,
                   COALESCE(SUM(total_cost - web_search_cost),0) AS model_cost,
                   COALESCE(SUM(web_search_calls),0) AS web_search_calls,
                   COALESCE(SUM(web_search_cost),0) AS web_search_cost
            FROM token_usage
            WHERE created_at >= CURRENT_DATE - INTERVAL 29 DAY
            GROUP BY DATE_FORMAT(created_at,'%Y-%m-%d')
            ORDER BY day ASC
        """)
        daily = cur.fetchall()

        def normalize(row):
            if not row:
                return row
            out = {}
            for key, value in row.items():
                if key in {"total_cost", "model_cost", "web_search_cost", "reasoning_cost"}:
                    out[key] = float(value or 0)
                elif isinstance(value, (int, float)):
                    out[key] = int(value or 0)
                else:
                    out[key] = value
            return out

        users = [normalize(x) for x in users]
        models = [normalize(x) for x in models]
        daily = [normalize(x) for x in daily]
        overall = normalize(overall)
        month = normalize(month)

        return jsonify({
            "success": True,
            "summary": {
                "users": total_users,
                "conversations": total_conversations,
                "messages": total_messages,
                "total_tokens": int(overall.get("total_tokens") or 0),
                "input_tokens": int(overall.get("input_tokens") or 0),
                "output_tokens": int(overall.get("output_tokens") or 0),
                "reasoning_tokens": int(overall.get("reasoning_tokens") or 0),
                "reasoning_cost": float(overall.get("reasoning_cost") or 0),
                "api_requests": int(overall.get("api_requests") or 0),
                "total_cost": float(overall.get("total_cost") or 0),
                "model_cost": float(overall.get("model_cost") or 0),
                "web_search_cost": float(overall.get("web_search_cost") or 0),
                "web_search_calls": int(overall.get("web_search_calls") or 0),
                "month_tokens": int(month.get("total_tokens") or 0),
                "month_reasoning_tokens": int(month.get("reasoning_tokens") or 0),
                "month_reasoning_cost": float(month.get("reasoning_cost") or 0),
                "month_requests": int(month.get("api_requests") or 0),
                "month_cost": float(month.get("total_cost") or 0),
                "month_model_cost": float(month.get("model_cost") or 0),
                "month_web_search_cost": float(month.get("web_search_cost") or 0),
                "month_web_search_calls": int(month.get("web_search_calls") or 0),
            },
            "users": users,
            "models": models,
            "daily": daily,
            "web_search_calls": int(overall.get("web_search_calls") or 0),
            "web_search_cost": float(overall.get("web_search_cost") or 0),
            "model_cost": float(overall.get("model_cost") or 0),
            "total_cost": float(overall.get("total_cost") or 0),
            "web_search_price_per_run": WEB_SEARCH_PRICE_PER_RUN
        })
    except Exception:
        current_app.logger.exception("admin dashboard failed")
        return admin_error("讀取管理統計失敗，請稍後再試。", 500)
    finally:
        cur.close()
        conn.close()


@bp.route("/api/admin/traffic")
def admin_traffic():
    if not require_admin():
        return admin_error("沒有管理員權限。", 403)

    conn = get_conn()
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute("""
            SELECT COUNT(*) AS page_views,
                   COUNT(DISTINCT visitor_hash) AS unique_visitors,
                   COUNT(DISTINCT CASE WHEN created_at >= CURRENT_DATE THEN visitor_hash END) AS today_visitors
            FROM visitor_events
        """)
        summary = cur.fetchone() or {}

        cur.execute("""
            SELECT COUNT(*) AS page_views,
                   COUNT(DISTINCT visitor_hash) AS unique_visitors
            FROM visitor_events
            WHERE created_at >= CURRENT_DATE
        """)
        today = cur.fetchone() or {}

        cur.execute("""
            SELECT DATE_FORMAT(created_at,'%Y-%m-%d') AS day,
                   COUNT(*) AS page_views,
                   COUNT(DISTINCT visitor_hash) AS unique_visitors
            FROM visitor_events
            WHERE created_at >= CURRENT_DATE - INTERVAL 29 DAY
            GROUP BY DATE_FORMAT(created_at,'%Y-%m-%d')
            ORDER BY day ASC
        """)
        daily = cur.fetchall()

        cur.execute("""
            SELECT path, COUNT(*) AS views, COUNT(DISTINCT visitor_hash) AS visitors
            FROM visitor_events
            WHERE created_at >= CURRENT_DATE - INTERVAL 29 DAY
            GROUP BY path
            ORDER BY views DESC
            LIMIT 15
        """)
        pages = cur.fetchall()

        cur.execute("""
            SELECT device_type, COUNT(*) AS views, COUNT(DISTINCT visitor_hash) AS visitors
            FROM visitor_events
            WHERE created_at >= CURRENT_DATE - INTERVAL 29 DAY
            GROUP BY device_type
            ORDER BY views DESC
        """)
        devices = cur.fetchall()

        cur.execute("""
            SELECT browser, COUNT(*) AS views, COUNT(DISTINCT visitor_hash) AS visitors
            FROM visitor_events
            WHERE created_at >= CURRENT_DATE - INTERVAL 29 DAY
            GROUP BY browser
            ORDER BY views DESC
        """)
        browsers = cur.fetchall()

        cur.execute("""
            SELECT os, COUNT(*) AS views, COUNT(DISTINCT visitor_hash) AS visitors
            FROM visitor_events
            WHERE created_at >= CURRENT_DATE - INTERVAL 29 DAY
            GROUP BY os
            ORDER BY views DESC
        """)
        systems = cur.fetchall()

        cur.execute("""
            SELECT
                CASE WHEN referrer IS NULL OR referrer='' THEN '直接訪問 / 無來源' ELSE referrer END AS source,
                COUNT(*) AS views,
                COUNT(DISTINCT visitor_hash) AS visitors
            FROM visitor_events
            WHERE created_at >= CURRENT_DATE - INTERVAL 29 DAY
            GROUP BY source
            ORDER BY views DESC
            LIMIT 15
        """)
        sources = cur.fetchall()

        cur.execute("""
            SELECT id, path, referrer, device_type, browser, os, created_at
            FROM visitor_events
            ORDER BY id DESC
            LIMIT 50
        """)
        recent = cur.fetchall()

        def norm(row):
            if not row:
                return row
            out = {}
            for k, v in row.items():
                if k in {"views", "visitors", "page_views", "unique_visitors", "today_visitors"}:
                    out[k] = int(v or 0)
                else:
                    out[k] = v
            return out

        return jsonify({
            "success": True,
            "summary": norm(summary),
            "today": norm(today),
            "daily": [norm(x) for x in daily],
            "pages": [norm(x) for x in pages],
            "devices": [norm(x) for x in devices],
            "browsers": [norm(x) for x in browsers],
            "systems": [norm(x) for x in systems],
            "sources": [norm(x) for x in sources],
            "recent": [norm(x) for x in recent]
        })
    except Exception:
        current_app.logger.exception("admin traffic failed")
        return admin_error("讀取訪客流量失敗，請稍後再試。", 500)
    finally:
        cur.close()
        conn.close()


@bp.route("/api/admin/users", methods=["POST"])
def admin_create_user():
    if not require_admin():
        return admin_error("沒有管理員權限。", 403)

    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        return admin_error("請求格式不正確。", 400)
    password = data.get("password")
    username, validation_error = validate_credentials(data.get("username"), password)
    display_name = str(data.get("display_name") or username).strip()[:255]
    email, email_error = validate_email(data.get("email"))

    if validation_error:
        return admin_error(validation_error, 400)
    if email_error:
        return admin_error(email_error, 400)

    conn = get_conn()
    cur = conn.cursor()
    try:
        cur.execute("""
            INSERT INTO users
            (username,password_hash,email,display_name,auth_provider)
            VALUES (%s,%s,%s,%s,%s)
        """, (username, hash_password(password), email,
              display_name or username, "local"))
        user_id = cur.lastrowid
        conn.commit()
        return jsonify({"success": True, "id": user_id})
    except mysql.connector.IntegrityError:
        conn.rollback()
        return admin_error("帳號已存在，請換一個帳號。", 400)
    except Exception:
        conn.rollback()
        current_app.logger.exception("admin create user failed")
        return admin_error("新增使用者失敗，請稍後再試。", 500)
    finally:
        cur.close()
        conn.close()


@bp.route("/api/admin/users/<int:user_id>", methods=["PATCH"])
def admin_update_user(user_id):
    if not require_admin():
        return admin_error("沒有管理員權限。", 403)

    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        return admin_error("請求格式不正確。", 400)
    username = normalize_username(data.get("username"))
    password = data.get("password")
    display_name = str(data.get("display_name") or "").strip()[:255]
    email, email_error = validate_email(data.get("email"))

    if username:
        username, username_error = validate_username(username)
        if username_error:
            return admin_error(username_error, 400)
    if password is not None and password != "":
        password_error = validate_password(password, username)
        if password_error:
            return admin_error(password_error, 400)
    if email_error:
        return admin_error(email_error, 400)

    conn = get_conn()
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute("SELECT id,username FROM users WHERE id=%s", (user_id,))
        if not cur.fetchone():
            return admin_error("找不到該使用者。", 404)

        updates, values = [], []
        if username:
            updates.append("username=%s")
            values.append(username)
        if password is not None and password != "":
            updates.append("password_hash=%s")
            values.append(hash_password(password))
        if display_name:
            updates.append("display_name=%s")
            values.append(display_name)
        if "email" in data:
            updates.append("email=%s")
            values.append(email)

        if not updates:
            return admin_error("沒有提供任何要修改的欄位。", 400)

        values.append(user_id)
        cur.execute(f"UPDATE users SET {', '.join(updates)} WHERE id=%s", tuple(values))
        conn.commit()
        return jsonify({"success": True})
    except mysql.connector.IntegrityError:
        conn.rollback()
        return admin_error("帳號已被其他使用者使用。", 400)
    except Exception:
        conn.rollback()
        current_app.logger.exception("admin update user failed")
        return admin_error("修改使用者失敗，請稍後再試。", 500)
    finally:
        cur.close()
        conn.close()


@bp.route("/api/admin/users/<int:user_id>", methods=["DELETE"])
def admin_delete_user(user_id):
    if not require_admin():
        return admin_error("沒有管理員權限。", 403)

    conn = get_conn()
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute("SELECT id,username FROM users WHERE id=%s", (user_id,))
        user = cur.fetchone()
        if not user:
            return admin_error("找不到該使用者。", 404)

        # 先清除該使用者擁有之共享對話資料，再移除他在其他共享對話的成員資格。
        cur.execute("""
            DELETE member
            FROM conversation_collaborators member
            INNER JOIN conversations c ON c.id=member.conversation_id
            WHERE c.user_id=%s
        """, (user_id,))
        cur.execute("""
            DELETE cs
            FROM conversation_shares cs
            INNER JOIN conversations c ON c.id=cs.conversation_id
            WHERE c.user_id=%s
        """, (user_id,))
        cur.execute("DELETE FROM conversation_collaborators WHERE user_id=%s", (user_id,))
        cur.execute("""
            DELETE message
            FROM chat message
            INNER JOIN conversations c ON c.id=message.conversation_id
            WHERE c.user_id=%s
        """, (user_id,))
        cur.execute("DELETE FROM chat WHERE user_id=%s", (user_id,))
        cur.execute("DELETE FROM conversations WHERE user_id=%s", (user_id,))
        cur.execute("DELETE FROM token_usage WHERE user_id=%s", (user_id,))
        # 全域聊天使用 ON DELETE CASCADE，但明確刪除可避免舊資料表尚未套上 FK 時殘留。
        cur.execute("DELETE FROM global_chat_messages WHERE user_id=%s", (user_id,))
        cur.execute("DELETE FROM users WHERE id=%s", (user_id,))
        conn.commit()
        return jsonify({"success": True})
    except Exception:
        conn.rollback()
        current_app.logger.exception("admin delete user failed")
        return admin_error("刪除使用者失敗，請稍後再試。", 500)
    finally:
        cur.close()
        conn.close()
