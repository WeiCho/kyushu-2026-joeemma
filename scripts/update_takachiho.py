#!/usr/bin/env python3
"""抓高千穗峽貸しボート「最新開賣日」的預約狀況，寫進 trip.json 的 live_status。

資料來源改成官方預約系統 eipro.jp 的日曆（原本抓觀光協會官網的「當日運行狀態」）。
原因：對這趟行程來說，當天能不能划船是到了才知道的事，真正要盯的是**搶票**——
乘船日 14 天前 09:00 JST 開放預約，開賣當天多久賣完，決定我們 10/2 要用什麼力道搶。

所以這支腳本看的是「今天剛開賣的那一天」（≈ 今天 +14 天）賣掉多少，
另外若我們自己的乘船日已進入可預約區間，順便報那天還剩幾艇。

用法：python scripts/update_takachiho.py [trip.json 路徑]

離開碼：
  0  已更新，或內容無變化
  1  抓取或解析失敗（讓 GitHub Actions 顯示紅燈）

抓取失敗時一律不動 trip.json，也不沿用舊值。
"""
import http.cookiejar
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

CALENDAR = "https://eipro.jp/takachiho1/eventCalendars/index"
SEARCH = "https://eipro.jp/takachiho1/eventCalendars/search"
ITEM_KEYWORD = "高千穗峽"
SERVICE_KEYWORD = "ボート"   # 同一個日曆日後若掛上別的服務，只取划船那個
RELEASE_DAYS = 14            # 乘船日的幾天前開賣（09:00 JST）
LOOKAHEAD = 16               # 往後查幾天：要蓋過開賣日（+14）並留一點餘裕
JST = timezone(timedelta(hours=9))
UA = "Mozilla/5.0 (compatible; kyushu-handbook-bot/1.0; +https://github.com/WeiCho/kyushu-2026-joeemma)"
TIMEOUT = 30
RETRIES = 3
RETRY_WAIT = 10   # 秒，逐次遞增（10、20）

RE_TOKEN = re.compile(r'name="action_token"[^>]*value="([^"]+)"')
WEEK_ZH = "一二三四五六日"

TITLE_ZH = "高千穗峽 划船開賣"   # 小卡一行放得下的長度


def fail(msg):
    print(f"❌ {msg}", file=sys.stderr)
    sys.exit(1)


def _open(opener, req):
    """送出一個 request，失敗重試幾次（GitHub runner 連日本主機偶爾逾時）。"""
    last = None
    for attempt in range(1, RETRIES + 1):
        try:
            with opener.open(req, timeout=TIMEOUT) as resp:
                return resp.read().decode("utf-8", "replace")
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last = exc
            if attempt < RETRIES:
                wait = RETRY_WAIT * attempt
                print(f"⚠ 第 {attempt} 次抓取失敗（{exc}），{wait} 秒後重試", file=sys.stderr)
                time.sleep(wait)
    raise last


def fetch_slots(start, end):
    """抓 [start, end] 區間所有場次（每場 30 分、每天 16 場）。

    日曆本身是空殼，資料靠 fullCalendar 事後 POST /eventCalendars/search 取得。
    這個 POST 有三個必要條件，少一個就回 HTTP 500（不是 4xx，別被誤導）：
      1. 先 GET 日曆頁拿 session cookie
      2. 帶上頁面裡的 action_token（CakePHP 的一次性表單 token）
      3. X-Requested-With: XMLHttpRequest
    """
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))

    page = _open(opener, urllib.request.Request(CALENDAR, headers={"User-Agent": UA}))
    m = RE_TOKEN.search(page)
    if not m:
        fail("日曆頁抓不到 action_token，eipro 可能改版了")
    token = m.group(1)

    body = urllib.parse.urlencode({
        "action_token": token,
        "root_action": "index",
        "data[conds][ServiceView][max_session_dateOver]": start.isoformat(),
        "data[conds][ServiceView][min_session_dateUnder]": end.isoformat(),
        "calendar_view_name": "agendaWeek",
        "calendar_type": "week",
    }).encode()
    req = urllib.request.Request(SEARCH, data=body, headers={
        "User-Agent": UA,
        "X-Requested-With": "XMLHttpRequest",
        "Referer": CALENDAR,
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    })
    raw = _open(opener, req)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        fail("search 回的不是 JSON（多半是 HTTP 500 錯誤頁），eipro 可能改版或擋了這次請求")

    results = data.get("results")
    if not isinstance(results, list) or not results:
        fail("search 回來沒有任何場次，eipro 可能改版了")

    boat = [r for r in results if SERVICE_KEYWORD in (r.get("service_name") or "")]
    return boat or results


def day_summary(slots):
    """同一天的所有場次 → 摘要。"""
    # 已被訂走 / 被卡掉的都算不能賣；官方偶爾會出現 -1（超賣），夾到 0
    remain = sum(max(0, int(s.get("order_remain_amount") or 0)) for s in slots)
    cap = max(int(s.get("max_accept_limit") or 0) for s in slots)
    open_times = sorted(
        s["service_start_datetime"][11:16]
        for s in slots
        if max(0, int(s.get("order_remain_amount") or 0)) > 0
    )
    return {
        "slots": len(slots),
        "cap": cap,
        "total": len(slots) * cap,
        "remain": remain,
        "open_times": open_times,
        # 未開賣的日子整天都是滿容量、看起來跟「完全沒賣出」一模一樣，
        # 所以判斷「開賣了沒」只能看 is_reserve_started，不能看 remain
        "started": any(s.get("is_reserve_started") for s in slots),
        "dead": all(s.get("is_reserve_dead") for s in slots),
    }


