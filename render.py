#!/usr/bin/env python3
"""trip.json + template/itinerary.html.j2 → 行程表.html

用法：python render.py trips/<旅程名>
"""
import hashlib
import json
import re
import sys
from datetime import date
from pathlib import Path
from urllib.parse import quote
from xml.sax.saxutils import escape as xml_escape

from jinja2 import Environment, FileSystemLoader, select_autoescape

ROOT = Path(__file__).parent
TEMPLATE_DIR = ROOT / "template"
TEMPLATE_NAME = "itinerary.html.j2"
OUTPUT_NAME = "行程表.html"
# PWA 附屬檔（放到 HTTPS 空間時可安裝、離線；單檔 file:// 用法不受影響）
SW_NAME = "sw.js"
MANIFEST_NAME = "manifest.webmanifest"
ICON_NAME = "icon.svg"
INDEX_NAME = "index.html"

INDEX_HTML = """<!doctype html>
<html lang="zh-Hant">
<head>
<meta charset="utf-8">
<meta http-equiv="refresh" content="0; url=%(html)s">
<link rel="canonical" href="%(html)s">
<title>旅程手冊</title>
<script>location.replace("%(html)s");</script>
</head>
<body>前往 <a href="%(html)s">旅程手冊</a>…</body>
</html>
"""

SW_JS = """/* 旅程手冊離線快取（PWA）。以 file:// 開啟時不會註冊，不影響單檔離線用法 */
const CACHE = "trip-%(version)s";
const ASSETS = ["./", "./%(index)s", "./%(html)s", "./%(manifest)s", "./%(icon)s"];
self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(ASSETS)).then(() => self.skipWaiting()));
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
  e.respondWith(
    caches.match(req).then((hit) => hit || fetch(req).then((res) => {
      const copy = res.clone();
      caches.open(CACHE).then((c) => c.put(req, copy));
      return res;
    }).catch(() => caches.match("./%(html)s")))
  );
});
"""

ITEM_TYPES = ("景點", "餐廳", "交通", "住宿", "購物")
TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
WEEKDAYS = ("週一", "週二", "週三", "週四", "週五", "週六", "週日")

# 花費分類：items 的 type 與 bookings 的 type 各自對映到統一的花費類別
CATEGORY_OF_ITEM = {"景點": "景點", "餐廳": "餐飲", "交通": "交通", "住宿": "住宿", "購物": "購物"}
CATEGORY_OF_BOOKING = {
    "航班": "交通", "飛機": "交通", "機票": "交通",
    "飯店": "住宿", "旅館": "住宿", "民宿": "住宿", "住宿": "住宿",
    "餐廳": "餐飲",
}
# 待確認偵測：名稱或備註命中這些字樣視為尚未定案
PENDING_RE = re.compile(r"待補|待確認|待報價|待補充|尚未|未定|TBD", re.IGNORECASE)
# 本日路線一鍵導航：Google Maps 消費者版路線點數上限（origin + waypoints + destination）
MAX_ROUTE_POINTS = 10


class TripError(ValueError):
    """trip.json 內容不合法。訊息需指出是哪裡、哪一筆。"""


def _require(obj, key, ctx):
    if key not in obj or obj[key] in (None, ""):
        raise TripError(f"{ctx} 缺少必填欄位「{key}」")
    return obj[key]


def _parse_date(value, ctx):
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        raise TripError(f"{ctx} 日期格式錯誤（應為 YYYY-MM-DD）：{value!r}") from None


def _check_time(value, ctx):
    if not TIME_RE.match(str(value)):
        raise TripError(f"{ctx} 時間格式錯誤（應為 24 小時制 HH:MM）：{value!r}")
    return str(value)


def _check_cost(obj, ctx):
    cost = obj.get("cost", 0)
    if not isinstance(cost, (int, float)) or isinstance(cost, bool) or cost < 0:
        raise TripError(f"{ctx} 的 cost 必須是 ≥ 0 的數字：{cost!r}")
    return cost


