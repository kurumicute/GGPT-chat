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
| `role_sessions.py` | 將使用者與管理員的 Cookie 隔離，避免登入和登出互相影響 |
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

使用者工作階段使用 `ggpt_session`，管理員使用 `ggpt_admin_session`。更新後需重新啟動後端，管理員首次進入需重新登入；兩種身分的登入、登出及工作階段刷新互不影響。

可選模型的費率集中於 `config.py`，新增 GPT-6 Luna、GPT-6 Sol、GPT-6.1 Sol，並移除 GPT-5 Nano。MySQL 的 `token_usage.model` 是歷史用量欄位，沒有獨立模型設定表；移除可選模型不會刪除歷史 Token 或費用紀錄。

回歸檢查（不寫入 MySQL、不呼叫付費 OpenAI API）：

```powershell
python -m unittest discover -s tests -p test_backend.py -v
```
