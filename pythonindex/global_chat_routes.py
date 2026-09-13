"""全域聊天室 API。"""

from flask import Blueprint, jsonify, request, session

from config import GLOBAL_CHAT_HISTORY_LIMIT, GLOBAL_CHAT_MAX_LENGTH
from database import get_conn, require_login


bp = Blueprint("global_chat", __name__)

@bp.route("/api/global_chat/messages", methods=["GET"])
def global_chat_messages():
    user_id = require_login()

    if user_id is None:
        return jsonify({
            "success": False,
            "error": "請先登入！"
        }), 401

    try:
        after_id = max(
            0,
            int(request.args.get("after_id", 0))
        )
    except (TypeError, ValueError):
        after_id = 0

    try:
        limit = max(
            1,
            min(
                int(
                    request.args.get(
                        "limit",
                        GLOBAL_CHAT_HISTORY_LIMIT
                    )
                ),
                100
            )
        )
    except (TypeError, ValueError):
        limit = GLOBAL_CHAT_HISTORY_LIMIT

    conn = None
    cur = None

    try:
        conn = get_conn()
        cur = conn.cursor(dictionary=True)

        if after_id > 0:
            cur.execute(
                """
                SELECT
                    g.id,
                    g.user_id,
                    g.message,
                    g.created_at,
                    COALESCE(
                        u.display_name,
                        u.username
                    ) AS display_name,
                    u.username,
                    u.email,
                    u.avatar_url
                FROM global_chat_messages g
                INNER JOIN users u
                    ON u.id = g.user_id
                WHERE g.id > %s
                ORDER BY g.id ASC
                LIMIT %s
                """,
                (after_id, limit)
            )
            rows = cur.fetchall()

        else:
            cur.execute(
                """
                SELECT
                    g.id,
                    g.user_id,
                    g.message,
                    g.created_at,
                    COALESCE(
                        u.display_name,
                        u.username
                    ) AS display_name,
                    u.username,
                    u.email,
                    u.avatar_url
                FROM global_chat_messages g
                INNER JOIN users u
                    ON u.id = g.user_id
                ORDER BY g.id DESC
                LIMIT %s
                """,
                (limit,)
            )

            rows = cur.fetchall()
            rows.reverse()

        messages = []

        for row in rows:
            created_at = row.get("created_at")

            messages.append({
                "id": int(row["id"]),
                "user_id": int(row["user_id"]),
                "username": row.get("username"),
                "display_name":
                    row.get("display_name")
                    or row.get("username"),
                "email": row.get("email"),
                "avatar_url": row.get("avatar_url"),
                "message": row["message"],
                "created_at":
                    created_at.isoformat()
                    if created_at
                    else None,
                "is_me":
                    int(row["user_id"])
                    == int(user_id)
            })

        return jsonify({
            "success": True,
            "messages": messages
        })

    except Exception as e:
        return jsonify({
            "success": False,
            "error": f"讀取全域聊天室失敗：{e}"
        }), 500

    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()


@bp.route("/api/global_chat/messages", methods=["POST"])
def global_chat_send():
    user_id = require_login()

    if user_id is None:
        return jsonify({
            "success": False,
            "error": "請先登入！"
        }), 401

    data = request.get_json(silent=True) or {}
    message = str(
        data.get("message") or ""
    ).strip()

    if not message:
        return jsonify({
            "success": False,
            "error": "訊息不能為空。"
        }), 400

    if len(message) > GLOBAL_CHAT_MAX_LENGTH:
        return jsonify({
            "success": False,
            "error":
                f"訊息最多 "
                f"{GLOBAL_CHAT_MAX_LENGTH} 個字元。"
        }), 400

    conn = None
    cur = None

    try:
        conn = get_conn()
        cur = conn.cursor(dictionary=True)

        cur.execute(
            """
            SELECT
                id,
                username,
                COALESCE(
                    display_name,
                    username
                ) AS display_name,
                email,
                avatar_url
            FROM users
            WHERE id = %s
            LIMIT 1
            """,
            (user_id,)
        )

        user = cur.fetchone()

        if not user:
            session.clear()

            return jsonify({
                "success": False,
                "error":
                    "使用者不存在，請重新登入。"
            }), 401

        cur.execute(
            """
            INSERT INTO global_chat_messages
                (user_id, message)
            VALUES
                (%s, %s)
            """,
            (user_id, message)
        )

        message_id = cur.lastrowid

        conn.commit()

        cur.execute(
            """
            SELECT
                g.id,
                g.user_id,
                g.message,
                g.created_at,
                COALESCE(
                    u.display_name,
                    u.username
                ) AS display_name,
                u.username,
                u.email,
                u.avatar_url
            FROM global_chat_messages g
            INNER JOIN users u
                ON u.id = g.user_id
            WHERE g.id = %s
            LIMIT 1
            """,
            (message_id,)
        )

        row = cur.fetchone()

        if not row:
            return jsonify({
                "success": False,
                "error":
                    "訊息已寫入，但無法讀取剛建立的訊息。"
            }), 500

        created_at = row.get("created_at")

        return jsonify({
            "success": True,
            "message": {
                "id": int(row["id"]),
                "user_id": int(row["user_id"]),
                "username": row.get("username"),
                "display_name":
                    row.get("display_name")
                    or row.get("username"),
                "email": row.get("email"),
                "avatar_url": row.get("avatar_url"),
                "message": row["message"],
                "created_at":
                    created_at.isoformat()
                    if created_at
                    else None,
                "is_me": True
            }
        })

    except Exception as e:
        if conn:
            conn.rollback()

        return jsonify({
            "success": False,
            "error":
                f"發送全域訊息失敗：{e}"
        }), 500

    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()


# ---------- Chat ----------
