from flask import Flask, request, jsonify, render_template, abort, send_from_directory, send_file, session
import io
import os
import uuid
import hashlib
import threading
import json
import logging
from logging.handlers import RotatingFileHandler
from flask import g
from datetime import datetime

import requests
import mysql.connector
from mysql.connector import Error
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from opencc import OpenCC
from openai import OpenAI
from google.oauth2 import id_token
from google.auth.transport import requests as google_requests
from dotenv import load_dotenv

try:
    from PIL import Image, ImageOps
except ImportError:
    Image = None
    ImageOps = None

load_dotenv()

app = Flask(__name__)

app.secret_key = os.getenv(
    "FLASK_SECRET_KEY",
    "change-this-secret-key"
)

app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=False,
)

# ---------- Admin operation log ----------
# 管理員操作紀錄獨立存放在專案的 admin_logs/ 目錄。
# 每筆事件一行 JSON，並以 rotating file 避免 log 無限增長。
ADMIN_LOG_DIR = os.path.join(app.root_path, "admin_logs")
os.makedirs(ADMIN_LOG_DIR, exist_ok=True)
ADMIN_LOG_PATH = os.path.join(ADMIN_LOG_DIR, "admin_activity.log")

admin_logger = logging.getLogger("admin_activity")
admin_logger.setLevel(logging.INFO)
admin_logger.propagate = False
if not admin_logger.handlers:
    _admin_file_handler = RotatingFileHandler(
        ADMIN_LOG_PATH, maxBytes=10 * 1024 * 1024, backupCount=10, encoding="utf-8"
    )
    _admin_file_handler.setFormatter(logging.Formatter("%(message)s"))
    admin_logger.addHandler(_admin_file_handler)


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


@app.before_request
def capture_admin_actor():
    if request.path.startswith("/api/admin/"):
        g.admin_log_actor_id = session.get("admin_id")
        g.admin_log_actor_username = session.get("admin_username")


@app.after_request
def audit_admin_request(response):
    _write_admin_log(response.status_code)
    return response

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
if not OPENAI_API_KEY:
    raise RuntimeError("請先設定環境變數 OPENAI_API_KEY")

client = OpenAI(api_key=OPENAI_API_KEY)

# Google Identity Services / Sign in with Google
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "").strip()

# 管理員設定
# 管理員帳號改由 MySQL 的 admin_users 資料表管理。
# 舊版 ADMIN_USERNAME / ADMIN_PASSWORD 僅作為第一次啟動時的相容引導，
# 會在 admin_users 尚無任何帳號時，自動建立第一個管理員並以雜湊密碼保存。
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "").strip()
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "")
ADMIN_ACCOUNTS = os.getenv("ADMIN_ACCOUNTS", "")

# Token / 成本設定（USD / 1M tokens）。可用環境變數覆蓋。
TOKEN_QUOTA = int(os.getenv("USER_TOKEN_QUOTA", "0") or 0)
MODEL_PRICING = {
    "gpt-5-nano": {
        "input": float(os.getenv("OPENAI_PRICE_GPT_5_NANO_INPUT", "0.05")),
        "cached_input": float(os.getenv("OPENAI_PRICE_GPT_5_NANO_CACHED_INPUT", "0.005")),
        "output": float(os.getenv("OPENAI_PRICE_GPT_5_NANO_OUTPUT", "0.40")),
    },
    "gpt-6-astra": {
        "input": float(os.getenv("OPENAI_PRICE_GPT_6_ASTRA_INPUT", "10")),
        "cached_input": float(os.getenv("OPENAI_PRICE_GPT_6_ASTRA_CACHED_INPUT", "1")),
        "output": float(os.getenv("OPENAI_PRICE_GPT_6_ASTRA_OUTPUT", "50")),
    },
    "gpt-5.6-sol": {
        "input": float(os.getenv("OPENAI_PRICE_GPT_5_6_SOL_INPUT", "4")),
        "cached_input": float(os.getenv("OPENAI_PRICE_GPT_5_6_SOL_CACHED_INPUT", "0.4")),
        "output": float(os.getenv("OPENAI_PRICE_GPT_5_6_SOL_OUTPUT", "20")),
    },
    "gpt-5.6-terra": {
        "input": float(os.getenv("OPENAI_PRICE_GPT_5_6_TERRA_INPUT", "2")),
        "cached_input": float(os.getenv("OPENAI_PRICE_GPT_5_6_TERRA_CACHED_INPUT", "0.2")),
        "output": float(os.getenv("OPENAI_PRICE_GPT_5_6_TERRA_OUTPUT", "12")),
    },
    "gpt-5.6-luna": {
        "input": float(os.getenv("OPENAI_PRICE_GPT_5_6_LUNA_INPUT", "0.2")),
        "cached_input": float(os.getenv("OPENAI_PRICE_GPT_5_6_LUNA_CACHED_INPUT", "0.02")),
        "output": float(os.getenv("OPENAI_PRICE_GPT_5_6_LUNA_OUTPUT", "1.2")),
    },
}


