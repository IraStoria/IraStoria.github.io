# 投稿後端 API 契約（ADR-008／ADR-009）

恥辱柱（bug 回報）與許願池（wish）共用**一個** Cloudflare Worker＋**一個** KV namespace（binding 名 `POOL`），以 `type` 分流。
前端只認 `site.json` 的 `backend.url`（例：`https://pool.<帳號>.workers.dev`，**不含結尾斜線**）；空字串＝未接線，前端走降級文案。

## 環境變數（Worker → Settings → Variables）
| 名稱 | 類型 | 內容 |
|---|---|---|
| `ALLOWED_ORIGINS` | var | 逗號分隔。正式＝`https://irastoria.github.io`；本機測試可加 `http://127.0.0.1:8766` |
| `OWNER_LOGIN` | var | 站主 GitHub login（`IraStoria`）。只有這個帳號能拿到管理通行證 |
| `SITE_URL` | var | OAuth 完成後導回的站址（`https://irastoria.github.io/zh/`） |
| `GITHUB_CLIENT_ID` | var | GitHub OAuth App 的 Client ID |
| `GITHUB_CLIENT_SECRET` | **secret** | 站主自己貼進 Cloudflare，永不進 repo |
| `TOKEN_SECRET` | **secret** | 任意 32 字以上亂數，簽管理通行證用 |
| `TOTP_SECRET` | **secret**（選用） | base32 字串（`python worker/totp_setup.py` 產生）。**有設＝登入多一道驗證器六位數碼**；沒設＝GitHub 通過即發通行證 |
| `BARK_KEY` | **secret**（選用） | Bark app 的 device key。**有設＝登入成功／被拒、新投稿都推手機通知**；沒設＝完全不推 |
| `BARK_SERVER` | var（選用） | Bark 伺服器，預設 `https://api.day.app`（自架才需要填） |
| `BARK_ON_SUBMIT` | var（選用） | 預設開；填 `"0"` 關掉「新投稿」通知（登入通知不受影響） |
| `MAIL_API_KEY` | **secret**（選用） | 寄信 API 的金鑰（Resend）。**有設（且 `MAIL_FROM` 有設）＝站主放行／改狀態／回覆時寄信給留了 email 的許願者**；沒設＝完全不寄（LOG-165） |
| `MAIL_FROM` | var（選用） | 寄件人，例 `IraStoria <well@example.com>`（Resend 要驗證過的網域） |
| `MAIL_API` | var（選用） | 寄信端點，預設 `https://api.resend.com/emails`（POST `{from,to,subject,text}`＋Bearer；換同形狀的服務才填） |

## 共通規則
- 所有回應 JSON：成功 `{ "ok": true, ... }`；失敗 `{ "ok": false, "error": "<code>" }`，HTTP 4xx/5xx。
- CORS：`Access-Control-Allow-Origin` 只回 `ALLOWED_ORIGINS` 內符合的那個 Origin；`OPTIONS` 預檢 204。寫入端點（POST）Origin 不在名單 → 403 `origin`。GET 不擋 Origin（V11：不當主防線），但有速率限制。
- 速率限制（KV key `rl:<route>:<ip>`，TTL 秒）：`submit` 5 次／10 分；`vote` 30 次／10 分；`wishes` 60 次／分；`mine` 30 次／分；`totp`（`POST /auth/totp`）5 次／10 分。超過 → 429 `rate`。
- 單筆大小：body 上限 32 KB（bug 含軌跡）／wish 8 KB → 413 `size`。
- IP 只存 SHA-256 前 16 hex（`iph`），不存原 IP、不存 UA 全文以外的識別。

## 資料形狀（KV value，JSON）
```
wish:<ts>-<rand>  { id, type:"wish", ts, lang:"zh"|"en", nick(≤24), cat, text(≤600),
                    approved:false, status:"wishing"|"considering"|"building"|"done"|"declined",
                    votes:0, reply:"", replyLang:"", link:"", email:""(≤120,選填,永不公開), iph }
bug:<ts>-<rand>   { id, type:"bug", ts, lang, nick(≤24,可空), text(≤2000), trail:[...](≤200筆,可空),
                    meta:{ shell, ua, vw, vh, ver, page }, read:false, status:"new"|"open"|"watch"|"fixed"|"declined", approved:false, iph }   ← LOG-168 站主判決（待審／在逃／保釋觀察中／已伏法／不受理）；公開名冊仍是 bugs.json 手動編
pub:wishes        { ts, items:[ 公開欄位版 wish ] }   ← 站主每次管理寫入後重建；GET /wishes 直接回這份
pub:bugs          { ts, items:[ { id, ts, lang, nick, text, status } ] }   ← LOG-169 站主「顯示：開啟」的回報；GET /bugs 直接回這份（永不含 trail／meta／iph）
rl:<route>:<ip>   計數（TTL）
v:<id>:<iph>      "1"（TTL 86400）＝這個 IP 今天對這則已 +1
```
`cat` ∈ `transcription | design | code | feature | interactive | other`（六類，前端顯示雙語標籤）。
`link` 存穩定識別：`work:<works.json id>`／`app:<app key>`／`demo:<demos path>`；前端解析不到就只顯示徽章不顯示連結（V13）。

