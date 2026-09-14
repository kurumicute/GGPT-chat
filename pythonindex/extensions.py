"""集中建立第三方服務客戶端，避免各模組重複初始化。"""

from openai import OpenAI
from opencc import OpenCC
from flask_limiter import Limiter

from config import OPENAI_API_KEY, RATELIMIT_STORAGE_URI
from security import client_ip


if not OPENAI_API_KEY:
    raise RuntimeError("請先設定環境變數 OPENAI_API_KEY")

openai_client = OpenAI(api_key=OPENAI_API_KEY)
traditional_chinese = OpenCC("s2twp")

# memory:// 適合目前的單機部署；多程序或多台主機時請改用 Redis 儲存位置。
limiter = Limiter(
    key_func=client_ip,
    storage_uri=RATELIMIT_STORAGE_URI,
    headers_enabled=True,
)
