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

## 2. 已完成：每日自動更新划船狀態（GitHub Actions）

**做法**：`.github/workflows/takachiho.yml` 每天抓 takachiho-kanko.info → 更新 `trip.json` 的 `live_status` → commit & push → 重新 render 並部署 GitHub Pages。

| 項目 | 值 |
|---|---|
| Workflow | `.github/workflows/takachiho.yml` |
| 腳本 | `scripts/update_takachiho.py`（只用 Python 標準庫） |
| 時間 | 每天 **09:04 JST**（cron `4 0 * 8,9,10 *`，UTC），只在 8・9・10 月執行 |
| 手動執行 | Actions 頁面 → 「高千穗峽划船狀態每日更新」→ Run workflow |
| 產物 | 只 commit `trip.json`；HTML/PWA 由同一個 job 重新 render 後直接部署 Pages |

### 為什麼不是 Claude 雲端排程（routine）
原本建了 routine `trig_01XCaq8oV8KYoLfrvmJWrd2J`，但 2026-08-02 首次試跑發現**雲端環境的 egress proxy 擋掉 takachiho-kanko.info**（CONNECT 收到 403，`connect_rejected`），是網路政策封鎖、不是暫時性錯誤。該 routine 已停用，改用 GitHub Actions。

（另外那次試跑還把 `trip.json` 裡 7/27 的舊值當成「今天抓到的」回報，看起來像抓成功。所以現在腳本一律做日期校驗。）

### 腳本行為
- 解析官網 `<div class="box_boat">` 區塊：更新時間、`M/D[Ddd]`、運行狀態、`<small class="note">` 全文（`<br>` → 換行）。
- **日期校驗**：官網顯示的 M/D ≠ 今天（JST）→ 印訊息、**不寫檔**、離開碼 0（官網每早 8 點左右才更新，偶爾會慢，不算錯誤）。
- **抓取或解析失敗** → 離開碼 1，Actions 亮紅燈，**不會寫入舊值**。
- `detail` 直接存官網日文原文（不再手動翻譯，才能全自動）。
- 內容與現有 `live_status` 完全相同時不寫檔，也就不會產生空 commit。

### 官網改版時要改哪裡
`scripts/update_takachiho.py` 最上面的 `RE_BLOCK` / `RE_DATE` / `RE_STATUS` / `RE_NOTE` 四個正規式。改版時 workflow 會直接失敗並印出是哪一段解析不到。

### 行程結束後
10/20 行程結束或 10 月底，把 `.github/workflows/takachiho.yml` 刪掉或在 Actions 頁面停用即可。

---

## 摘要給接手者
- `live_status` 功能已上線。
- 每日更新改用 GitHub Actions（`takachiho.yml`），2026-08-02 起每天 09:04 JST 自動跑，無待辦。
- Claude 雲端 routine 已停用（該環境連不到來源網站）。
- 行程結束（10/20）或 10 月底可刪掉 workflow。
