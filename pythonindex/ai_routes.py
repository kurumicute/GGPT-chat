"""AI 回覆、Token 使用量與語音朗讀 API。"""

import io
import json
import os
from urllib.parse import urlsplit

import requests
from flask import Blueprint, abort, current_app, jsonify, request, send_file

from config import (
    CHAT_MAX_ATTACHMENTS,
    CHAT_MESSAGE_MAX_LENGTH,
    MODEL_PRICING,
    OPENAI_API_KEY,
    OPENAI_MAX_OUTPUT_TOKENS,
    TOKEN_QUOTA,
    WEB_SEARCH_PRICE_PER_1K,
    WEB_SEARCH_PRICE_PER_RUN,
)
from database import accessible_conversation, ensure_user_has_conversation, get_conn, require_login
from extensions import limiter, openai_client as client, traditional_chinese as cc
from pricing import calculate_model_cost, calculate_web_search_cost, get_model_pricing


bp = Blueprint("ai", __name__)


def normalize_local_upload_url(value):
    """附件只能引用本站 /uploads/ 下的檔案，避免由前端任意指定外部資源。"""
    value = str(value or "").strip()
    if not value:
        return ""
    parsed = urlsplit(value)
    if parsed.scheme or parsed.netloc or parsed.query or parsed.fragment:
        return ""
    if not parsed.path.startswith("/uploads/") or ".." in parsed.path:
        return ""
    return parsed.path[:1024]

def normalize_attachment(attachment):
    if not isinstance(attachment, dict):
        return None
    name = str(attachment.get("name") or "附件")[:255]
    mime_type = str(attachment.get("mime_type") or "application/octet-stream")[:255]
    url = normalize_local_upload_url(
        attachment.get("url") or attachment.get("file_url") or ""
    )
    content = attachment.get("content")
    if content is not None:
        content = str(content)[:200000]
    size = attachment.get("size")
    try:
        size = int(size) if size is not None else None
    except (ValueError, TypeError):
        size = None
    return {
        "name": name,
        "mime_type": mime_type,
        "url": url,
        "content": content,
        "size": size,
    }


def build_attachment_input(attachment):
    """轉成 Responses API 可接受的輸入內容。文字檔內容以 input_text；圖片以 input_image。"""
    url = attachment.get("url")
    mime_type = (attachment.get("mime_type") or "").lower()
    content = attachment.get("content")
    name = attachment.get("name") or "附件"

    is_image = mime_type.startswith("image/")

    if is_image and url:
        public_url = request.host_url.rstrip("/") + url if url.startswith("/") else url
        return {
            "type": "input_image",
            "image_url": public_url
        }

    if content is not None:
        return {
            "type": "input_text",
            "text": f"以下是附件「{name}」的內容：\n\n{content}"
        }

    if url:
        return {
            "type": "input_text",
            "text": f"使用者附加了檔案「{name}」，檔案位置：{url}。後端目前沒有可直接解析此格式的內容。"
        }

    return {
        "type": "input_text",
        "text": f"使用者附加了一個檔案「{name}」，但未提供可讀取內容。"
    }


def extract_reasoning_summary(response):
    """從 Responses API 的 reasoning output 擷取可公開顯示的摘要文字。

    這裡只取 API 提供的 summary，不讀取或拼接原始 reasoning / chain-of-thought。
    """
    parts = []
    for item in getattr(response, "output", []) or []:
        if getattr(item, "type", None) != "reasoning":
            continue
        summary = getattr(item, "summary", None)
        for part in summary or []:
            text = getattr(part, "text", None)
            if text is None and isinstance(part, dict):
                text = part.get("text")
            if text:
                parts.append(str(text).strip())
    # 保留順序、移除相鄰重複片段
    cleaned = []
    seen = set()
    for part in parts:
        if part and part not in seen:
            cleaned.append(part)
            seen.add(part)
    return "\n\n".join(cleaned).strip()


