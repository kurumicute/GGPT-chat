# GGPT 資安更新安裝說明

## 本次修改

- 新註冊帳號限制為 3～32 字元，密碼限制為 8～128 字元。
- 擋下常見帳號、常見密碼、帳密相同與全部相同字元的密碼。
- 密碼使用 Werkzeug scrypt 雜湊；既有 Werkzeug 密碼雜湊仍可登入。
- 註冊、登入、管理員登入、聊天、分享與上傳加入頻率限制。
- 登入失敗採一致訊息及假雜湊比對，降低帳號探測風險。
- 登入成功重新建立 Session，Cookie 設為 HttpOnly、SameSite=Lax，正式站使用 Secure。
- 寫入 API 會拒絕跨網站來源，並加入 CSP、HSTS、nosniff 等安全標頭。
- 伺服器詳細例外只寫入後端 Log，不再回傳資料庫或程式內部資訊。
- 圖片會驗證實際內容與像素量；禁止 SVG、真正的 `.env` 與私鑰類附件。
- 非圖片附件強制下載，避免 HTML/JavaScript 在本站網域直接執行。
- 聊天訊息、附件數量與模型輸出 Token 設置上限，降低濫用與意外費用。

## 安裝

將 `pythonindex` 內檔案覆蓋到你的 Python 後端資料夾，並保留自己的 `.env`、`uploads` 與資料庫。

```powershell
cd pythonindex
python -m pip install -r requirements.txt
python app.py
```

前端請將 `frontend/ChatView.vue` 與 `frontend/AdminView.vue` 放回 `src/views/`。

## 正式環境 `.env`

```dotenv
FLASK_DEBUG=false
SESSION_COOKIE_SECURE=true
TRUST_PROXY_COUNT=1
TRUSTED_HOSTS=chat.kurumicute.com,localhost,127.0.0.1
ALLOWED_ORIGINS=
RATELIMIT_STORAGE_URI=memory://
```

用以下指令產生 Flask 密鑰，將結果放到 `FLASK_SECRET_KEY=` 後面：

```powershell
python -c "import secrets; print(secrets.token_hex(32))"
```

請勿把 `.env`、資料庫密碼、OpenAI API Key 或 Google Client Secret 上傳至 GitHub。

若使用 Gunicorn 多程序或多台後端，`memory://` 的限流計數不會共用，應改成 Redis，例如：

```dotenv
RATELIMIT_STORAGE_URI=redis://127.0.0.1:6379/0
```
