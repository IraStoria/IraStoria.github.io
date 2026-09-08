# 投稿後端部署指南（只用 Cloudflare 儀表板＋GitHub 網頁，不用命令列）

這份指南把 `worker/worker.js` 部署成一個 Cloudflare Worker，並接上 KV 與 GitHub 登入。
全程約 15 分鐘。做完後**唯一要回報給 Claude 的東西是 workers.dev 網址**（見最後一節）。

---

## (a) 建立 Worker 並貼上程式碼

1. 到 <https://dash.cloudflare.com> 註冊／登入免費帳號（不需綁信用卡）。
2. 左側選單 **Workers & Pages** → **Create** → **Create Worker**。
3. 名稱填 `pool`（之後網址會是 `https://pool.<你的子網域>.workers.dev`）→ **Deploy**（先部署預設的 Hello World 沒關係）。
4. 部署完成畫面按 **Edit code**（或進 Worker 頁面右上 **Quick edit**）。
5. 把編輯器裡的內容**全部刪掉**，貼上 `worker/worker.js` 的**完整內容**（從第一行 `// © 2026 IraStoria` 到最後一行）。
6. 右上 **Deploy**。此時打開網址會回 `{"ok":false,"error":"config"}`——正常，因為 KV 還沒綁。

## (b) 建立 KV namespace 並綁定到 Worker（binding 名必須是 `POOL`）

1. 左側 **Storage & Databases** → **KV** → **Create a namespace**，名稱填 `pool-kv` → **Add**。
2. 回到 **Workers & Pages** → 點進 `pool` → **Settings** → **Bindings** → **Add** → 選 **KV namespace**。
3. **Variable name** 填 `POOL`（大寫，一字不差）；**KV namespace** 選剛建的 `pool-kv` → **Save**（或 **Deploy**）。

## (c) 設定 Variables（Settings → Variables and Secrets → Add）

| 名稱 | 類型 | 填什麼 |
|---|---|---|
| `ALLOWED_ORIGINS` | Text | `https://irastoria.github.io`（本機測試見下方，用逗號加） |
| `OWNER_LOGIN` | Text | `IraStoria`（你的 GitHub 帳號名） |
| `SITE_URL` | Text | `https://irastoria.github.io/zh/` |
| `GITHUB_CLIENT_ID` | Text | 步驟 (d) 拿到的 Client ID |
| `GITHUB_CLIENT_SECRET` | **Secret**（Type 選 Secret／按 Encrypt） | 步驟 (d) 拿到的 Client secret |
| `TOKEN_SECRET` | **Secret**（Type 選 Secret／按 Encrypt） | 自己亂打 32 字以上的英數字（例如密碼產生器產一組 48 字） |

每加一個都按 **Save**／**Deploy**。Secret 存進去後儀表板不會再顯示內容，這是正常的。
前四個可以先填；後兩個等 (d) 拿到資料再回來填。

## (d) 建立 GitHub OAuth App（讓站主用 GitHub 登入管理後台）

1. GitHub 右上頭像 → **Settings** → 最下方 **Developer settings** → **OAuth Apps** → **New OAuth App**。
2. 填表：
   - **Application name**：`irastoria pool`（隨意）
   - **Homepage URL**：`https://irastoria.github.io`
   - **Authorization callback URL**：`https://<worker 網址>/auth/callback`
     （例：`https://pool.xxxx.workers.dev/auth/callback`，網址在 Worker 頁面右側 **Preview** 旁可複製）
   - **Enable Device Flow** 不要勾。
3. **Register application** → 頁面會顯示 **Client ID**，複製到 Cloudflare 的 `GITHUB_CLIENT_ID`。
4. 同頁按 **Generate a new client secret** → 複製那串（只顯示一次）→ 貼到 Cloudflare 的 `GITHUB_CLIENT_SECRET`（記得選 Secret 型別）。
5. 回 Cloudflare 確認六個變數都在，按 **Deploy**。

## (e) 驗證

1. 瀏覽器打開 `https://<worker 網址>/health`，看到 `{"ok":true,"service":"pool","ts":...}` 就通了。
2. 打開 `https://<worker 網址>/auth/start`，應該跳到 GitHub 授權頁；授權後會被帶回站上，網址結尾帶 `#wp=...`（一長串）就代表登入鏈通了；若看到 `#wp=denied`，檢查 `OWNER_LOGIN` 是否拼對。
3. 若 `/health` 回 `config`：KV 綁定名不是 `POOL`。若 `/auth/start` 回 `config`：`GITHUB_CLIENT_ID` 或 `TOKEN_SECRET` 沒填。

## (f) 回報給 Claude

**只要回報 workers.dev 網址**，例如 `https://pool.xxxx.workers.dev`（結尾不要加斜線）。
Claude 會把它填進 `site.json` 的 `backend.url`。**不要貼任何 Secret、Client secret 或 TOKEN_SECRET。**

---

## 免費額度與注意事項

- **KV 免費方案：每日 1,000 次寫入、100,000 次讀取。** 每筆投稿、每次投票、每次速率限制計數都算一次寫入；正常個人站用量綽綽有餘，但若有人灌水會先撞到這個牆（超額只是當天寫入失敗，不會收費）。
- **Workers 免費方案：每日 100,000 次請求。**
- **workers.dev 網址是公開的**，任何人都打得到，所以 Worker 內建了速率限制（投稿 5 次／10 分鐘、投票 30 次／10 分鐘、讀清單 60 次／分鐘）；即使有人亂打，最多也只是把自己鎖住。
- **Secret 永不進 repo。** `GITHUB_CLIENT_SECRET`、`TOKEN_SECRET` 只存在 Cloudflare；`worker.js` 裡沒有任何金鑰，可以放心放在公開 repo。
- Worker 不記錄、不儲存原始 IP，只存 SHA-256 前 16 碼；投稿人的暱稱與內容只有站主審核通過（`approved`）後才會公開。
- 之後若改了 `worker.js`，重做 (a) 的第 4～6 步（Quick edit 貼上→Deploy）即可，變數與 KV 綁定都會保留。

## 本機測試

本機用 `python -m http.server 8766`（或任何在 `http://127.0.0.1:8766` 的預覽）時，瀏覽器的 Origin 是 `http://127.0.0.1:8766`，
Worker 會擋掉。到 Cloudflare **Settings → Variables** 把 `ALLOWED_ORIGINS` 改成：

```
https://irastoria.github.io,http://127.0.0.1:8766
```

（逗號分隔，不要空格也可以）→ **Deploy**。測試完可以留著，本機位址對外沒有意義。

另外 `worker/test_worker.mjs` 是不需要 Cloudflare 的離線自測：在 repo 根目錄執行 `node worker/test_worker.mjs`，最後一行顯示 `20/20 passed` 即代表 Worker 邏輯正常。