def get_model_pricing(model):
    return MODEL_PRICING.get(model, {"input": 0.0, "cached_input": 0.0, "output": 0.0})


# OpenAI Web Search 工具費：USD $10 / 1,000 次 web run。
# 搜尋所產生的內容 token 仍依模型 token 價格計費。
WEB_SEARCH_PRICE_PER_1K = float(os.getenv("OPENAI_WEB_SEARCH_PRICE_PER_1K", "10"))
WEB_SEARCH_PRICE_PER_RUN = WEB_SEARCH_PRICE_PER_1K / 1000.0


def calculate_model_cost(model, input_tokens, output_tokens, cached_input_tokens=0):
    """只計算模型 token 成本，不包含 Web Search。"""
    price = get_model_pricing(model)
    normal_input = max(0, int(input_tokens or 0) - int(cached_input_tokens or 0))
    cached = max(0, int(cached_input_tokens or 0))
    output = max(0, int(output_tokens or 0))
    return (
        normal_input * price["input"]
        + cached * price["cached_input"]
        + output * price["output"]
    ) / 1_000_000.0


def calculate_web_search_cost(web_search_calls=0):
    return max(0, int(web_search_calls or 0)) * WEB_SEARCH_PRICE_PER_RUN


cc = OpenCC("s2twp")

DB_CONFIG = {
    "host": os.getenv("DB_HOST", "127.0.0.1"),
    "user": os.getenv("DB_USER", "root"),
    "password": os.getenv("DB_PASSWORD"),
    "database": os.getenv("DB_NAME", "chat_db"),
}

TRAFFIC_SALT = os.getenv("TRAFFIC_SALT", app.secret_key)
TRACKED_TRAFFIC_PATHS = {"/", "/login", "/register"}

UPLOAD_FOLDER = os.path.join(app.root_path, "uploads")
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER
MAX_UPLOAD_MB = max(1, int(os.getenv("MAX_UPLOAD_MB", "50")))
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_MB * 1024 * 1024
IMAGE_MAX_DIMENSION = max(640, int(os.getenv("IMAGE_MAX_DIMENSION", "2048")))
IMAGE_WEBP_QUALITY = min(95, max(60, int(os.getenv("IMAGE_WEBP_QUALITY", "82"))))

@app.errorhandler(413)
def request_entity_too_large(error):
    return jsonify({
        "success": False,
        "error": f"單次上傳請小於 {MAX_UPLOAD_MB}MB。"
    }), 413

ALLOWED_IMAGE_EXTENSIONS = {
    "png", "jpg", "jpeg", "gif", "webp", "bmp", "svg", "ico", "tif", "tiff", "avif"
}

# 一般附件採用「副檔名白名單」而不是只限制少數程式語言，
# 同時涵蓋常見程式碼、設定檔、腳本、文件與工程專案檔。
ALLOWED_FILE_EXTENSIONS = {
    # 純文字 / 文件
    "txt", "text", "md", "markdown", "rst", "csv", "tsv", "log",
    "json", "jsonl", "xml", "rss", "atom", "html", "htm", "xhtml",
    "css", "scss", "sass", "less", "styl", "tex", "bib",
    "ini", "cfg", "conf", "config", "properties", "env", "example",
    "toml", "yaml", "yml",

    # JavaScript / Web / Frontend
    "js", "mjs", "cjs", "jsx", "ts", "mts", "cts", "tsx",
    "vue", "svelte", "astro", "astrojs",

    # Python / JVM / C-family / 編譯型語言
    "py", "pyw", "pyi", "ipynb",
    "java", "kt", "kts", "scala", "groovy",
    "c", "h", "cc", "cpp", "cxx", "hpp", "hh", "hxx",
    "cs", "csx", "fs", "fsx", "vb",
    "go", "rs", "swift", "m", "mm",
    "dart", "lua", "r", "pl", "pm", "rb", "php",
    "zig", "nim", "d", "ex", "exs", "erl", "hrl",

    # Shell / Windows / automation
    "sh", "bash", "zsh", "fish", "ksh", "csh",
    "bat", "cmd", "ps1", "psm1", "psd1",
    "vbs", "vbe", "wsf",

    # SQL / API / Schema
    "sql", "ddl", "dml", "prisma", "graphql", "gql", "proto",

    # Build / DevOps / 工程設定
    "dockerfile", "containerfile", "cmake", "make", "mk",
    "gradle", "gradle.kts", "sbt", "lock",
    "gitignore", "gitattributes", "editorconfig",
    "prettierrc", "eslintrc",

    # Assembly / embedded / hardware
    "asm", "s", "inc", "ino", "hex", "sv", "v", "vh", "vhd", "vhdl",

    # Data / notebooks / misc
    "sql", "dbml", "csv", "tsv", "geojson", "graphql",
}

