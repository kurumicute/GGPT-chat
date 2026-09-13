"""對話清單、分享、協作者與訊息讀取 API。"""

import json
import uuid

from flask import Blueprint, jsonify, request

from database import (
    accessible_conversation,
    ensure_user_has_conversation,
    get_conn,
    owned_conversation,
    require_login,
)


bp = Blueprint("conversations", __name__)

@bp.route("/conversations", methods=["GET"])
def list_conversations():
    user_id = require_login()
    if user_id is None:
        return jsonify({"error": "請先登入！"}), 401

    ensure_user_has_conversation(user_id)

    conn = get_conn()
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute("""
            SELECT
                c.id,
                c.user_id,
                c.title,
                c.created_at,
                c.updated_at,
                CASE WHEN c.user_id=%s THEN 1 ELSE 0 END AS is_owner,
                CASE WHEN c.user_id<>%s THEN 1 ELSE 0 END AS is_shared,
                EXISTS(
                    SELECT 1
                    FROM conversation_shares cs
                    WHERE cs.conversation_id=c.id AND cs.is_active=1
                ) AS share_enabled
            FROM conversations c
            LEFT JOIN conversation_collaborators member
                ON member.conversation_id=c.id AND member.user_id=%s
            LEFT JOIN conversation_shares active_share
                ON active_share.conversation_id=c.id AND active_share.is_active=1
            WHERE c.user_id=%s
               OR (member.user_id IS NOT NULL AND active_share.conversation_id IS NOT NULL)
            ORDER BY c.updated_at DESC, c.id DESC
        """, (user_id, user_id, user_id, user_id))
        return jsonify(cur.fetchall())
    finally:
        cur.close()
        conn.close()


@bp.route("/conversations", methods=["POST"])
def create_conversation():
    user_id = require_login()
    if user_id is None:
        return jsonify({"error": "請先登入！"}), 401

    data = request.get_json(silent=True) or {}
    title = (data.get("title") or "新對話").strip()[:255] or "新對話"

    conn = get_conn()
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute("INSERT INTO conversations (user_id,title) VALUES (%s,%s)", (user_id, title))
        conv_id = cur.lastrowid
        conn.commit()
        return jsonify({
            "id": conv_id,
            "user_id": user_id,
            "title": title,
            "is_owner": 1,
            "is_shared": 0,
            "share_enabled": 0,
        })
    finally:
        cur.close()
        conn.close()


@bp.route("/conversations/<int:conversation_id>/share", methods=["POST"])
def share_conversation(conversation_id):
    user_id = require_login()
    if user_id is None:
        return jsonify({"error": "請先登入！"}), 401

    conn = get_conn()
    cur = conn.cursor(dictionary=True)
    try:
        if not owned_conversation(cur, user_id, conversation_id):
            return jsonify({"error": "只有對話擁有者可以建立分享連結。"}), 403

        cur.execute(
            """
            SELECT share_token
            FROM conversation_shares
            WHERE conversation_id=%s AND is_active=1
            LIMIT 1
            """,
            (conversation_id,),
        )
        existing = cur.fetchone()
        share_token = existing["share_token"] if existing else str(uuid.uuid4())

        if not existing:
            cur.execute(
                """
                INSERT INTO conversation_shares
                    (conversation_id, share_token, created_by, is_active)
                VALUES (%s, %s, %s, 1)
                ON DUPLICATE KEY UPDATE
                    share_token=VALUES(share_token),
                    created_by=VALUES(created_by),
                    is_active=1
                """,
                (conversation_id, share_token, user_id),
            )
            conn.commit()

        return jsonify({
            "success": True,
            "share_token": share_token,
            "share_path": f"/c/{share_token}",
        })
    except Exception as e:
        conn.rollback()
        return jsonify({"error": f"建立分享連結失敗：{e}"}), 500
    finally:
        cur.close()
        conn.close()


@bp.route("/conversations/<int:conversation_id>/share", methods=["DELETE"])
def revoke_conversation_share(conversation_id):
    user_id = require_login()
    if user_id is None:
        return jsonify({"error": "請先登入！"}), 401

    conn = get_conn()
    cur = conn.cursor(dictionary=True)
    try:
        if not owned_conversation(cur, user_id, conversation_id):
            return jsonify({"error": "只有對話擁有者可以停止分享。"}), 403

        cur.execute(
            "UPDATE conversation_shares SET is_active=0 WHERE conversation_id=%s",
            (conversation_id,),
        )
        # 停止分享會同步移除所有協作者，舊連結與既有加入者都不能再存取。
        cur.execute(
            "DELETE FROM conversation_collaborators WHERE conversation_id=%s",
            (conversation_id,),
        )
        conn.commit()
        return jsonify({"success": True})
    except Exception as e:
        conn.rollback()
        return jsonify({"error": f"停止分享失敗：{e}"}), 500
    finally:
        cur.close()
        conn.close()


