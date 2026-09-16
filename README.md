# GGPT 對話平台

GGPT 是一套以前後端分離架構開發的 AI 對話平台，提供多模型切換、推理模式、網路搜尋、檔案與圖片輸入、對話分享、共同對話，以及完整的管理後台。

介面以桌面與行動裝置皆可使用為目標，並針對程式碼顯示、數學公式、響應式排版及前端載入效能進行最佳化。

> 線上網站：[https://chat.kurumicute.com/](https://chat.kurumicute.com/)

## 主要功能
<img width="2164" height="1085" alt="image" src="https://github.com/user-attachments/assets/d6949752-0b8b-485b-8ecc-cc07491c3359" />

### 使用者功能

- 帳號註冊、登入及工作階段管理
- Google 帳號登入
- 建立、重新命名及刪除對話
- 多種 AI 模型切換與推理強度設定
- 選用網路搜尋功能
- 圖片、文字檔與程式碼附件輸入
- 對話內容複製與程式碼下載
- 深色／淺色介面
- 手機與桌面響應式版面

### 協作功能

- 產生不可預測的對話分享連結
- 透過 `/c/:shareToken` 加入共同對話
- 分享者可隨時停止分享
- 共同對話定時同步
- 全域聊天室

### 管理後台
<img width="2167" height="1353" alt="image" src="https://github.com/user-attachments/assets/ab1dfb11-4d72-49e9-93d1-6a94de2b5cf8" />
- 使用者與帳號管理
- 對話、訊息、API 請求及 Token 統計
- 模型使用量與費用分析
- Web Search 使用次數與費用統計
- 訪客、瀏覽量、來源、裝置、瀏覽器及作業系統分析
- 管理員操作紀錄
- CSV 匯出

## 技術架構

| 類別 | 使用技術 |
| --- | --- |
| 前端 | Vue 3、Vite、Vue Router、Pinia |
| 圖表 | Chart.js |
| 內容顯示 | Marked、DOMPurify、KaTeX、Highlight.js |
| 後端 | Python、Flask |
| 資料庫 | MySQL |
| AI 服務 | OpenAI API |
| 第三方登入 | Google Identity Services |
| 圖片處理 | Pillow、WebP |

## 系統需求

開始前請先準備：

- Node.js 20 LTS 或相容版本
- npm
- Python 3.10 以上版本
- MySQL 8.0 或相容版本
- OpenAI API Key
- Git

## 環境變數

在後端專案根目錄建立 `.env`：

```dotenv
# Flask
FLASK_SECRET_KEY=replace_with_a_long_random_value
TRAFFIC_SALT=replace_with_another_random_value

# MySQL
DB_HOST=127.0.0.1
DB_USER=root
DB_PASSWORD=replace_with_your_database_password
DB_NAME=chat_db

# OpenAI
OPENAI_API_KEY=replace_with_your_openai_api_key
OPENAI_MODEL=gpt-5.6-luna
OPENAI_TTS_MODEL=tts-1

# 管理員，格式為 帳號:密碼；多組帳號以逗號分隔
ADMIN_ACCOUNTS=admin:replace_with_a_strong_password

# Google 登入；不使用時可留空
GOOGLE_CLIENT_ID=

# 使用限制與上傳設定
USER_TOKEN_QUOTA=1000000
MAX_UPLOAD_MB=50
IMAGE_MAX_DIMENSION=2048
IMAGE_WEBP_QUALITY=82
GLOBAL_CHAT_MAX_LENGTH=500
GLOBAL_CHAT_HISTORY_LIMIT=50
```


## 安裝與啟動

### 1. 取得專案

```bash
git clone https://github.com/USERNAME/ggpt-chat.git
cd ggpt-chat
```

### 2. 使用 SQL 結構檔建立 MySQL 資料庫

專案已在 database/schema.sql 提供完整資料庫結構，內容包含 chat_db 資料庫及網站所需的資料表、索引與關聯設定。

確認 MySQL 服務已啟動後，在專案根目錄執行：

mysql -u root -p < database\schema.sql

依提示輸入 MySQL 密碼後，系統會自動建立 chat_db 並匯入完整資料表。

匯入完成後可以執行以下指令確認結果：

mysql -u root -p -e "USE chat_db; SHOW TABLES;"

正常情況下應顯示以下資料表：

admin_users
chat
conversation_collaborators
conversation_shares
conversations
global_chat_messages
token_usage
users
visitor_events

database/schema.sql 必須包含 CREATE DATABASE IF NOT EXISTS chat_db 與 USE chat_db，因此不需要另外手動建立資料庫。重複執行時會保留既有資料，不會主動刪除資料表。

### 3. 安裝並啟動後端

進入後端目錄後建立虛擬環境：

```powershell
python -m venv venv
venv\Scripts\Activate.ps1
```

先安裝目前後端使用的主要套件：

```powershell
pip install Flask requests mysql-connector-python Werkzeug opencc-python-reimplemented openai google-auth python-dotenv Pillow
```

啟動 Flask：

```powershell
python app.py
```

預設後端位址：

```text
http://127.0.0.1:8080
```

健康檢查：

```text
http://127.0.0.1:8080/health
```

### 4. 安裝並啟動前端

開啟另一個 PowerShell 視窗並進入前端目錄：

```powershell
npm install
npm run dev
```

預設前端位址：

```text
http://127.0.0.1:5173
```

Vite 開發伺服器會將登入、聊天、上傳、分享、TTS 與管理 API 請求代理到 `http://127.0.0.1:8080`。

## 正式建置

建立前端正式版本：

```powershell
npm run build
```

輸出檔案會位於：

```text
dist/
```

需要分析前端打包大小時，先安裝分析工具：

```powershell
npm install -D rollup-plugin-visualizer
$env:ANALYZE="true"
npm run build
```

分析報告將產生於：

```text
dist/bundle-report.html
```

## 主要路由

| 路徑 | 說明 |
| --- | --- |
| `/` | 使用者登入與主要對話介面 |
| `/c/:shareToken` | 共同對話分享連結 |
| `/admin` | 管理後台 |
| `/health` | 後端健康檢查 |


## 專案狀態

目前專案仍持續開發中，功能與資料庫結構可能隨版本調整。
