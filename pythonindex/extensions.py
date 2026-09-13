"""集中建立第三方服務客戶端，避免各模組重複初始化。"""

from openai import OpenAI
from opencc import OpenCC

from config import OPENAI_API_KEY


if not OPENAI_API_KEY:
    raise RuntimeError("請先設定環境變數 OPENAI_API_KEY")

openai_client = OpenAI(api_key=OPENAI_API_KEY)
traditional_chinese = OpenCC("s2twp")