# 沒有副檔名但常見於開發專案的特殊檔名。
ALLOWED_SPECIAL_FILENAMES = {
    "dockerfile", "containerfile", "makefile", "license", "licence",
    "readme", "readme.md", "robots.txt", ".env", ".gitignore",
    ".gitattributes", ".editorconfig", ".npmrc", ".yarnrc",
    "requirements.txt", "pipfile", "gemfile", "rakefile"
}

GLOBAL_CHAT_MAX_LENGTH = int(os.getenv("GLOBAL_CHAT_MAX_LENGTH", "500"))
GLOBAL_CHAT_HISTORY_LIMIT = int(os.getenv("GLOBAL_CHAT_HISTORY_LIMIT", "50"))


# ---------- DB helpers ----------

def get_conn():
    return mysql.connector.connect(**DB_CONFIG)


def ensure_column(cur, table_name, column_name, alter_sql):
    cur.execute(f"SHOW COLUMNS FROM `{table_name}` LIKE %s", (column_name,))
    if cur.fetchone() is None:
        cur.execute(alter_sql)


def _configured_admin_accounts():
    """讀取環境變數中的多管理員設定。格式：user1:pass1,user2:pass2"""
    accounts = []

    for item in ADMIN_ACCOUNTS.split(","):
        item = item.strip()
        if not item or ":" not in item:
            continue
        username, password = item.split(":", 1)
        username = username.strip()
        password = password.strip()
        if username and password:
            accounts.append((username, password))

    # 向下相容舊版單一管理員環境變數。
    if ADMIN_USERNAME and ADMIN_PASSWORD:
        accounts.append((ADMIN_USERNAME, ADMIN_PASSWORD))

    # 同一個 username 只保留最後一組設定。
    deduped = {}
    for username, password in accounts:
        deduped[username] = password
    return list(deduped.items())


def ensure_admin_schema(conn, cur):
    """建立多管理員資料表，並將環境變數管理員同步到資料庫。"""
    cur.execute("""
        CREATE TABLE IF NOT EXISTS admin_users (
            id INT AUTO_INCREMENT PRIMARY KEY,
            username VARCHAR(100) NOT NULL UNIQUE,
            password_hash VARCHAR(255) NOT NULL,
            is_active TINYINT(1) NOT NULL DEFAULT 1,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            last_login_at TIMESTAMP NULL DEFAULT NULL,
            INDEX idx_admin_active (is_active)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """)

    # 啟動時只補上不存在的帳號；不會刪除資料庫中既有的管理員。
    for username, password in _configured_admin_accounts():
        cur.execute(
            "SELECT id FROM admin_users WHERE username=%s LIMIT 1",
            (username,),
        )
        if cur.fetchone() is None:
            cur.execute(
                """
                INSERT INTO admin_users (username, password_hash, is_active)
                VALUES (%s, %s, 1)
                """,
                (username, generate_password_hash(password)),
            )

    conn.commit()


