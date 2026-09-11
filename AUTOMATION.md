# 自動化與待辦（給接手的 Claude session 讀）

本檔記錄本 repo 的自動化功能與**尚未完成**的工作，讓新的 Claude Code session 能無縫接續。

---

## 1. 已完成：景點「即時運行狀態」功能（live_status）

`trip.json` 的每個 `days[].items[]` 可加一個 **`live_status`** 物件，模板會在該景點下方渲染一塊狀態框（日期 + 狀態 badge + 明細 + 更新時間 + 官網連結）。

目前已套在 **高千穗峽（貸しボート划船）** 這個點（`date` = `2026-10-16`，`name` 含「高千穗峽」）。

`live_status` 結構：

```json
"live_status": {
  "date_label": "9/25[五]",                 // 最新開賣日 M/D[週]
  "status": "満席",                          // 日文原文（中文轉不出來時的退路）
  "updated": "2026-09-11 15:57",            // 抓取時間（JST）
  "detail": "…",                            // 日文多行原文
  "source": "https://eipro.jp/takachiho1/eventCalendars/index",
  "title_zh": "高千穗峽 划船開賣",
  "status_zh": "9/25[五] 全數售完",
  "summary_zh": "16 場 240 艇開賣當天清空 + 我們 10/16 的狀況"
}
```

模板實作在 `template/itinerary.html.j2`：
- CSS：搜尋 `.live-status`
- 渲染區塊：在 item 的 `{% if item.note %}` 之後、`{% set meta = [] %}` 之前

另有通用 **`links`** 欄位（item 名稱旁的 emoji 超連結，如福岡 Airbnb 的「🚃 吉塚駅時刻表」）：
```json
"links": [{"emoji": "🚃", "label": "吉塚駅時刻表", "url": "https://…"}]
```

### 資料來源網站
- **高千穗峽划船預約日曆（官方訂位系統）**：https://eipro.jp/takachiho1/eventCalendars/index
  - 乘船日 **14 天前 09:00 JST** 開放預約、2 天前 09:00 截止
  - 每天 16 個 30 分場次，一場 15～18 艇（假日 15、平日 18，隨月份調整）
  - 2026-09-11 起改抓這裡。原本抓觀光協會官網 https://takachiho-kanko.info/ 的
    「當天運行狀態」，但當天能不能划是到了才知道的事；真正要盯的是**搶票**——
    今天剛開賣的那天多久賣完，決定我們 10/2 要用什麼力道搶

### 重新產出頁面
```bash
python3 render.py .        # 讀 ./trip.json → 產出 行程表.html + PWA 檔
```
產出的 HTML/PWA 檔（`行程表.html`、`index.html`、`sw.js`、`manifest.webmanifest`、`icon.svg`）**未納入 git 追蹤**，是可重新 render 的產物，不要 commit。只 commit `trip.json` 與 `template/`。

---

## 2. 已完成：每日自動更新划船狀態（GitHub Actions）

**做法**：`.github/workflows/takachiho.yml` 每天抓 eipro.jp 預約日曆 → 更新 `trip.json` 的 `live_status` → commit & push → 重新 render 並部署 GitHub Pages。

| 項目 | 值 |
|---|---|
| Workflow | `.github/workflows/takachiho.yml`（同一個 job 也順便更新天氣，見第 3 節） |
| 腳本 | `scripts/update_takachiho.py`（只用 Python 標準庫） |
| 名稱 | 「行程每日更新（划船狀態・天氣預報）」（檔名仍是 takachiho.yml，保留 Actions 歷史） |
| 時間 | 每天 **09:04 JST**（cron `4 0 * 8,9,10 *`，UTC），只在 8・9・10 月執行 |
| 手動執行 | Actions 頁面 → 「高千穗峽划船狀態每日更新」→ Run workflow |
| 產物 | 只 commit `trip.json`；HTML/PWA 由同一個 job 重新 render 後直接部署 Pages |

### 為什麼不是 Claude 雲端排程（routine）
原本建了 routine `trig_01XCaq8oV8KYoLfrvmJWrd2J`，但 2026-08-02 首次試跑發現**雲端環境的 egress proxy 擋掉 takachiho-kanko.info**（CONNECT 收到 403，`connect_rejected`），是網路政策封鎖、不是暫時性錯誤。該 routine 已停用，改用 GitHub Actions。

（另外那次試跑還把 `trip.json` 裡 7/27 的舊值當成「今天抓到的」回報，看起來像抓成功。所以現在腳本一律做日期校驗。）

### 腳本行為
- **抓法**（日曆頁是空殼，資料靠 fullCalendar 事後 AJAX 取得）：
  1. GET `/takachiho1/eventCalendars/index` 拿 session cookie 與頁面裡的 `action_token`
  2. POST `/takachiho1/eventCalendars/search`，帶 `action_token`、日期區間
     （`data[conds][ServiceView][max_session_dateOver]` / `min_session_dateUnder`）
     與 **`X-Requested-With: XMLHttpRequest`**
  - 三個條件少一個一律回 **HTTP 500**（不是 4xx，別誤判成日期帶錯）
