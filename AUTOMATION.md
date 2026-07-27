# 自動化與待辦（給接手的 Claude session 讀）

本檔記錄本 repo 的自動化功能與**尚未完成**的工作，讓新的 Claude Code session 能無縫接續。

---

## 1. 已完成：景點「即時運行狀態」功能（live_status）

`trip.json` 的每個 `days[].items[]` 可加一個 **`live_status`** 物件，模板會在該景點下方渲染一塊狀態框（日期 + 狀態 badge + 明細 + 更新時間 + 官網連結）。

目前已套在 **高千穗峽（貸しボート划船）** 這個點（`date` = `2026-10-16`，`name` 含「高千穗峽」）。

`live_status` 結構：

```json
"live_status": {
  "date_label": "10/16[Fri]",              // M/D[Ddd]，星期用英文縮寫
  "status": "運行開放中",                    // 運行狀態原文
  "updated": "2026-07-27 AM10:59",         // 網站顯示的更新時間
  "detail": "【當日券殘り】…\n※…",           // 多行字串，\n 換行
  "source": "https://takachiho-kanko.info/"
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
- **高千穗峽划船運行狀況**：https://takachiho-kanko.info/
  - 每早約 8 點（JST）更新；含當日運行狀態、當日券殘量、注意事項
  - 用 WebFetch 可正常抓取

### 重新產出頁面
```bash
python3 render.py .        # 讀 ./trip.json → 產出 行程表.html + PWA 檔
```
產出的 HTML/PWA 檔（`行程表.html`、`index.html`、`sw.js`、`manifest.webmanifest`、`icon.svg`）**未納入 git 追蹤**，是可重新 render 的產物，不要 commit。只 commit `trip.json` 與 `template/`。

---

## 2. 待完成：每日自動更新划船狀態的「雲端排程」

**目標**：用 Claude Code 的 scheduled cloud agent（routine），每天自動抓 takachiho-kanko.info → 更新高千穗 `live_status` → `render.py` → commit & push。

**目前卡點**：建立 routine 時 API 回 401 —
> `Connect your GitHub account before saving a routine that uses a GitHub repository.`

→ 使用者需先到 **https://claude.ai/code/routines** 連結 GitHub 帳號（要有本 repo 存取權）。連好後即可建立。

### 使用者已確認的排程規格
| 項目 | 值 |
|---|---|
| 方式 | 雲端排程（scheduled cloud agent / routine） |
| 時間 | 每天 **09:00 JST**（= `00:00 UTC`），**9 月起**、只在 9・10 月執行 |
| Cron | `0 0 * 9,10 *` |
| 自動推送 | **自動 commit + push 到 GitHub main** |
| Repo | `https://github.com/WeiCho/kyushu-2026-joeemma` |
| Model | `claude-sonnet-5` |
| Env | Default（`env_01YQ2yEjugps2w2PgcWJmnek`） |
| 工具 | WebFetch / Bash / Read / Write / Edit / Grep / Glob |

### 接手步驟（GitHub 連好後）
1. 用 `schedule` skill 或直接 `ToolSearch select:RemoteTrigger` 載入 RemoteTrigger 工具。
2. `RemoteTrigger action:create`，body 用上面規格；`events[].data.message.content` 放下方「agent 任務 prompt」。
3. 成功後把 routine 連結（`https://claude.ai/code/routines/{id}`）回報使用者。

### agent 任務 prompt（放進 routine 的 message.content）
> 你是這個九州行程規劃 repo（kyushu-2026-joeemma）的自動更新代理。任務：抓取高千穗峽貸しボート（划船）當日運行狀態，更新到行程檔並 push。
>
> 1. 用 WebFetch 抓 https://takachiho-kanko.info/ ，擷取：更新時間、日期（M/D[Ddd]，星期英文縮寫）、運行狀態原文、當日券殘り枚數（今天與隔天）、注意事項原文。
> 2. 讀取 repo 根目錄 trip.json，在 days[].items 找 name 含「高千穗峽」的 item。
> 3. 用 python3 覆寫該 item 的 live_status（欄位：date_label / status / updated / detail / source，見 AUTOMATION.md）。寫回時 ensure_ascii=False, indent=2，其餘內容不動。
> 4. `python3 render.py .` 重新產出（需 jinja2，未裝先 `pip install -r requirements.txt`）。
> 5. 只 `git add trip.json`（HTML/PWA 產物不 commit）；有 diff 才 commit，訊息「chore: 高千穗峽划船運行狀態每日更新（<date_label> <status>）」，然後 `git push origin main`；無 diff 就不 commit。
> 6. 回報今天的 date_label、status、當日券殘量、是否有 push。
>
> 注意：雲端環境無本機檔案；時間以 JST 為準；抓取失敗別亂猜，保留原狀並回報。

> 備註：routine 建好後，行程結束（10/20）或 10 月底可到 https://claude.ai/code/routines 停用或刪除。

---

## 摘要給接手者
- 功能已上線，資料手動填到 `2026-07-27`。
- **唯一待辦**：使用者連 GitHub → 建立上述 routine。