def label(d):
    return f"{d.month}/{d.day}[{WEEK_ZH[d.weekday()]}]"


def parse(slots, today, our_date):
    by_date = {}
    for s in slots:
        try:
            d = datetime.strptime(s["service_date"], "%Y/%m/%d").date()
        except (KeyError, ValueError):
            continue
        by_date.setdefault(d, []).append(s)
    if not by_date:
        fail("場次裡解析不到日期（service_date），eipro 可能改版了")

    summaries = {d: day_summary(v) for d, v in by_date.items()}

    # 最新開賣日＝已開放預約的日期裡最遠的那天。正常情況等於今天 +14，
    # 但 09:00 JST 前跑就會是 +13——那不是錯誤，是當下真的還沒開。
    opened = [d for d, s in summaries.items() if s["started"]]
    if not opened:
        fail("查不到任何已開賣的日期，eipro 可能改版了")
    latest = max(opened)
    if latest < today:
        fail(f"最新開賣日 {latest} 比今天（JST）還早，資料不合理")
    expected = today + timedelta(days=RELEASE_DAYS)
    if latest not in (expected, expected - timedelta(days=1)):
        print(f"⚠ 最新開賣日是 {latest}，與預期的 {expected} 不符（開賣規則可能變了）",
              file=sys.stderr)

    s = summaries[latest]
    sold = s["total"] - s["remain"]

    if s["remain"] == 0:
        status = "満席"
        status_zh = f"{label(latest)} 全數售完"
        line1 = f"{s['slots']} 場 {s['total']} 艇開賣當天清空"
    else:
        status = f"残り{s['remain']}艇"
        status_zh = f"{label(latest)} 尚有 {s['remain']} 艇"
        times = "・".join(t.lstrip("0") for t in s["open_times"][:3])
        more = "…" if len(s["open_times"]) > 3 else ""
        line1 = f"賣掉 {sold}/{s['total']} 艇，還有 {times}{more}"

    # 第二行：我們自己那天。進到可預約區間就報實況，還沒開就報什麼時候開賣
    ours = summaries.get(our_date)
    release = our_date - timedelta(days=RELEASE_DAYS)
    if ours and ours["started"]:
        if ours["dead"]:
            line2 = f"我們 {our_date.month}/{our_date.day} 已截止預約"
        elif ours["remain"] == 0:
            line2 = f"我們 {our_date.month}/{our_date.day} 已完售"
        else:
            line2 = f"我們 {our_date.month}/{our_date.day} 剩 {ours['remain']} 艇，快訂"
    else:
        line2 = (f"我們 {our_date.month}/{our_date.day}："
                 f"{release.month}/{release.day} 09:00 JST 開賣")

    detail = "\n".join([
        f"{latest.isoformat()} 分の予約が開始済み（乗船日の{RELEASE_DAYS}日前 09:00 JST 開放）",
        f"全{s['slots']}枠 × {s['cap']}艇 = {s['total']}艇 / 残り{s['remain']}艇",
        ("空き枠：" + "、".join(s["open_times"])) if s["open_times"] else "空き枠なし",
    ])

    return {
        "date_label": label(latest),
        "status": status,
        "updated": datetime.now(JST).strftime("%Y-%m-%d %H:%M"),
        "detail": detail,
        "source": CALENDAR,
        "title_zh": TITLE_ZH,
        "status_zh": status_zh,
        "summary_zh": f"{line1}\n{line2}",
    }


def find_item(data):
    """回傳（item, 該天的日期）。日期用來算我們自己的開賣日。"""
    for day in data.get("days", []):
        for item in day.get("items", []):
            if ITEM_KEYWORD in item.get("name", ""):
                try:
                    d = datetime.strptime(day["date"], "%Y-%m-%d").date()
                except (KeyError, ValueError):
                    d = None
                return item, d
    return None, None


def main():
    trip_path = Path(sys.argv[1] if len(sys.argv) > 1 else "trip.json")
    if not trip_path.exists():
        fail(f"找不到 {trip_path}")

    data = json.loads(trip_path.read_text(encoding="utf-8"))
    item, our_date = find_item(data)
    if item is None:
        fail(f"trip.json 裡找不到 name 含「{ITEM_KEYWORD}」的 item")
    if our_date is None:
        fail("高千穗峽那天的 days[].date 讀不出來")

    today = datetime.now(JST).date()
    try:
        slots = fetch_slots(today, today + timedelta(days=LOOKAHEAD))
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        fail(f"抓不到「{CALENDAR}」：{exc}")

    parsed = parse(slots, today, our_date)

    if item.get("live_status") == parsed:
        print(f"＝ 內容無變化（{parsed['status_zh']}），不寫入")
        return

    # updated 每次都會變，所以只有這欄不同時也算沒變（避免每天產生沒意義的 commit）
    old = dict(item.get("live_status") or {})
    if old and {k: v for k, v in old.items() if k != "updated"} == \
            {k: v for k, v in parsed.items() if k != "updated"}:
        print(f"＝ 只有更新時間不同（{parsed['status_zh']}），不寫入")
        return

    item["live_status"] = parsed
    trip_path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"✅ 已更新 {trip_path}：{parsed['status_zh']}")
    print(parsed["summary_zh"])


if __name__ == "__main__":
    main()
