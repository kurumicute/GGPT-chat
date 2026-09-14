"""GGPT 後端集中設定。所有機密資料均從 .env 讀取。"""

import os
import secrets
from pathlib import Path

from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")


def env_bool(name, default=False):
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


FLASK_SECRET_KEY = os.getenv("FLASK_SECRET_KEY", "").strip()
SESSION_COOKIE_SECURE = env_bool("SESSION_COOKIE_SECURE", False)
FLASK_HOST = os.getenv("FLASK_HOST", "127.0.0.1")
FLASK_PORT = int(os.getenv("FLASK_PORT", "8080"))
FLASK_DEBUG = env_bool("FLASK_DEBUG", False)
SKIP_DB_INIT = env_bool("FLASK_SKIP_DB_INIT", False)
TRUST_PROXY_COUNT = max(0, int(os.getenv("TRUST_PROXY_COUNT", "1")))
SESSION_LIFETIME_HOURS = max(1, int(os.getenv("SESSION_LIFETIME_HOURS", "12")))
RATELIMIT_STORAGE_URI = os.getenv("RATELIMIT_STORAGE_URI", "memory://").strip() or "memory://"

# 正式環境必須使用固定且不可預測的密鑰；開發環境未設定時才使用每次重啟都會失效的暫時密鑰。
if not FLASK_SECRET_KEY:
    if FLASK_DEBUG:
        FLASK_SECRET_KEY = secrets.token_hex(32)
    else:
        raise RuntimeError("正式環境必須設定至少 32 字元的 FLASK_SECRET_KEY。")
if not FLASK_DEBUG and len(FLASK_SECRET_KEY) < 32:
    raise RuntimeError("正式環境的 FLASK_SECRET_KEY 至少需要 32 字元。")

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "").strip()

ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "").strip()
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "")
ADMIN_ACCOUNTS = os.getenv("ADMIN_ACCOUNTS", "")

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


WEB_SEARCH_PRICE_PER_1K = float(os.getenv("OPENAI_WEB_SEARCH_PRICE_PER_1K", "10"))
WEB_SEARCH_PRICE_PER_RUN = WEB_SEARCH_PRICE_PER_1K / 1000.0

DB_CONFIG = {
    "host": os.getenv("DB_HOST", "127.0.0.1"),
    "user": os.getenv("DB_USER", "root"),
    "password": os.getenv("DB_PASSWORD"),
    "database": os.getenv("DB_NAME", "chat_db"),
}

TRAFFIC_SALT = os.getenv("TRAFFIC_SALT", FLASK_SECRET_KEY)
TRACKED_TRAFFIC_PATHS = {"/", "/login", "/register"}

UPLOAD_FOLDER = str(BASE_DIR / "uploads")
MAX_UPLOAD_MB = max(1, int(os.getenv("MAX_UPLOAD_MB", "50")))
IMAGE_MAX_DIMENSION = max(640, int(os.getenv("IMAGE_MAX_DIMENSION", "2048")))
IMAGE_WEBP_QUALITY = min(95, max(60, int(os.getenv("IMAGE_WEBP_QUALITY", "82"))))

ALLOWED_IMAGE_EXTENSIONS = {
    "png", "jpg", "jpeg", "gif", "webp", "bmp", "ico", "tif", "tiff", "avif"
}

# 一般附件採用「副檔名白名單」而不是只限制少數程式語言，
# 同時涵蓋常見程式碼、設定檔、腳本、文件與工程專案檔。
ALLOWED_FILE_EXTENSIONS = {
    # 純文字 / 文件
    "txt", "text", "md", "markdown", "rst", "csv", "tsv", "log",
    "json", "jsonl", "xml", "rss", "atom", "html", "htm", "xhtml",
    "css", "scss", "sass", "less", "styl", "tex", "bib",
    "ini", "cfg", "conf", "config", "properties", "example",
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
    "readme", "readme.md", "robots.txt", ".gitignore",
    ".gitattributes", ".editorconfig", ".npmrc", ".yarnrc",
    "requirements.txt", "pipfile", "gemfile", "rakefile"
}

GLOBAL_CHAT_MAX_LENGTH = int(os.getenv("GLOBAL_CHAT_MAX_LENGTH", "500"))
GLOBAL_CHAT_HISTORY_LIMIT = int(os.getenv("GLOBAL_CHAT_HISTORY_LIMIT", "50"))

# 帳號與密碼採一般網站常見的長度限制；實際驗證集中在 security.py。
USERNAME_MIN_LENGTH = max(3, int(os.getenv("USERNAME_MIN_LENGTH", "3")))
USERNAME_MAX_LENGTH = min(100, max(USERNAME_MIN_LENGTH, int(os.getenv("USERNAME_MAX_LENGTH", "32"))))
PASSWORD_MIN_LENGTH = max(8, int(os.getenv("PASSWORD_MIN_LENGTH", "8")))
PASSWORD_MAX_LENGTH = min(256, max(PASSWORD_MIN_LENGTH, int(os.getenv("PASSWORD_MAX_LENGTH", "128"))))
CHAT_MESSAGE_MAX_LENGTH = max(1000, int(os.getenv("CHAT_MESSAGE_MAX_LENGTH", "20000")))
CHAT_MAX_ATTACHMENTS = min(20, max(1, int(os.getenv("CHAT_MAX_ATTACHMENTS", "8"))))
OPENAI_MAX_OUTPUT_TOKENS = min(32768, max(1024, int(os.getenv("OPENAI_MAX_OUTPUT_TOKENS", "16000"))))

# 留空代表僅允許同源請求；需要額外前端網域時以逗號分隔完整 Origin。
ALLOWED_ORIGINS = {
    item.strip().rstrip("/")
    for item in os.getenv("ALLOWED_ORIGINS", "").split(",")
    if item.strip()
}
TRUSTED_HOSTS = [
    item.strip()
    for item in os.getenv("TRUSTED_HOSTS", "").split(",")
    if item.strip()
] or None