- 回來的 JSON 每個場次都有 `is_reserve_started` / `is_reserve_dead` /
  `order_remain_amount` / `max_accept_limit`。
  **判斷「開賣了沒」只能看 `is_reserve_started`**——未開賣的日子整天都是滿容量，
  跟「完全沒賣出」長得一模一樣。`order_remain_amount` 偶爾是 -1（超賣），要夾到 0。
- 查 今天 ~ 今天+16 天，取**已開賣日期裡最遠的那天**當「最新開賣日」（正常＝今天+14；
  09:00 JST 前跑會是 +13，不算錯）。與預期不符只印警告、照樣寫入。
- 小卡兩行：第一行是最新開賣日賣掉多少，第二行是**我們自己的 10/16**——
  還沒開賣就寫 10/2 開賣，開賣後改成報那天剩幾艇 / 已完售 / 已截止。
- **抓取或解析失敗** → 離開碼 1，Actions 亮紅燈，**不會寫入舊值**。
- 內容相同（或只有 `updated` 不同）就不寫檔，也就不會產生空 commit。

### eipro 改版時要改哪裡
`scripts/update_takachiho.py` 的 `fetch_slots()`（請求三件套）與 `day_summary()`
用到的欄位名。改版時 workflow 會直接失敗並印出是哪一步解析不到。

### 行程結束後
10/20 行程結束或 10 月底，把 `.github/workflows/takachiho.yml` 刪掉或在 Actions 頁面停用即可。

---

## 3. 已完成：每日天氣預報（氣象廳 JMA）

行程左欄原本寫死一句「福岡市區 16–24°C，入夜微涼」，現在改抓**氣象廳官方預報**顯示在同一個位置。

| 項目 | 值 |
|---|---|
| 腳本 | `scripts/update_weather.py`（只用 Python 標準庫） |
| 來源 | `https://www.jma.go.jp/bosai/forecast/data/forecast/{府縣code}.json`（無金鑰、無流量限制） |
| 執行 | 併在 `takachiho.yml` 裡，每天 09:04 JST 跟划船狀態一起跑 |
| 寫入欄位 | `days[].weather_live` = `{"text": "福岡 18–24°C・多雲時晴・降雨 30%", "updated": "10/14 17:00", "scope": "週間預報"（只有第 4～7 天才有）}` |

### 每天抓哪一區：`days[].weather_area`
```json
"weather_area": {"office": "400000", "area": "400010", "spot": "82182", "label": "福岡"}
```
- `office` 府縣預報區（福岡 400000／熊本 430000／大分 440000／宮崎 450000）
- `area` 一次細分區（福岡地方 400010、北九州地方 400020、阿蘇地方 430020、大分中部 440010、宮崎北部山沿い 450040…）
- `spot` 溫度觀測點（福岡 82182、八幡 82056、阿蘇乙姫 86111、大分 83216、高千穗 87041…）
- `label` 手冊上顯示的地名，想改文字只改這裡

各縣有哪些區碼，直接開上面那個 JSON 看 `areas[].area`。

### 行為
- 氣象廳只給到 **7 天後**：前 3 天用細分區預報（地區細），第 4～7 天退回府縣層級的週間預報，並在 `weather_live.scope` 標「週間預報」，手冊上會顯示出來，不會讓人誤以為是當地精確數字。
- 行程還在 7 天外（現在就是）→ 印「還在預報範圍外」、**不寫檔**、離開碼 0。樣板自動退回顯示 `days[].weather` 那句靜態描述。
- 抓取失敗 → 離開碼 1。這步在 workflow 裡設了 `continue-on-error`，天氣掛掉不會連帶擋掉划船狀態的更新。
- 天氣代碼→中文對照在腳本最上面的 `TELOP_ZH`；查不到的代碼用百位數退回「晴／多雲／雨／雪」。

### 模板
`template/itinerary.html.j2` 搜 `day-weather`：`weather_live` 有值就顯示預報 + 一行來源（`.wx-src`），沒有就顯示 `weather`。

---

## 摘要給接手者
- `live_status` 功能已上線。
- 每日更新改用 GitHub Actions（`takachiho.yml`），2026-08-02 起每天 09:04 JST 自動跑，無待辦。
- 同一個 workflow 也更新天氣預報（`scripts/update_weather.py`）；行程進入 10/8 之後才會真的抓到值，在那之前顯示 trip.json 原本的靜態氣候描述。
- Claude 雲端 routine 已停用（該環境連不到來源網站）。
- 行程結束（10/20）或 10 月底可刪掉 workflow。