def validate(data):
    """驗證 trip.json 結構，錯誤丟 TripError。"""
    if not isinstance(data, dict):
        raise TripError("trip.json 頂層必須是物件")

    trip = _require(data, "trip", "trip.json")
    for key in ("title", "destination", "currency"):
        _require(trip, key, "trip")
    if "budget" not in trip:
        raise TripError("trip 缺少必填欄位「budget」")
    _check_cost({"cost": trip["budget"]}, "trip.budget")
    start = _parse_date(_require(trip, "start_date", "trip"), "trip.start_date")
    end = _parse_date(_require(trip, "end_date", "trip"), "trip.end_date")
    if end < start:
        raise TripError(f"end_date（{end}）不得早於 start_date（{start}）")

    days = _require(data, "days", "trip.json")
    if not isinstance(days, list) or not days:
        raise TripError("days 必須至少包含一天")

    prev_date = None
    for d_idx, day in enumerate(days, 1):
        ctx = f"days 第 {d_idx} 天"
        d = _parse_date(_require(day, "date", ctx), f"{ctx} 的 date")
        if d < start or d > end:
            raise TripError(f"{ctx} 的日期 {d} 不在旅程區間（{start} ~ {end}）內")
        if prev_date is not None:
            if d == prev_date:
                raise TripError(f"{ctx} 的日期 {d} 與前一天重複")
            if d < prev_date:
                raise TripError(
                    f"{ctx} 的日期 {d} 早於前一天（{prev_date}），days 需依日期遞增排列"
                )
        prev_date = d
        items = day.get("items")
        if not isinstance(items, list):
            raise TripError(f"{ctx} 缺少 items 陣列")
        prev_time, prev_name = None, None
        for item in items:
            name = _require(item, "name", f"{ctx} 的某筆行程")
            ictx = f"{ctx} 的「{name}」"
            t = _check_time(_require(item, "time", ictx), ictx)
            itype = _require(item, "type", ictx)
            if itype not in ITEM_TYPES:
                raise TripError(f"{ictx} 的 type 必須是 {'/'.join(ITEM_TYPES)}：{itype!r}")
            _check_cost(item, ictx)
            if prev_time is not None and t < prev_time:
                raise TripError(
                    f"{ctx} 行程未依時間排序：「{name}」（{t}）排在「{prev_name}」（{prev_time}）之後"
                )
            prev_time, prev_name = t, name

    for b_idx, booking in enumerate(data.get("bookings") or [], 1):
        ctx = f"bookings 第 {b_idx} 筆"
        _require(booking, "type", ctx)
        _require(booking, "name", ctx)
        if booking.get("date"):
            _parse_date(booking["date"], f"{ctx} 的 date")
        if booking.get("time"):
            _check_time(booking["time"], ctx)
        _check_cost(booking, ctx)

    notes = data.get("notes") or []
    if not isinstance(notes, list):
        raise TripError("notes 必須是字串陣列")

    alerts = data.get("alerts") or []
    if not isinstance(alerts, list):
        raise TripError("alerts 必須是字串陣列")

    highlights = data.get("highlights") or []
    if not isinstance(highlights, list):
        raise TripError("highlights 必須是陣列")
    for h_idx, h in enumerate(highlights, 1):
        _require(h, "title", f"highlights 第 {h_idx} 項")


def make_icon_svg(trip):
    """單色松綠圓角方塊 + 襯線首字，作為 PWA / 主畫面圖示。"""
    label = trip.get("hero_word") or trip.get("title") or "旅"
    glyph = xml_escape(label[0])
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" width="512" height="512" viewBox="0 0 512 512">'
        '<rect width="512" height="512" rx="112" fill="#315b49"/>'
        '<text x="50%" y="54%" text-anchor="middle" dominant-baseline="central" '
        'font-family="Noto Serif TC,Georgia,serif" font-size="300" font-weight="700" '
        f'fill="#f7f3ea">{glyph}</text></svg>'
    )


def make_manifest(trip):
    return {
        "name": trip["title"],
        "short_name": (trip.get("hero_word") or trip["title"])[:12],
        "start_url": "./" + OUTPUT_NAME,
        "scope": "./",
        "display": "standalone",
        "background_color": "#f7f3ea",
        "theme_color": "#f7f3ea",
        "icons": [
            {"src": ICON_NAME, "sizes": "any", "type": "image/svg+xml", "purpose": "any maskable"}
        ],
    }


def map_url(query):
    return "https://www.google.com/maps/search/?api=1&query=" + quote(str(query))


def dir_url(query):
    return "https://www.google.com/maps/dir/?api=1&destination=" + quote(str(query))