## 公開端點
### `POST /submit`
Body：`{ type:"wish", lang, nick, cat, text, email? }` 或 `{ type:"bug", lang, nick?, text, trail?, meta }`
- 驗證：`type` 二選一；`lang` ∈ zh/en；wish 的 `nick` 必填、`cat` 必在六類、`text` 非空且 ≤ 上限；bug 的 `text` 非空；`trail` 若有必須是陣列 ≤ 200 筆、每筆 ≤ 200 字。錯 → 400 `invalid`。
- wish 的 `email`（LOG-165）選填：trim＋轉小寫、≤120 字、須符合 `^[^\s@]+@[^\s@]+\.[^\s@]+$`，錯 → 400 `invalid`；只存在 KV，`GET /wishes`／`POST /mine` 永不回它。
- 回 `{ ok:true, id }`。wish 一律 `approved:false`、`status:"wishing"`。
- 存入成功後（且 `BARK_KEY` 有設、`BARK_ON_SUBMIT` 不是 `"0"`）推一則 Bark：標題 `許願池 · 新願望`／`恥辱柱 · 新回報`，內文 `<nick 或 匿名>：<text 前 80 字>`，level `active`。推送在背景進行（`ctx.waitUntil`），不影響回應時間，失敗也不影響回應。

### `GET /wishes`
回 `{ ok:true, ts, items:[ { id, ts, lang, nick, cat, text, status, votes, reply, replyLang, link } ] }`——**只含 `approved:true`**，且剔除 `iph`。`Cache-Control: public, max-age=60`。

### `GET /bugs`（LOG-169）
回 `{ ok:true, ts, items:[ { id, ts, lang, nick, text, status } ] }`——只含站主「顯示：開啟」（`approved:true`）的 bug 回報，只有這六個欄位（軌跡、meta、iph 結構上不會出）。`Cache-Control: public, max-age=60`。速率 60 次／分。前端：桌面恥辱柱彈幕與手機名冊把它們排在手編名冊之後。

### `POST /mine`
Body `{ ids:[ …最多 10 個 id ] }`（id 為非空字串 ≤ 64 字）。回 `{ ok:true, states:{ <id>: "pending" | "public" | "gone" } }`——`pending`＝存在但未核准、`public`＝已核准（此刻在 `GET /wishes` 裡）、`gone`＝不存在（被刪除、或本來就沒有；bug 的 id 也算 gone）。除這三個字以外不回任何欄位。用途：投稿者的瀏覽器把自己那份「審核中」副本（`localStorage.wish_mine`）拿來核對，被刪的立刻消失、核准的改由公開卡片接手（LOG-161 追記⑥）。速率 30 次／分；形狀不對 → 400 `invalid`。

### `POST /vote`
Body `{ id }`。該 id 必須存在且 `approved:true`；同 IP 同 id 一天一次（已投 → 200 `{ ok:true, votes, dup:true }` 不加）。回 `{ ok:true, votes }`。投票後重建 `pub:wishes`。

### `GET /unsub?id=<id>&t=<sig>`（LOG-165；信裡的一鍵退訂）
`t` ＝ base64url(HMAC-SHA256(`TOKEN_SECRET`, `"unsub:" + id`))，由 Worker 在寄信時算好放進信裡；無 session、不多寫 KV。對 → 把該 wish 的 `email` 清成空字串（願望本身留著），回 200 一頁純 HTML（依願望語言）；id 形狀不對／簽名不對／找不到 → 400 一頁 HTML。冪等。速率 10 次／10 分。POST → 405。