@bp.route("/chat", methods=["POST"])
@limiter.limit("20 per minute")
def chat():
    user_id = require_login()
    if user_id is None:
        return jsonify({"error": "請先登入！"}), 401

    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        return jsonify({"error": "請求格式不正確。"}), 400
    conversation_id = data.get("conversation_id")
    user_message = str(data.get("message") or "").strip()
    image_url = normalize_local_upload_url(data.get("image_url"))

    if len(user_message) > CHAT_MESSAGE_MAX_LENGTH:
        return jsonify({"error": f"單次訊息最多 {CHAT_MESSAGE_MAX_LENGTH} 個字元。"}), 400

    requested_model = (data.get("model") or os.getenv("OPENAI_MODEL") or "gpt-5.6-luna").strip()
    if requested_model not in MODEL_PRICING:
        return jsonify({"error": f"不支援的模型：{requested_model}"}), 400

    requested_reasoning_enabled = bool(data.get("reasoning_enabled", True))
    requested_reasoning_effort = str(
        data.get("reasoning_effort") or "medium"
    ).strip().lower()

    reasoning_capabilities = {
        "gpt-6-astra": {"low", "medium", "high", "xhigh", "max"},
        "gpt-5.6-sol": {"none", "low", "medium", "high", "xhigh", "max"},
        "gpt-5.6-terra": {"none", "low", "medium", "high", "xhigh", "max"},
        "gpt-5.6-luna": {"none", "low", "medium", "high", "xhigh", "max"},
    }

    if requested_model == "gpt-5-nano":
        # 依 UI 設計：GPT-5 nano 固定不使用思考模式。
        reasoning_enabled = False
        reasoning_effort = None
    else:
        allowed_efforts = reasoning_capabilities.get(requested_model)
        reasoning_enabled = bool(
            requested_reasoning_enabled and allowed_efforts
        )
        reasoning_effort = (
            requested_reasoning_effort
            if reasoning_enabled and requested_reasoning_effort in allowed_efforts
            else ("medium" if reasoning_enabled and "medium" in allowed_efforts else None)
        )

    web_search_enabled = bool(data.get("web_search", False))

    raw_attachments = data.get("attachments") or []
    if not isinstance(raw_attachments, list):
        return jsonify({"error": "attachments 必須是陣列。"}), 400
    if len(raw_attachments) > CHAT_MAX_ATTACHMENTS:
        return jsonify({"error": f"單次最多附加 {CHAT_MAX_ATTACHMENTS} 個檔案。"}), 400

    attachments = [x for x in (normalize_attachment(a) for a in raw_attachments) if x]

    if not conversation_id:
        conversation_id = ensure_user_has_conversation(user_id)
    try:
        conversation_id = int(conversation_id)
    except (TypeError, ValueError):
        return jsonify({"error": "對話編號格式不正確。"}), 400

    conn = get_conn()
    cur = conn.cursor(dictionary=True)
    try:
        conv = accessible_conversation(cur, user_id, conversation_id)
        if not conv:
            return jsonify({"error": "對話不存在，或你沒有這個共享對話的權限。"}), 404

        if not user_message and not image_url and not attachments:
            return jsonify({"error": "訊息、圖片或附件不能全部為空！"}), 400

        # 共享對話的模型上下文包含所有協作者最近的訊息。
        cur.execute("""
            SELECT
                message.user_message,
                message.bot_reply,
                message.attachment_json,
                COALESCE(users.display_name, users.username, '使用者') AS author_name
            FROM chat message
            LEFT JOIN users ON users.id=message.user_id
            WHERE message.conversation_id=%s
            ORDER BY message.id DESC
            LIMIT 12
        """, (conversation_id,))
        history = cur.fetchall()

        input_messages = []
        for row in reversed(history):
            if row["user_message"]:
                try:
                    previous_attachments = json.loads(row.get("attachment_json") or "[]")
                except Exception:
                    previous_attachments = []
                content = [{
                    "type": "input_text",
                    "text": f"{row.get('author_name') or '使用者'}：{row['user_message']}"
                }]
                for att in previous_attachments:
                    if isinstance(att, dict) and att.get("content"):
                        content.append({
                            "type": "input_text",
                            "text": f"先前附件「{att.get('name','附件')}」：\n{str(att.get('content'))[:100000]}"
                        })
                input_messages.append({"role": "user", "content": content})
            if row["bot_reply"]:
                input_messages.append({
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": row["bot_reply"]}]
                })

        user_content = []
        user_content.append({
            "type": "input_text",
            "text": user_message or "請分析我附加的檔案。"
        })

        if image_url:
            public_url = request.host_url.rstrip("/") + image_url
            user_content.append({"type": "input_image", "image_url": public_url})

        for attachment in attachments:
            user_content.append(build_attachment_input(attachment))

        input_messages.append({"role": "user", "content": user_content})

        response_kwargs = {
            "model": requested_model,
            "instructions": (
                "你是一個中文 AI 助手。請優先使用繁體中文。"
                "若需要程式碼，請使用 Markdown fenced code block 並標示語言。"
                "數學公式可使用 LaTeX，例如 $x^2$ 或 $$\\frac{a}{b}$$。"
                "如果使用者提供附件內容，請直接根據附件內容回答，不要假裝看過不存在的內容。"
            ),
            "input": input_messages,
            "max_output_tokens": OPENAI_MAX_OUTPUT_TOKENS,
        }

        if reasoning_enabled and reasoning_effort:
            response_kwargs["reasoning"] = {
                "effort": reasoning_effort,
                # OpenAI Responses API 現行欄位：
                # generate_summary 已棄用，改用 summary。
                "summary": "auto"
            }

        if web_search_enabled:
            response_kwargs["tools"] = [{"type": "web_search"}]

        response = client.responses.create(**response_kwargs)

        reply = getattr(response, "output_text", "") or ""
        if not reply:
            chunks = []
            for item in getattr(response, "output", []) or []:
                if getattr(item, "type", None) == "message":
                    for content in getattr(item, "content", []) or []:
                        text = getattr(content, "text", None)
                        if text:
                            chunks.append(text)
            reply = "\n".join(chunks)
        reply = cc.convert(reply)
        reasoning_summary = cc.convert(extract_reasoning_summary(response)) if reasoning_enabled else ""

        usage = getattr(response, "usage", None)
        input_tokens = int(getattr(usage, "input_tokens", 0) or 0) if usage else 0
        output_tokens = int(getattr(usage, "output_tokens", 0) or 0) if usage else 0
        total_tokens = int(getattr(usage, "total_tokens", 0) or (input_tokens + output_tokens)) if usage else (input_tokens + output_tokens)

        cached_input_tokens = 0
        if usage:
            details = getattr(usage, "input_tokens_details", None)
            cached_input_tokens = int(getattr(details, "cached_tokens", 0) or 0) if details else 0

        # Responses API 將 reasoning token 計入 output_tokens；這裡額外拆出來顯示，
        # 但不能再把 reasoning_cost 加到 total_cost，否則會重複計費。
        reasoning_tokens = 0
        reasoning_details = getattr(usage, "output_tokens_details", None) if usage else None
        if reasoning_details:
            reasoning_tokens = int(getattr(reasoning_details, "reasoning_tokens", 0) or 0)

        model_used = requested_model

        # 每一個聊天請求最多計 1 次 Web Search；只在 Responses output 真正出現 web_search_call 時才收費。
        has_web_search_call = any(
            getattr(item, "type", None) == "web_search_call"
            for item in (getattr(response, "output", []) or [])
        )
        web_search_calls = 1 if web_search_enabled and has_web_search_call else 0

        web_search_cost = calculate_web_search_cost(web_search_calls)
        model_cost = calculate_model_cost(
            model_used,
            input_tokens,
            output_tokens,
            cached_input_tokens,
        )

        # reasoning token 已包含在 output_tokens 的 output pricing 中。
        output_price = get_model_pricing(model_used)["output"]
        reasoning_cost = (reasoning_tokens * output_price) / 1_000_000.0

        # 總金額維持只算一次：模型 output 成本 + Web Search。
        token_cost = model_cost + web_search_cost

        # 擷取 Web Search 來源網址。
        web_sources = []
        if web_search_enabled:
            seen_urls = set()
            for item in getattr(response, "output", []) or []:
                for content in getattr(item, "content", []) or []:
                    for ann in getattr(content, "annotations", []) or []:
                        url = getattr(ann, "url", None)
                        title = getattr(ann, "title", None) or url
                        if url and url not in seen_urls:
                            seen_urls.add(url)
                            web_sources.append({"title": title, "url": url})
                action = getattr(item, "action", None)
                for source in getattr(action, "sources", []) or []:
                    url = getattr(source, "url", None)
                    if url and url not in seen_urls:
                        seen_urls.add(url)
                        web_sources.append({"title": url, "url": url})

        new_title = conv["title"]
        cur.execute(
            "SELECT COUNT(*) AS c FROM chat WHERE conversation_id=%s",
            (conversation_id,)
        )
        count = cur.fetchone()["c"]
        if count == 0 and new_title == "新對話":
            source = user_message or (attachments[0]["name"] if attachments else "圖片對話")
            new_title = source.replace("\n", " ").strip()[:36] or "新對話"
            cur.execute(
                "UPDATE conversations SET title=%s WHERE id=%s",
                (new_title, conversation_id)
            )

        cur.execute("""
            INSERT INTO token_usage
            (user_id,conversation_id,model,input_tokens,cached_input_tokens,output_tokens,reasoning_tokens,total_tokens,total_cost,reasoning_cost,web_search,web_search_calls,web_search_cost)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        """, (
            user_id, conversation_id, model_used, input_tokens, cached_input_tokens,
            output_tokens, reasoning_tokens, total_tokens, token_cost, reasoning_cost,
            int(web_search_calls > 0), web_search_calls, web_search_cost
        ))

        attachment_json = json.dumps(attachments, ensure_ascii=False)
        cur.execute("""
            INSERT INTO chat
            (user_id,conversation_id,user_message,image_url,attachment_json,reasoning_summary,bot_reply)
            VALUES (%s,%s,%s,%s,%s,%s,%s)
        """, (
            user_id,
            conversation_id,
            user_message or (attachments[0]["name"] if attachments else "圖片上傳"),
            image_url,
            attachment_json if attachments else None,
            reasoning_summary or None,
            reply
        ))

        cur.execute(
            "UPDATE conversations SET updated_at=CURRENT_TIMESTAMP WHERE id=%s",
            (conversation_id,)
        )
        conn.commit()

        return jsonify({
            "reply": reply,
            "usage": {
                "model": model_used,
                "input_tokens": input_tokens,
                "cached_input_tokens": cached_input_tokens,
                "output_tokens": output_tokens,
                "reasoning_tokens": reasoning_tokens,
                "total_tokens": total_tokens,
                "model_cost": round(model_cost, 8),
                "reasoning_cost": round(reasoning_cost, 8),
                "web_search_calls": web_search_calls,
                "web_search_cost": round(web_search_cost, 8),
                "total_cost": round(token_cost, 8),
                "reasoning_enabled": reasoning_enabled,
                "reasoning_effort": reasoning_effort
            },
            "model": model_used,
            "reasoning_summary": reasoning_summary,
            "web_search": web_search_enabled,
            "web_sources": web_sources,
            "attachments": [
                {
                    "name": a.get("name"),
                    "mime_type": a.get("mime_type"),
                    "url": a.get("url"),
                    "size": a.get("size")
                }
                for a in attachments
            ],
            "conversation": {
                "id": conversation_id,
                "user_id": conv["user_id"],
                "title": new_title,
                "is_owner": int(conv.get("is_owner") or 0),
                "is_shared": 0 if conv.get("is_owner") else 1
            }
        })
    except Exception:
        conn.rollback()
        current_app.logger.exception("chat request failed")
        return jsonify({"error": "AI 回覆暫時失敗，請稍後再試。"}), 500
    finally:
        cur.close()
        conn.close()


