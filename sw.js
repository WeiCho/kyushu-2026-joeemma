/* 旅程手冊離線快取（PWA）。以 file:// 開啟時不會註冊，不影響單檔離線用法 */
const CACHE = "trip-e0166385";
const ASSETS = ["./", "./index.html", "./行程表.html", "./manifest.webmanifest", "./icon.svg"];
self.addEventListener("install", (e) => {
  // cache: "reload" 跳過瀏覽器的 HTTP 快取（GitHub Pages 給 10 分鐘），不然新版 SW 可能存到舊頁
  e.waitUntil(caches.open(CACHE)
    .then((c) => c.addAll(ASSETS.map((u) => new Request(u, {cache: "reload"}))))
    .then(() => self.skipWaiting()));
});
self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys()
      .then((ks) => Promise.all(ks.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});
self.addEventListener("fetch", (e) => {
  const req = e.request;
  if (req.method !== "GET") return;
  if (new URL(req.url).origin !== location.origin) return; // 外部連結（地圖等）走網路
  const save = (res) => {
    if (res.ok) { const copy = res.clone(); caches.open(CACHE).then((c) => c.put(req, copy)); }
    return res;
  };
  // 頁面本身網路優先：手冊每天自動更新天氣，快取優先會讓人一直看到前一版；
  // 沒網路（山區、飛機上）才退回快取。圖示等靜態檔維持快取優先
  if (req.mode === "navigate") {
    e.respondWith(fetch(req, {cache: "no-cache"}).then(save)
      .catch(() => caches.match(req).then((hit) => hit || caches.match("./行程表.html"))));
    return;
  }
  e.respondWith(
    caches.match(req).then((hit) => hit || fetch(req).then(save)
      .catch(() => caches.match("./行程表.html")))
  );
});