def ensure_schema():
    conn = get_conn()
    cur = conn.cursor(dictionary=True)
    try:
        ensure_admin_schema(conn, cur)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS conversations (
                id INT AUTO_INCREMENT PRIMARY KEY,
                user_id INT NOT NULL,
                title VARCHAR(255) NOT NULL DEFAULT '新對話',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                INDEX idx_conv_user_updated (user_id, updated_at)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """)

        # 對話分享：share_token 是網址中的不可預測 UUID；協作者加入後才取得存取權。
        cur.execute("""
            CREATE TABLE IF NOT EXISTS conversation_shares (
                conversation_id INT NOT NULL PRIMARY KEY,
                share_token CHAR(36) NOT NULL UNIQUE,
                created_by INT NOT NULL,
                is_active TINYINT(1) NOT NULL DEFAULT 1,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                INDEX idx_conversation_share_token (share_token, is_active)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS conversation_collaborators (
                conversation_id INT NOT NULL,
                user_id INT NOT NULL,
                joined_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (conversation_id, user_id),
                INDEX idx_conversation_collaborator_user (user_id, joined_at)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS token_usage (
                id BIGINT AUTO_INCREMENT PRIMARY KEY,
                user_id INT NOT NULL,
                conversation_id INT NULL,
                model VARCHAR(100) NOT NULL,
                input_tokens BIGINT NOT NULL DEFAULT 0,
                cached_input_tokens BIGINT NOT NULL DEFAULT 0,
                output_tokens BIGINT NOT NULL DEFAULT 0,
                reasoning_tokens BIGINT NOT NULL DEFAULT 0,
                total_tokens BIGINT NOT NULL DEFAULT 0,
                total_cost DECIMAL(18,8) NOT NULL DEFAULT 0,
                reasoning_cost DECIMAL(18,8) NOT NULL DEFAULT 0,
                web_search TINYINT(1) NOT NULL DEFAULT 0,
                web_search_calls SMALLINT UNSIGNED NOT NULL DEFAULT 0,
                web_search_cost DECIMAL(18,8) NOT NULL DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                INDEX idx_usage_user_time (user_id, created_at),
                INDEX idx_usage_user_model (user_id, model)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """)

        ensure_column(
            cur, "token_usage", "web_search_calls",
            "ALTER TABLE token_usage ADD COLUMN web_search_calls SMALLINT UNSIGNED NOT NULL DEFAULT 0 AFTER web_search"
        )
        ensure_column(
            cur, "token_usage", "web_search_cost",
            "ALTER TABLE token_usage ADD COLUMN web_search_cost DECIMAL(18,8) NOT NULL DEFAULT 0 AFTER web_search_calls"
        )
        ensure_column(
            cur, "token_usage", "reasoning_tokens",
            "ALTER TABLE token_usage ADD COLUMN reasoning_tokens BIGINT NOT NULL DEFAULT 0 AFTER output_tokens"
        )
        ensure_column(
            cur, "token_usage", "reasoning_cost",
            "ALTER TABLE token_usage ADD COLUMN reasoning_cost DECIMAL(18,8) NOT NULL DEFAULT 0 AFTER total_cost"
        )

        # 舊版只有 web_search 旗標時，只補欄位資訊，不再重加 total_cost。
        # 舊版 total_cost 已可能包含 Web Search 費用，重加會造成管理員看到的總額被算兩次。
        cur.execute("""
            UPDATE token_usage
            SET web_search_calls=1,
                web_search_cost=%s
            WHERE web_search=1 AND web_search_calls=0
        """, (WEB_SEARCH_PRICE_PER_RUN,))

        # 舊資料若誤記成多個 web_search_call，本系統計費口徑固定以每個聊天請求 1 次計。
        cur.execute("""
            UPDATE token_usage
            SET total_cost = GREATEST(0, total_cost - ((web_search_calls - 1) * %s)),
                web_search_calls = 1,
                web_search_cost = %s
            WHERE web_search_calls > 1
        """, (WEB_SEARCH_PRICE_PER_RUN, WEB_SEARCH_PRICE_PER_RUN))

        ensure_column(
            cur, "chat", "conversation_id",
            "ALTER TABLE chat ADD COLUMN conversation_id INT NULL AFTER user_id"
        )
        ensure_column(
            cur, "chat", "attachment_json",
            "ALTER TABLE chat ADD COLUMN attachment_json LONGTEXT NULL AFTER image_url"
        )
        ensure_column(
            cur, "chat", "reasoning_summary",
            "ALTER TABLE chat ADD COLUMN reasoning_summary LONGTEXT NULL AFTER attachment_json"
        )

        cur.execute("""
            CREATE INDEX idx_chat_conv ON chat (conversation_id)
        """) if not index_exists(cur, "chat", "idx_chat_conv") else None

        # Google 登入欄位
        for column, alter_sql in {
            "google_sub": "ALTER TABLE users ADD COLUMN google_sub VARCHAR(255) NULL AFTER password_hash",
            "email": "ALTER TABLE users ADD COLUMN email VARCHAR(320) NULL AFTER google_sub",
            "display_name": "ALTER TABLE users ADD COLUMN display_name VARCHAR(255) NULL AFTER email",
            "avatar_url": "ALTER TABLE users ADD COLUMN avatar_url VARCHAR(1024) NULL AFTER display_name",
            "auth_provider": "ALTER TABLE users ADD COLUMN auth_provider VARCHAR(20) NOT NULL DEFAULT 'local' AFTER avatar_url",
        }.items():
            ensure_column(cur, "users", column, alter_sql)

        if not index_exists(cur, "users", "uq_users_google_sub"):
            try:
                cur.execute("CREATE UNIQUE INDEX uq_users_google_sub ON users (google_sub)")
            except mysql.connector.Error:
                # 若舊資料有重複非 NULL google_sub，網站仍可啟動；管理者需自行清理後再建立唯一索引。
                pass

        cur.execute("""
            CREATE TABLE IF NOT EXISTS visitor_events (
                id BIGINT AUTO_INCREMENT PRIMARY KEY,
                visitor_hash CHAR(64) NOT NULL,
                path VARCHAR(255) NOT NULL,
                referrer VARCHAR(1024) NULL,
                user_agent VARCHAR(1024) NULL,
                device_type VARCHAR(30) NOT NULL DEFAULT 'unknown',
                browser VARCHAR(50) NOT NULL DEFAULT 'unknown',
                os VARCHAR(50) NOT NULL DEFAULT 'unknown',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                INDEX idx_visitor_time (created_at),
                INDEX idx_visitor_hash_time (visitor_hash, created_at),
                INDEX idx_visitor_path_time (path, created_at)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """)

        # 全域聊天室
        cur.execute("""
            CREATE TABLE IF NOT EXISTS global_chat_messages (
                id BIGINT AUTO_INCREMENT PRIMARY KEY,
                user_id INT NOT NULL,
                message TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                INDEX idx_global_chat_id (id),
                INDEX idx_global_chat_user (user_id),
                INDEX idx_global_chat_created (created_at),
                CONSTRAINT fk_global_chat_user
                    FOREIGN KEY (user_id) REFERENCES users(id)
                    ON DELETE CASCADE
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """)

        conn.commit()
    finally:
        cur.close()
        conn.close()


def index_exists(cur, table_name, index_name):
    cur.execute(f"SHOW INDEX FROM `{table_name}` WHERE Key_name=%s", (index_name,))
    return cur.fetchone() is not None


def ensure_user_has_conversation(user_id):
    conn = get_conn()
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute("SELECT id FROM conversations WHERE user_id=%s ORDER BY id ASC LIMIT 1", (user_id,))
        conv = cur.fetchone()
        if not conv:
            cur.execute("INSERT INTO conversations (user_id,title) VALUES (%s,%s)", (user_id, "新對話"))
            conv_id = cur.lastrowid
        else:
            conv_id = conv["id"]

        cur.execute("""
            UPDATE chat
            SET conversation_id=%s
            WHERE user_id=%s AND conversation_id IS NULL
        """, (conv_id, user_id))
        conn.commit()
        return conv_id
    finally:
        cur.close()
        conn.close()


def require_login():
    return session.get("user_id") if session.get("user_id") is not None else None


def owned_conversation(cur, user_id, conversation_id):
    cur.execute(
        "SELECT id,user_id,title,created_at,updated_at FROM conversations WHERE id=%s AND user_id=%s",
        (conversation_id, user_id),
    )
    return cur.fetchone()


def accessible_conversation(cur, user_id, conversation_id):
    """回傳使用者可存取的自有或已加入共享對話。"""
    cur.execute(
        """
        SELECT
            c.id,
            c.user_id,
            c.title,
            c.created_at,
            c.updated_at,
            CASE WHEN c.user_id=%s THEN 1 ELSE 0 END AS is_owner
        FROM conversations c
        LEFT JOIN conversation_collaborators member
            ON member.conversation_id=c.id AND member.user_id=%s
        LEFT JOIN conversation_shares active_share
            ON active_share.conversation_id=c.id AND active_share.is_active=1
        WHERE c.id=%s
          AND (
              c.user_id=%s
              OR (member.user_id IS NOT NULL AND active_share.conversation_id IS NOT NULL)
          )
        LIMIT 1
        """,
        (user_id, user_id, conversation_id, user_id),
    )
    return cur.fetchone()


def _request_client_ip():
    forwarded = request.headers.get("X-Forwarded-For", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.remote_addr or "unknown"


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


@app.before_request
def track_visitor():
    _record_visitor_event()


# ---------- app ----------
@app.route("/health")
def health():
    return jsonify({"success": True, "service": "GGPT Flask API"})


#@app.route("/")
#def index():
#   return jsonify({"success": True, "service": "Not this time"})


@app.route("/register", methods=["POST"])
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


@app.route("/login", methods=["POST"])
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


@app.route("/check_session")
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
@app.route("/auth/google/config")
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


@app.route("/auth/google", methods=["POST"])
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


@app.route("/logout")
def logout():
    session.clear()
    return jsonify({"success": True})


# ---------- Admin ----------
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


@app.route("/admin-legacy")
def admin_index_legacy():
    return render_template("adminindex.html")


@app.route("/api/admin/login", methods=["POST"])
def admin_login():
    data = request.get_json(silent=True) or {}
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

        if not admin or not bool(admin.get("is_active")):
            return admin_error("管理員帳號或密碼錯誤。", 401)

        if not check_password_hash(admin["password_hash"], password):
            return admin_error("管理員帳號或密碼錯誤。", 401)

        cur.execute(
            "UPDATE admin_users SET last_login_at=CURRENT_TIMESTAMP WHERE id=%s",
            (admin["id"],),
        )
        conn.commit()

        session["is_admin"] = True
        session["admin_username"] = admin["username"]
        session["admin_id"] = int(admin["id"])

        return jsonify({"success": True, "username": admin["username"]})
    except Exception as e:
        conn.rollback()
        return admin_error(f"管理員登入失敗：{e}", 500)
    finally:
        cur.close()
        conn.close()


@app.route("/api/admin/check")
def admin_check():
    return jsonify({"logged_in": require_admin(), "username": session.get("admin_username")})


@app.route("/api/admin/logout")
def admin_logout():
    session.pop("is_admin", None)
    session.pop("admin_username", None)
    session.pop("admin_id", None)
    return jsonify({"success": True})


@app.route("/api/admin/dashboard")
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
    except Exception as e:
        return admin_error(f"讀取管理統計失敗：{e}", 500)
    finally:
        cur.close()
        conn.close()


@app.route("/api/admin/traffic")
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
    except Exception as e:
        return admin_error(f"讀取訪客流量失敗：{e}", 500)
    finally:
        cur.close()
        conn.close()


@app.route("/api/admin/users", methods=["POST"])
def admin_create_user():
    if not require_admin():
        return admin_error("沒有管理員權限。", 403)

    data = request.get_json(silent=True) or {}
    username = str(data.get("username") or "").strip()
    password = str(data.get("password") or "")
    display_name = str(data.get("display_name") or username).strip()[:255]
    email = str(data.get("email") or "").strip().lower()[:320]

    if not username or not password:
        return admin_error("帳號與密碼都不能為空。", 400)
    if len(username) > 100:
        return admin_error("帳號最多 100 個字元。", 400)
    if len(password) < 4:
        return admin_error("密碼至少需要 4 個字元。", 400)

    conn = get_conn()
    cur = conn.cursor()
    try:
        cur.execute("""
            INSERT INTO users
            (username,password_hash,email,display_name,auth_provider)
            VALUES (%s,%s,%s,%s,%s)
        """, (username, generate_password_hash(password), email or None,
              display_name or username, "local"))
        user_id = cur.lastrowid
        conn.commit()
        return jsonify({"success": True, "id": user_id})
    except mysql.connector.IntegrityError:
        conn.rollback()
        return admin_error("帳號已存在，請換一個帳號。", 400)
    except Exception as e:
        conn.rollback()
        return admin_error(f"新增使用者失敗：{e}", 500)
    finally:
        cur.close()
        conn.close()


@app.route("/api/admin/users/<int:user_id>", methods=["PATCH"])
def admin_update_user(user_id):
    if not require_admin():
        return admin_error("沒有管理員權限。", 403)

    data = request.get_json(silent=True) or {}
    username = str(data.get("username") or "").strip()
    password = str(data.get("password") or "")
    display_name = str(data.get("display_name") or "").strip()[:255]
    email = str(data.get("email") or "").strip().lower()[:320]

    if username and len(username) > 100:
        return admin_error("帳號最多 100 個字元。", 400)
    if password and len(password) < 4:
        return admin_error("新密碼至少需要 4 個字元。", 400)

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
        if password:
            updates.append("password_hash=%s")
            values.append(generate_password_hash(password))
        if display_name:
            updates.append("display_name=%s")
            values.append(display_name)
        if "email" in data:
            updates.append("email=%s")
            values.append(email or None)

        if not updates:
            return admin_error("沒有提供任何要修改的欄位。", 400)

        values.append(user_id)
        cur.execute(f"UPDATE users SET {', '.join(updates)} WHERE id=%s", tuple(values))
        conn.commit()
        return jsonify({"success": True})
    except mysql.connector.IntegrityError:
        conn.rollback()
        return admin_error("帳號已被其他使用者使用。", 400)
    except Exception as e:
        conn.rollback()
        return admin_error(f"修改使用者失敗：{e}", 500)
    finally:
        cur.close()
        conn.close()


@app.route("/api/admin/users/<int:user_id>", methods=["DELETE"])
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
    except Exception as e:
        conn.rollback()
        return admin_error(f"刪除使用者失敗：{e}", 500)
    finally:
        cur.close()
        conn.close()


# ---------- Upload ----------
def _normalized_filename(filename):
    raw = os.path.basename(str(filename or "")).strip()
    safe = secure_filename(raw) or "file"
    return raw, safe


def allowed_image_file(filename):
    raw, safe = _normalized_filename(filename)
    ext = safe.rsplit(".", 1)[1].lower() if "." in safe else ""
    return ext in ALLOWED_IMAGE_EXTENSIONS


def allowed_attachment_file(filename):
    raw, safe = _normalized_filename(filename)
    lower_name = raw.lower()
    safe_lower = safe.lower()

    # 支援無副檔名的特殊開發檔案。
    if lower_name in ALLOWED_SPECIAL_FILENAMES or safe_lower in ALLOWED_SPECIAL_FILENAMES:
        return True

    ext = safe.rsplit(".", 1)[1].lower() if "." in safe else ""
    return bool(ext) and ext in ALLOWED_FILE_EXTENSIONS


def is_uploaded_image(filename, mime_type=""):
    # 圖片判斷以允許的副檔名為主，避免只因瀏覽器 MIME 宣告為 image/* 就放行其他格式。
    return allowed_image_file(filename)


def build_upload_name(filename):
    safe = secure_filename(filename) or "file"
    return f"{uuid.uuid4().hex}_{safe}"


def store_upload(file, safe_name, kind):
    """儲存上傳內容；一般靜態圖片會縮圖並轉為 WebP。"""
    extension = safe_name.rsplit(".", 1)[1].lower() if "." in safe_name else ""

    if kind == "image" and Image is not None and extension in {
        "png", "jpg", "jpeg", "webp", "bmp", "tif", "tiff"
    }:
        try:
            file.stream.seek(0)
            with Image.open(file.stream) as source:
                # 動圖保持原格式，避免只保留第一幀。
                if getattr(source, "is_animated", False):
                    raise ValueError("animated image")

                image = ImageOps.exif_transpose(source)
                image.thumbnail(
                    (IMAGE_MAX_DIMENSION, IMAGE_MAX_DIMENSION),
                    Image.Resampling.LANCZOS
                )
                if image.mode not in {"RGB", "RGBA"}:
                    image = image.convert("RGBA" if "transparency" in image.info else "RGB")

                optimized_name = f"{uuid.uuid4().hex}.webp"
                optimized_path = os.path.join(app.config["UPLOAD_FOLDER"], optimized_name)
                image.save(
                    optimized_path,
                    "WEBP",
                    quality=IMAGE_WEBP_QUALITY,
                    method=6
                )
                return optimized_name, optimized_path, "image/webp"
        except Exception:
            # Pillow 未支援的格式或壓縮失敗時保留原檔，不中斷使用者上傳。
            file.stream.seek(0)

    stored_name = build_upload_name(safe_name)
    stored_path = os.path.join(app.config["UPLOAD_FOLDER"], stored_name)
    file.save(stored_path)
    return stored_name, stored_path, file.mimetype or "application/octet-stream"


def read_text_file(path):
    encodings = ["utf-8", "utf-8-sig", "big5", "cp950", "latin-1"]
    for encoding in encodings:
        try:
            with open(path, "r", encoding=encoding) as f:
                text = f.read()
            return text[:200000]
        except UnicodeDecodeError:
            continue
        except OSError:
            return None
    return None


@app.route("/upload", methods=["POST"])
def upload():
    """統一的圖片 / 一般檔案上傳 API。支援一次上傳多個檔案。"""
    if require_login() is None:
        return jsonify({"success": False, "error": "請先登入！"}), 401

    incoming = request.files.getlist("files") or request.files.getlist("file")
    if not incoming:
        return jsonify({"success": False, "error": "沒有檔案"}), 400

    uploaded = []

    for file in incoming:
        if not file or not file.filename:
            continue

        original_name, safe_name = _normalized_filename(file.filename)
        mime_type = file.mimetype or "application/octet-stream"

        # 圖片與一般附件共用同一個端點，但仍做基本副檔名驗證。
        if is_uploaded_image(original_name, mime_type):
            if not allowed_image_file(original_name) and not mime_type.startswith("image/"):
                return jsonify({
                    "success": False,
                    "error": f"不支援的圖片格式：{original_name}"
                }), 400
            kind = "image"
        else:
            if not allowed_attachment_file(original_name):
                return jsonify({
                    "success": False,
                    "error": f"不支援的檔案類型：{original_name}"
                }), 400
            kind = "file"

        unique_filename, path, stored_mime_type = store_upload(file, safe_name, kind)

        size = os.path.getsize(path)
        content = None

        # 圖片不讀成文字；其餘檔案若看起來像文字/程式檔則讀取前 200,000 字元。
        if kind == "file":
            ext = safe_name.rsplit(".", 1)[1].lower() if "." in safe_name else ""
            if ext in ALLOWED_FILE_EXTENSIONS or safe_name.lower() in ALLOWED_SPECIAL_FILENAMES:
                content = read_text_file(path)

        item = {
            "success": True,
            "kind": kind,
            "name": original_name[:255],
            "safe_name": safe_name[:255],
            "size": size,
            "mime_type": stored_mime_type[:255],
            "content": content,
            "readable": content is not None,
        }

        if kind == "image":
            item["image_url"] = f"/uploads/{unique_filename}"
            item["url"] = item["image_url"]
        else:
            item["file_url"] = f"/uploads/{unique_filename}"
            item["url"] = item["file_url"]

        uploaded.append(item)

    if not uploaded:
        return jsonify({"success": False, "error": "沒有可上傳的檔案。"}), 400

    return jsonify({
        "success": True,
        "files": uploaded,
        # 單一檔案時直接提供向下相容欄位。
        **(uploaded[0] if len(uploaded) == 1 else {})
    })


# 舊版 API 保留，避免既有前端 / 書籤暫時壞掉；實際上都轉送到同一個處理邏輯。
@app.route("/upload_image", methods=["POST"])
def upload_image_legacy():
    if require_login() is None:
        return jsonify({"success": False, "error": "請先登入！"}), 401

    file = request.files.get("image")
    if not file or not file.filename:
        return jsonify({"success": False, "error": "沒有圖片檔案"}), 400

    if not allowed_image_file(file.filename) and not (file.mimetype or "").startswith("image/"):
        return jsonify({"success": False, "error": "不支援的圖片格式"}), 400

    original_name, safe_name = _normalized_filename(file.filename)
    unique_filename, path, stored_mime_type = store_upload(file, safe_name, "image")
    return jsonify({
        "success": True,
        "image_url": f"/uploads/{unique_filename}",
        "url": f"/uploads/{unique_filename}",
        "name": original_name[:255],
        "mime_type": stored_mime_type
    })


@app.route("/upload_file", methods=["POST"])
def upload_file_legacy():
    if require_login() is None:
        return jsonify({"success": False, "error": "請先登入！"}), 401

    file = request.files.get("file")
    if not file or not file.filename:
        return jsonify({"success": False, "error": "沒有檔案"}), 400

    original_name, safe_name = _normalized_filename(file.filename)
    if not allowed_attachment_file(original_name):
        return jsonify({"success": False, "error": "不支援此檔案類型"}), 400

    unique_filename, path, stored_mime_type = store_upload(file, safe_name, "file")

    extension = safe_name.rsplit(".", 1)[1].lower() if "." in safe_name else ""
    content = read_text_file(path) if extension in ALLOWED_FILE_EXTENSIONS or safe_name.lower() in ALLOWED_SPECIAL_FILENAMES else None

    return jsonify({
        "success": True,
        "file_url": f"/uploads/{unique_filename}",
        "url": f"/uploads/{unique_filename}",
        "name": original_name[:255],
        "file_name": original_name[:255],
        "size": os.path.getsize(path),
        "mime_type": stored_mime_type,
        "content": content,
        "readable": content is not None,
        "message": None if content is not None else "此格式已上傳，但目前後端不直接解析內容。"
    })


@app.route("/uploads/<path:filename>")
def uploaded_file(filename):
    response = send_from_directory(
        app.config["UPLOAD_FOLDER"],
        filename,
        conditional=True,
        max_age=31536000
    )
    # 檔名含 UUID，內容更新時 URL 也會改變，因此可安全長期快取。
    response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    return response


# ---------- conversation APIs ----------
@app.route("/conversations", methods=["GET"])
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


@app.route("/conversations", methods=["POST"])
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


@app.route("/conversations/<int:conversation_id>/share", methods=["POST"])
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


@app.route("/conversations/<int:conversation_id>/share", methods=["DELETE"])
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


@app.route("/shared-conversations/<share_token>/join", methods=["POST"])
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


@app.route("/conversations/<int:conversation_id>", methods=["PATCH"])
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


@app.route("/conversations/<int:conversation_id>", methods=["DELETE"])
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


@app.route("/conversations/<int:conversation_id>/messages", methods=["GET"])
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

@app.route("/api/global_chat/messages", methods=["GET"])
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


@app.route("/api/global_chat/messages", methods=["POST"])
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

def normalize_attachment(attachment):
    if not isinstance(attachment, dict):
        return None
    name = str(attachment.get("name") or "附件")[:255]
    mime_type = str(attachment.get("mime_type") or "application/octet-stream")[:255]
    url = str(attachment.get("url") or attachment.get("file_url") or "")[:1024]
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


@app.route("/chat", methods=["POST"])
def chat():
    user_id = require_login()
    if user_id is None:
        return jsonify({"error": "請先登入！"}), 401

    data = request.get_json(silent=True) or {}
    conversation_id = data.get("conversation_id")
    user_message = (data.get("message") or "").strip()
    image_url = data.get("image_url")

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

    attachments = [x for x in (normalize_attachment(a) for a in raw_attachments) if x]

    if not conversation_id:
        conversation_id = ensure_user_has_conversation(user_id)

    conn = get_conn()
    cur = conn.cursor(dictionary=True)
    try:
        conv = accessible_conversation(cur, user_id, int(conversation_id))
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
            "max_output_tokens": 100000,
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
    except Exception as e:
        conn.rollback()
        return jsonify({"error": str(e)}), 500
    finally:
        cur.close()
        conn.close()


# ---------- Token usage / cost ----------
@app.route("/usage")
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
    except Exception as e:
        return jsonify({"error": f"讀取 Token 統計失敗：{e}"}), 500
    finally:
        cur.close()
        conn.close()


# ---------- TTS ----------
@app.route("/tts")
def tts():
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


# ---------- start ----------
with app.app_context():
    ensure_schema()


if __name__ == "__main__":
    app.run(debug=True, port=8080)
