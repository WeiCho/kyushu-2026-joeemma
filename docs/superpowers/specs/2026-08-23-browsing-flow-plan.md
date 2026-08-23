# 實作計畫：旅程手冊 UI 銜接優化

設計文件：`2026-08-23-browsing-flow-design.md`
唯一需要編輯的檔案：`template/itinerary.html.j2`

## 量測基線（改動前，供比對）

以 100px 為間隔掃過全頁，量測畫面被有內容元素覆蓋的比例（1707×781）：
scrollY 300 = 17.3%、400 = 25.8%、600–900 = 35.7%、中位數 = 54.7%。
手機 390×844：6 段空白，最大 296px。
390px 下 `documentElement.scrollWidth = 372`（無橫向溢出）。
主控台：兩種載入皆零訊息。

量測方法必須與上述一致（有效不透明度 ≥0.15 才算有內容），否則數字不可比。

## 階段

每個階段結束都要 `python render.py .` 重新產生 `行程表.html`，並在 `http://localhost:8777` 上驗證。

### 階段 0：環境

1. 確認 `python render.py .` 可執行（需要 `jinja2`）。
2. 背景啟動 `python -m http.server 8777`，記下停止方式，最後收尾時停掉。

### 階段 1：過場機制（設計 1.1–1.3）

1. **先解耦緊急區漸層**：新增 `--em-fade: 170px`，`:367` 的 `calc(var(--sec) * .85)` 改用它。必須在改 `--sec` 之前做，否則中間狀態會看到硬邊。
2. `:64` `--sec` 改為 `clamp(72px,11vh,140px)`。
3. `:1437` 淡入算式的 `0.35` 位移項改為 `0.08`（除數 `0.35` 不動）。
4. `.section`（`:327`）加上由 `--fade` 驅動的 `translateY`，起點 10px，使用 `--ease`。已確認 `.section` 內沒有 `position:fixed`／`sticky` 的後代（三個 fixed 元素是頂列、封面浮卡、今日浮條，皆在 `.section` 之外；`.ed-bar` 的 sticky 在 `#editor`，也是 body 直屬子層），因此 transform 建立包含區塊不會影響它們。

**驗收**：重掃覆蓋率，取得實際數字（不預設門檻），連同基線回報給使用者判定。
**風險**：`:367` 解耦後仍要目視確認緊急區上緣的漸層交接沒有出現硬邊。

### 階段 2：敘事感（設計 2.1–2.4）

順序建議由影響最小的先做，方便逐步驗證：

1. **2.3 行程說明行** — 在 `:858-860` 的 `.section-heading` 內補一個 `<p>`。最小改動，先做確認樣式繼承正確。
2. **2.2 頁尾收束** — 加在 `band-e` **之內**、`#packing` 之後，文案取 `trip.range_label` 與 `trip.title`。
3. **2.1 銜接引子** — **只有兩處**：行程區 `.daybar` 之後、花費區結尾。緊急→行程那一處刻意不放（那一跳沒有邏輯關係，由深→淺色彩轉場撐住）。因此不需要深色變體樣式。兩處共用一個 Jinja macro。
**驗收**：五個 hash 跳轉落點仍為 `top = 88px` 且落地後 `--fade` 為 1；引子可點且跳對區（只有兩處，緊急區結尾不得出現引子）；頁尾收束在 `band-e` 帶內、列印時保留。

### 階段 3：手機缺陷（設計 3.1–3.5）

1. **3.1** `.sticky-nav a` 垂直 padding 撐到 44px 高。注意 `:184-188` 的 `@media (width<=760px)` 分支也要一起改。
2. **3.5** `:1191` 除了寫 `body.paddingBottom`，同時把 `bar.offsetHeight` 寫成 `--nowbar-h`；`.js .card-deck` 的 `bottom` 改為包含它。`:1194` 清除時要一併清掉。
3. **3.3** `:376` 的 `rgba(255,255,255,.72)` 提高至實測對 `#6b5c46` 達 4.5:1。
4. **3.4** `:489` `.stop.past`、`:406` `.arw:disabled`、浮卡未選頁碼點，三者依設計的標準調整；`.stop.past` 改用時間細線刪除號傳達語意，不再只靠透明度。**刪除號要加進 `:701` 的列印隱藏**（該處現行寫死 `.stop.past{opacity:1}`）。
5. **3.2** 補 skip link，目的地 `#itinerary`，外觀沿用既有 `:focus-visible`（3px `--focus` 外框、4px 圓角、`outline-offset: 3px`），平常隱藏、取得焦點時出現在頂列左側空白處，不引入新視覺。浮卡非最上層卡片內的連結移出 Tab 序（`layout()` 內設定 `aria-hidden` 的同一處一併處理）。

**驗收**：逐項實測數值（不目視認定）；Tab 第一站為 skip link；今日模式下浮條不再覆蓋浮卡。

### 階段 4：回歸驗證

1. 主控台：`行程表.html` 與 `?today=2026-10-16T14:30` 皆零錯誤零警告。
2. 390px 無橫向溢出。
3. 換日三路徑：直接按箭頭、觸碰浮卡後按箭頭、今日模式載入後按一次箭頭。
4. 停用 JavaScript：六天全顯示、浮卡回文流、新增元素不破版。
5. 列印預覽：六天全展開、提醒展開、深色區去墨、進度線與引子不印出。
6. `prefers-reduced-motion: reduce`：無位移、無殘留偏移。
7. 深色模式與大字模式下所有新增元素正常。
8. 執行 `node C:\Users\joy58\.claude\skills\impeccable\scripts\detect.mjs --json 行程表.html`（注意：該安裝缺少 `htmlparser2` 等相依套件，會退化為正規式比對並自陳為低估；瀏覽器內偵測器才是完整路徑）。

### 階段 5：收尾

1. 停掉 8777 的靜態伺服器。
2. 產物（`行程表.html`、`index.html`、`sw.js`、`manifest.webmanifest`、`icon.svg`）不進 git。
3. commit：`template/itinerary.html.j2` 與 `docs/superpowers/specs/` 兩份文件，一次提交（含先前已修好的 `deckCur`）。

## 已知會拖慢驗證的環境限制

- `resize_window` 在這台 Chrome 無效（視窗維持最大化）。手機尺寸需用同源 iframe 量測。
- 分頁在背景時 `document.hidden` 為 true，`smooth` 捲動與 `rAF` 不執行；量測前必須把分頁移到前景，或暫時設 `scrollBehavior = 'auto'`。曾有兩筆讀數（浮卡 `opacity:0`、四個 section `--fade:0`）就是背景分頁造成的假象。
- `browser_batch` 一次含 3 張以上截圖會逾時，控制在 2 張以內。