## 管理端點（ADR-009：GitHub OAuth 驗本人）
### `GET /auth/start`
產生 `state`（HMAC 簽、含時間戳、10 分內有效，不用 KV），302 到 `https://github.com/login/oauth/authorize?client_id=…&state=…&scope=read:user`。

### `GET /auth/callback?code&state`
驗 `state` → 向 GitHub 換 token → `GET https://api.github.com/user` → `login` 必須等於 `OWNER_LOGIN`（不分大小寫）
→ 是，且 **`TOTP_SECRET` 未設**：簽發通行證 `token = base64url(payload).base64url(HMAC-SHA256)`，payload `{ sub:login, exp: now+12h }`，302 到 `SITE_URL + '#wp=' + token`。
→ 是，且 **`TOTP_SECRET` 有設**：**不發通行證**，改簽一張 5 分鐘的 PRE-token（同一套 HMAC，payload `{ sub:login, exp: now+300, pre:1 }`），302 到 `SITE_URL + '#wp2=' + pre`。前端讀到 `#wp2=` 要向使用者要驗證器六位數碼，再呼叫 `POST /auth/totp`。**PRE-token 打任何 `/admin/*` 一律 401**（`verifyToken` 拒絕帶 `pre` 的 payload）。
→ 否：302 到 `SITE_URL + '#wp=denied'`。
前端讀到 `#wp=` 立刻存 `sessionStorage.wp_token` 並清掉 fragment。

Bark（`BARK_KEY` 有設時）：GitHub 說不是站主 → `許願池 · 登入被拒`（內文 `owner · <login> · <國家/城市> · <ISO 時間>`；GitHub 換 token 失敗則 reason `github`）；TOTP 未啟用而直接發通行證 → `許願池 · 登入成功`（`<login> · <國家/城市> · <ISO 時間>`）。國家／城市取自 `request.cf`，缺就 `?`。兩者 level 皆 `timeSensitive`，group `irastoria-pool`。

### `POST /auth/totp`（TOTP 第二關；需 Origin 在名單、速率 5 次／10 分）
Body `{ pre, code }`。
1. 驗 `pre`：HMAC 正確、未過期、payload 有 `pre:1`、`sub` 是 `OWNER_LOGIN` → 否則 401 `{ ok:false, error:"pre" }`（Bark `登入被拒`，reason `pre`）。
2. 驗 `code`：RFC 6238 TOTP（HMAC-SHA1、30 秒一步、6 位數、接受前後各一步），金鑰＝base32 解碼後的 `TOTP_SECRET` → 錯 401 `{ ok:false, error:"code" }`（Bark `登入被拒`，reason `code · <login>`）。
3. 都對 → 簽發正常 12 小時通行證，回 `{ ok:true, token }`（Bark `登入成功`）。前端存 `sessionStorage.wp_token`。
`TOKEN_SECRET`／`TOTP_SECRET` 沒設（或 `TOTP_SECRET` 不是合法 base32）→ 500 `config`。

### 需 `Authorization: Bearer <token>` 的端點（無效／過期／PRE-token → 401 `auth`）
- `GET /admin/list?type=wish|bug` → `{ ok:true, items:[ 全欄位含未審 ] }`（bug 含 trail）。
- `POST /admin/update` Body `{ id, approved?, status?, reply?, replyLang?, link?, read? }` → 只改給的欄位（`status` 依 type 驗證：wish 用五個願望狀態，bug 用 `new|open|watch|fixed|declined`，混用 → 400；bug 的 `approved` 就是收件匣的「顯示」開關）；改完若是 wish 重建 `pub:wishes`，是 bug 重建 `pub:bugs`。回 `{ ok:true, item }`。**寄信（LOG-165）**：wish 有 `email`、且這次改動對許願者算新聞——放行（false→true）／`status` 變了／`reply` 新增或改變——且 `MAIL_API_KEY`＋`MAIL_FROM` 都有設 → 背景寄**一封**純文字信（依願望 `lang`；主旨 `許願池：你的願望有新進展`／`Wishing well: news on your wish`，`done` 時加「（已實現）」／「(granted)」；內文＝暱稱、願望前 80 字、變了什麼、站址、退訂連結）。只改 `link`、取消放行、原值重存、bug 的更新一律不寄；寄信失敗不影響回應。
- `POST /admin/delete` Body `{ id }` → 刪除；wish 則重建 `pub:wishes`。回 `{ ok:true }`。

## 健康檢查
`GET /` 或 `GET /health` → `{ ok:true, service:"pool", ts }`（前端用來判斷通道是否活著；不吃速率限制）。
