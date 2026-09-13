"""MySQL 連線、資料表初始化與權限查詢。"""

import mysql.connector
from flask import session
from werkzeug.security import generate_password_hash

from config import ADMIN_ACCOUNTS, ADMIN_PASSWORD, ADMIN_USERNAME, DB_CONFIG, WEB_SEARCH_PRICE_PER_RUN

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