def build_route_url(queries):
    """把當日各站的 map_query 串成 Google Maps 多點路線連結。

    回傳 (url, capped)：
    - 0 站 → (None, False)
    - 1 站 → 退化為單點搜尋連結
    - 多站 → dir 路線；超過上限時只取前段＋終點並回報 capped=True（不靜默截斷）
    """
    qs = [q for q in queries if q]
    if not qs:
        return None, False
    if len(qs) == 1:
        return map_url(qs[0]), False
    capped = False
    if len(qs) > MAX_ROUTE_POINTS:
        qs = qs[:MAX_ROUTE_POINTS - 1] + [qs[-1]]
        capped = True
    origin, destination, waypoints = qs[0], qs[-1], qs[1:-1]
    url = (
        "https://www.google.com/maps/dir/?api=1"
        "&origin=" + quote(str(origin))
        + "&destination=" + quote(str(destination))
    )
    if waypoints:
        url += "&waypoints=" + quote("|".join(str(w) for w in waypoints))
    return url, capped


def make_fx_alt(fx):
    """回傳一個 fx_alt(v) 函式：有匯率且金額非零時輸出「 ≈ ¥12,345」，否則空字串。"""
    def fx_alt(v):
        if not fx or not fx.get("rate") or not v:
            return ""
        alt = float(v) * fx["rate"]
        return f" ≈ {fx.get('currency', '')}{alt:,.0f}"
    return fx_alt


def _norm(s):
    """名稱正規化：去掉所有空白，供疑似重複比對。"""
    return re.sub(r"\s+", "", str(s or ""))


def _pending_reason(obj, is_booking=False):
    """判斷單筆 item/booking 是否待確認，回傳原因字串或 None。

    明確 status=confirmed 會抑制所有自動旗標（使用者已人工確認）。
    """
    status = obj.get("status")
    if status == "confirmed":
        return None
    if status == "pending":
        return "標記為待確認"
    text = f"{obj.get('name', '')} {obj.get('note', '')}"
    if PENDING_RE.search(text):
        return "內容含待確認字樣"
    if is_booking and obj.get("cost", 0) and not obj.get("code"):
        return "有金額但缺確認碼"
    return None


def enrich(data):
    """加上衍生欄位，回傳模板 context。不修改原始 data。"""
    trip = dict(data["trip"])
    trip.setdefault("travelers", 1)
    start = _parse_date(trip["start_date"], "start_date")
    end = _parse_date(trip["end_date"], "end_date")
    trip["num_days"] = (end - start).days + 1
    end_label = f"{end.month:02d}.{end.day:02d}"
    if end.year != start.year:
        end_label = f"{end.year}.{end_label}"
    trip["range_label"] = f"{start.year}.{start.month:02d}.{start.day:02d} – {end_label}"
    if not trip.get("hero_word"):
        # hero 浮水印：取目的地最後一段（「日本・東京」→「東京」），最多 4 字
        segment = re.split(r"[・·,，/\s]+", str(trip["destination"]).strip())[-1]
        trip["hero_word"] = segment[:4] or str(trip["title"])[:4]

    days = []
    spent = 0
    by_cat = {}          # 花費類別 → 金額
    paid_items = []      # (名稱, 金額) 供疑似重複比對
    pending = []         # 待確認清單
    for day in data["days"]:
        d = _parse_date(day["date"], "date")
        items = []
        subtotal = 0
        day_no = len(days) + 1
        for raw in day["items"]:
            item = dict(raw)
            item.setdefault("cost", 0)
            subtotal += item["cost"]
            if item["cost"]:
                cat = CATEGORY_OF_ITEM.get(item["type"], "其他")
                by_cat[cat] = by_cat.get(cat, 0) + item["cost"]
                paid_items.append((item["name"], item["cost"]))
            if item.get("map_query"):
                item["map_url"] = map_url(item["map_query"])
            reason = _pending_reason(item)
            if reason:
                pending.append({
                    "where": f"Day {day_no}「{item['name']}」",
                    "reason": reason,
                    "anchor": f"day-{day_no}",
                })
            items.append(item)
        # 交通銜接列連到「下一站」的導航
        for item, nxt in zip(items, items[1:]):
            if item.get("next_transit") and nxt.get("map_query"):
                item["transit_url"] = dir_url(nxt["map_query"])
        route_url, route_capped = build_route_url(
            [it.get("map_query") for it in items]
        )
        spent += subtotal
        days.append({
            **day,
            "items": items,
            "weekday": WEEKDAYS[d.weekday()],
            "date_label": f"{d.month}月{d.day}日",
            "subtotal": subtotal,
            "day_no": day_no,
            "route_url": route_url,
            "route_capped": route_capped,
        })

    bookings = []
    for raw in data.get("bookings") or []:
        booking = dict(raw)
        booking.setdefault("cost", 0)
        spent += booking["cost"]
        if booking["cost"]:
            cat = CATEGORY_OF_BOOKING.get(booking["type"], "其他")
            by_cat[cat] = by_cat.get(cat, 0) + booking["cost"]
        reason = _pending_reason(booking, is_booking=True)
        if reason:
            pending.append({
                "where": f"訂位「{booking['name']}」",
                "reason": reason,
                "anchor": "bookings",
            })
        # 疑似重複：與某行程項目名稱相近且金額相同
        if booking["cost"] and booking.get("status") != "confirmed":
            nb = _norm(booking["name"])
            for iname, icost in paid_items:
                ni = _norm(iname)
                if icost == booking["cost"] and (nb in ni or ni in nb):
                    pending.append({
                        "where": f"訂位「{booking['name']}」",
                        "reason": f"與行程項目「{iname}」金額相同、名稱相近，疑似重複計價",
                        "anchor": "bookings",
                    })
                    break
        bookings.append(booking)

    budget = trip["budget"]
    travelers = trip["travelers"] or 1
    by_category = sorted(
        ({"label": k, "amount": v} for k, v in by_cat.items() if v),
        key=lambda x: -x["amount"],
    )
    return {
        "trip": trip,
        "days": days,
        "bookings": bookings,
        "notes": data.get("notes") or [],
        "alerts": data.get("alerts") or [],
        "highlights": data.get("highlights") or [],
        "pending": pending,
        "totals": {
            "spent": spent,
            "budget": budget,
            "over": spent > budget,
            "has_costs": spent > 0,
            "per_person": round(spent / travelers),
            "by_category": by_category,
        },
    }