# ---------- Token usage / cost ----------
@bp.route("/usage")
def usage():
    user_id = require_login()
    if user_id is None:
        return jsonify({"error": "請先登入！"}), 401

    conn = get_conn()
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute("""
            SELECT
                COUNT(*) AS requests,
                COALESCE(SUM(input_tokens),0) AS input_tokens,
                COALESCE(SUM(cached_input_tokens),0) AS cached_input_tokens,
                COALESCE(SUM(output_tokens),0) AS output_tokens,
                COALESCE(SUM(reasoning_tokens),0) AS reasoning_tokens,
                COALESCE(SUM(reasoning_cost),0) AS reasoning_cost,
                COALESCE(SUM(total_tokens),0) AS total_tokens,
                COALESCE(SUM(total_cost),0) AS total_cost,
                COALESCE(SUM(total_cost - web_search_cost),0) AS model_cost,
                COALESCE(SUM(web_search),0) AS web_search_requests,
                COALESCE(SUM(web_search_calls),0) AS web_search_calls,
                COALESCE(SUM(web_search_cost),0) AS web_search_cost
            FROM token_usage WHERE user_id=%s
        """, (user_id,))
        total = cur.fetchone() or {}

        cur.execute("""
            SELECT
                COUNT(*) AS requests,
                COALESCE(SUM(input_tokens),0) AS input_tokens,
                COALESCE(SUM(cached_input_tokens),0) AS cached_input_tokens,
                COALESCE(SUM(output_tokens),0) AS output_tokens,
                COALESCE(SUM(reasoning_tokens),0) AS reasoning_tokens,
                COALESCE(SUM(reasoning_cost),0) AS reasoning_cost,
                COALESCE(SUM(total_tokens),0) AS total_tokens,
                COALESCE(SUM(total_cost),0) AS total_cost,
                COALESCE(SUM(total_cost - web_search_cost),0) AS model_cost,
                COALESCE(SUM(web_search),0) AS web_search_requests,
                COALESCE(SUM(web_search_calls),0) AS web_search_calls,
                COALESCE(SUM(web_search_cost),0) AS web_search_cost
            FROM token_usage
            WHERE user_id=%s AND created_at >= DATE_FORMAT(CURRENT_DATE, '%Y-%m-01')
        """, (user_id,))
        month = cur.fetchone() or {}

        cur.execute("""
            SELECT model, COUNT(*) AS requests,
                   COALESCE(SUM(input_tokens),0) AS input_tokens,
                   COALESCE(SUM(output_tokens),0) AS output_tokens,
                   COALESCE(SUM(reasoning_tokens),0) AS reasoning_tokens,
                   COALESCE(SUM(reasoning_cost),0) AS reasoning_cost,
                   COALESCE(SUM(total_tokens),0) AS total_tokens,
                   COALESCE(SUM(total_cost),0) AS total_cost,
                   COALESCE(SUM(total_cost - web_search_cost),0) AS model_cost,
                   COALESCE(SUM(web_search_calls),0) AS web_search_calls
            FROM token_usage WHERE user_id=%s
            GROUP BY model ORDER BY total_cost DESC, total_tokens DESC
        """, (user_id,))
        by_model = cur.fetchall()

        cur.execute("""
            SELECT DATE_FORMAT(created_at, '%Y-%m-%d') AS day, COUNT(*) AS requests,
                   COALESCE(SUM(total_tokens),0) AS total_tokens,
                   COALESCE(SUM(total_cost),0) AS total_cost,
                   COALESCE(SUM(total_cost - web_search_cost),0) AS model_cost,
                   COALESCE(SUM(web_search_calls),0) AS web_search_calls
            FROM token_usage WHERE user_id=%s
            GROUP BY DATE_FORMAT(created_at, '%Y-%m-%d') ORDER BY day DESC LIMIT 90
        """, (user_id,))
        daily = cur.fetchall()

        cur.execute("""
            SELECT DATE_FORMAT(created_at, '%Y-%m') AS month, COUNT(*) AS requests,
                   COALESCE(SUM(total_tokens),0) AS total_tokens,
                   COALESCE(SUM(total_cost),0) AS total_cost,
                   COALESCE(SUM(total_cost - web_search_cost),0) AS model_cost,
                   COALESCE(SUM(web_search_calls),0) AS web_search_calls
            FROM token_usage WHERE user_id=%s
            GROUP BY DATE_FORMAT(created_at, '%Y-%m') ORDER BY month DESC LIMIT 24
        """, (user_id,))
        monthly = cur.fetchall()

        for item in (total, month, *by_model, *daily, *monthly):
            for key, value in list(item.items()):
                if hasattr(value, "item"):
                    item[key] = value.item()
                elif key in {"total_cost", "model_cost", "web_search_cost", "reasoning_cost"}:
                    item[key] = float(value or 0)
                elif key in {"requests", "input_tokens", "cached_input_tokens", "output_tokens", "reasoning_tokens", "total_tokens", "web_search_requests", "web_search_calls"}:
                    item[key] = int(value or 0)

        total_tokens = int(total.get("total_tokens") or 0)
        remaining = max(0, TOKEN_QUOTA - total_tokens) if TOKEN_QUOTA > 0 else None
        percent = min(100, total_tokens / TOKEN_QUOTA * 100) if TOKEN_QUOTA > 0 else None

        return jsonify({
            "total": total,
            "month": month,
            "by_model": by_model,
            "daily": daily,
            "monthly": monthly,
            "quota": TOKEN_QUOTA,
            "remaining": remaining,
            "percent": percent,
            "pricing": MODEL_PRICING,
            "web_search_price_per_1k": WEB_SEARCH_PRICE_PER_1K,
            "web_search_price_per_run": WEB_SEARCH_PRICE_PER_RUN,
            "cost_breakdown": {
                "model_cost": float(total.get("model_cost") or 0),
                "web_search_cost": float(total.get("web_search_cost") or 0),
                "total_cost": float(total.get("total_cost") or 0)
            },
            "web_search": {
                "requests": int(total.get("web_search_requests") or 0),
                "calls": int(total.get("web_search_calls") or 0),
                "cost": float(total.get("web_search_cost") or 0),
                "price_per_run": WEB_SEARCH_PRICE_PER_RUN
            }
        })
    except Exception:
        current_app.logger.exception("usage query failed")
        return jsonify({"error": "讀取 Token 統計失敗，請稍後再試。"}), 500
    finally:
        cur.close()
        conn.close()


# ---------- TTS ----------
@bp.route("/tts")
@limiter.limit("10 per minute")
def tts():
    if require_login() is None:
        return jsonify({"error": "請先登入！"}), 401
    text = request.args.get("text", "").strip()
    if not text:
        abort(400, "缺少 text")

    url = "https://api.openai.com/v1/audio/speech"
    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": os.getenv("OPENAI_TTS_MODEL", "tts-1"),
        "input": text[:4000],
        "voice": "nova",
        "response_format": "mp3",
        "speed": 1.0,
    }
    response = requests.post(url, headers=headers, json=payload, timeout=60)
    response.raise_for_status()
    return send_file(io.BytesIO(response.content), mimetype="audio/mpeg", download_name="tts.mp3")