@bp.route("/shared-conversations/<share_token>/join", methods=["POST"])
def join_shared_conversation(share_token):
    user_id = require_login()
    if user_id is None:
        return jsonify({"error": "請先登入後再加入共享對話。"}), 401

    try:
        normalized_token = str(uuid.UUID(str(share_token)))
    except (ValueError, AttributeError, TypeError):
        return jsonify({"error": "分享連結格式不正確。"}), 404

    conn = get_conn()
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute(
            """
            SELECT c.id, c.user_id, c.title, c.created_at, c.updated_at
            FROM conversation_shares cs
            INNER JOIN conversations c ON c.id=cs.conversation_id
            WHERE cs.share_token=%s AND cs.is_active=1
            LIMIT 1
            """,
            (normalized_token,),
        )
        conversation = cur.fetchone()
        if not conversation:
            return jsonify({"error": "分享連結不存在或已停止分享。"}), 404

        is_owner = int(conversation["user_id"]) == int(user_id)
        if not is_owner:
            cur.execute(
                """
                INSERT IGNORE INTO conversation_collaborators
                    (conversation_id, user_id)
                VALUES (%s, %s)
                """,
                (conversation["id"], user_id),
            )
            conn.commit()

        conversation["is_owner"] = 1 if is_owner else 0
        conversation["is_shared"] = 0 if is_owner else 1
        conversation["share_enabled"] = 1
        return jsonify({"success": True, "conversation": conversation})
    except Exception as e:
        conn.rollback()
        return jsonify({"error": f"加入共享對話失敗：{e}"}), 500
    finally:
        cur.close()
        conn.close()


@bp.route("/conversations/<int:conversation_id>", methods=["PATCH"])
def rename_conversation(conversation_id):
    user_id = require_login()
    if user_id is None:
        return jsonify({"error": "請先登入！"}), 401

    title = ((request.get_json(silent=True) or {}).get("title") or "").strip()[:255]
    if not title:
        return jsonify({"error": "對話名稱不能為空。"}), 400

    conn = get_conn()
    cur = conn.cursor(dictionary=True)
    try:
        if not owned_conversation(cur, user_id, conversation_id):
            return jsonify({"error": "找不到這個對話。"}), 404
        cur.execute("UPDATE conversations SET title=%s WHERE id=%s AND user_id=%s", (title, conversation_id, user_id))
        conn.commit()
        return jsonify({"id": conversation_id, "user_id": user_id, "title": title})
    finally:
        cur.close()
        conn.close()


@bp.route("/conversations/<int:conversation_id>", methods=["DELETE"])
def delete_conversation(conversation_id):
    user_id = require_login()
    if user_id is None:
        return jsonify({"error": "請先登入！"}), 401

    conn = get_conn()
    cur = conn.cursor(dictionary=True)
    try:
        conversation = accessible_conversation(cur, user_id, conversation_id)
        if not conversation:
            return jsonify({"error": "找不到這個對話。"}), 404

        if not conversation.get("is_owner"):
            cur.execute(
                "DELETE FROM conversation_collaborators WHERE conversation_id=%s AND user_id=%s",
                (conversation_id, user_id),
            )
            conn.commit()
            return jsonify({"success": True, "left": True})

        cur.execute("DELETE FROM conversation_collaborators WHERE conversation_id=%s", (conversation_id,))
        cur.execute("DELETE FROM conversation_shares WHERE conversation_id=%s", (conversation_id,))
        cur.execute("DELETE FROM chat WHERE conversation_id=%s", (conversation_id,))
        cur.execute("DELETE FROM conversations WHERE id=%s AND user_id=%s", (conversation_id, user_id))
        conn.commit()
        ensure_user_has_conversation(user_id)
        return jsonify({"success": True, "left": False})
    finally:
        cur.close()
        conn.close()


@bp.route("/conversations/<int:conversation_id>/messages", methods=["GET"])
def conversation_messages(conversation_id):
    user_id = require_login()
    if user_id is None:
        return jsonify({"error": "請先登入！"}), 401

    conn = get_conn()
    cur = conn.cursor(dictionary=True)
    try:
        if not accessible_conversation(cur, user_id, conversation_id):
            return jsonify({"error": "找不到這個對話。"}), 404
        cur.execute("""
            SELECT
                chat.id,
                chat.user_id,
                chat.user_message,
                chat.image_url,
                chat.attachment_json,
                chat.reasoning_summary,
                chat.bot_reply,
                chat.created_at,
                COALESCE(users.display_name, users.username, '使用者') AS author_name,
                CASE WHEN chat.user_id=%s THEN 1 ELSE 0 END AS is_mine
            FROM chat
            LEFT JOIN users ON users.id=chat.user_id
            WHERE chat.conversation_id=%s
            ORDER BY chat.id ASC
        """, (user_id, conversation_id))
        rows = cur.fetchall()

        for row in rows:
            raw = row.get("attachment_json")
            if raw:
                try:
                    row["attachments"] = json.loads(raw)
                except Exception:
                    row["attachments"] = []
            else:
                row["attachments"] = []
            row.pop("attachment_json", None)

        return jsonify(rows)
    finally:
        cur.close()
        conn.close()


# ---------- Global Chat ----------
