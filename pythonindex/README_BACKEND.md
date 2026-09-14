# GGPT 模組化後端

此目錄是由原本單一 Flask Python 檔拆分而成，原有 API 路徑保持不變。

## 檔案用途

| 檔案 | 用途 |
| --- | --- |
| `app.py` | Flask Application Factory、Blueprint 註冊與啟動入口 |
| `config.py` | 環境變數、模型價格、上傳限制與副檔名設定 |
| `extensions.py` | OpenAI 與 OpenCC 客戶端 |
| `pricing.py` | Token 與 Web Search 費用計算 |
| `database.py` | MySQL 連線、Schema 更新與共用權限查詢 |
| `audit.py` | 管理員操作紀錄 |
| `traffic_routes.py` | 健康檢查與訪客紀錄 |
| `auth_routes.py` | 註冊、登入、Google 登入與 Session |
| `admin_routes.py` | 管理後台 API |
| `upload_routes.py` | 圖片與附件上傳 |
| `conversation_routes.py` | 對話、分享與訊息 API |
| `global_chat_routes.py` | 全域聊天室 API |
| `ai_routes.py` | AI 回覆、用量統計與 TTS |

## 使用方式

1. 將 `.env.example` 複製為 `.env`，填入資料庫密碼與 API Key。
2. 將既有的 `templates/`、`uploads/` 與資料庫結構保留在此目錄旁。
3. 安裝套件並啟動：

```powershell
python -m venv venv
venv\Scripts\Activate.ps1
pip install -r requirements.txt
python app.py
```

後端預設啟動於 `http://127.0.0.1:8080`。