def render(trip_dir):
    """讀取 trip_dir/trip.json，渲染並原子性寫出 行程表.html。回傳輸出路徑。"""
    trip_dir = Path(trip_dir)
    src = trip_dir / "trip.json"
    if not src.is_file():
        raise TripError(f"找不到 {src}")
    try:
        data = json.loads(src.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise TripError(f"{src} 不是合法 JSON：{e}") from None

    validate(data)
    context = enrich(data)
    # 供頁內編輯器讀取的原始資料（跳脫 < 以免 </script> 或 <!-- 破壞 script 區塊）
    context["data_json"] = json.dumps(data, ensure_ascii=False).replace("<", "\\u003c")

    env = Environment(
        loader=FileSystemLoader(TEMPLATE_DIR),
        autoescape=select_autoescape(["html", "j2"]),
    )
    env.globals["fx_alt"] = make_fx_alt(context["trip"].get("fx"))
    html = env.get_template(TEMPLATE_NAME).render(**context)

    out = trip_dir / OUTPUT_NAME
    tmp = out.with_suffix(".html.tmp")
    tmp.write_text(html, encoding="utf-8")
    tmp.replace(out)

    # PWA 附屬檔：內容變動時 cache 版本跟著變，強制更新
    version = hashlib.md5(html.encode("utf-8")).hexdigest()[:8]
    (trip_dir / ICON_NAME).write_text(make_icon_svg(context["trip"]), encoding="utf-8")
    (trip_dir / MANIFEST_NAME).write_text(
        json.dumps(make_manifest(context["trip"]), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (trip_dir / INDEX_NAME).write_text(INDEX_HTML % {"html": OUTPUT_NAME}, encoding="utf-8")
    (trip_dir / SW_NAME).write_text(
        SW_JS % {"version": version, "index": INDEX_NAME, "html": OUTPUT_NAME,
                 "manifest": MANIFEST_NAME, "icon": ICON_NAME},
        encoding="utf-8",
    )
    return out


def main(argv):
    if len(argv) != 2:
        print("用法：python render.py trips/<旅程名>")
        return 2
    try:
        out = render(argv[1])
    except TripError as e:
        print(f"渲染失敗：{e}")
        return 1
    print(f"已輸出：{out}")
    return 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except AttributeError:
        pass
    sys.exit(main(sys.argv))
