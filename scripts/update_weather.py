#!/usr/bin/env python3
"""抓氣象廳（JMA）預報，寫進 trip.json 每天的 weather_live。

用法：python scripts/update_weather.py [trip.json 路徑]

離開碼：
  0  已更新，或行程還在預報範圍外（正常情況，不寫檔）
  1  抓取或解析失敗（讓 GitHub Actions 顯示紅燈）

每天用 trip.json 的 days[].weather_area 決定要抓哪個預報區：
  {"office": "400000", "area": "400010", "spot": "82182", "label": "福岡"}
  office 府縣預報區（氣象台）／area 一次細分區／spot 溫度觀測點／label 手冊上顯示的地名

氣象廳只給到 7 天後，行程還遠的時候抓不到——那是預期行為，不是失敗：
維持 trip.json 原本的 weather 靜態描述（樣板會自動退回顯示）。
"""
import json
import sys
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

API = "https://www.jma.go.jp/bosai/forecast/data/forecast/{office}.json"
JST = timezone(timedelta(hours=9))
UA = "Mozilla/5.0 (compatible; kyushu-handbook-bot/1.0; +https://github.com/WeiCho/kyushu-2026-joeemma)"
TIMEOUT = 30
RETRIES = 3
RETRY_WAIT = 10

# 氣象廳天氣代碼 → 中文。查不到的代碼用百位數退回「晴／多雲／雨／雪」，
# 寧可粗一點，也不要在手冊上開天窗。
TELOP_ZH = {
    "100": "晴", "101": "晴時多雲", "102": "晴短暫雨", "103": "晴時陣雨",
    "104": "晴短暫雪", "105": "晴時陣雪", "106": "晴短暫雨或雪",
    "110": "晴後多雲", "111": "晴後多雲", "112": "晴後短暫雨", "113": "晴後陣雨",
    "114": "晴後雨", "115": "晴後短暫雪", "116": "晴後陣雪", "117": "晴後雪",
    "119": "晴後雷雨", "120": "晴晨晚短暫雨", "121": "晴晨晚短暫雨",
    "122": "晴傍晚短暫雨", "123": "晴山區雷雨", "124": "晴山區陣雪",
    "125": "晴午後雷雨", "126": "晴午後短暫雨", "127": "晴傍晚起短暫雨",
    "128": "晴入夜短暫雨", "130": "晨霧後晴", "131": "晴清晨有霧",
    "132": "晴晨晚多雲", "140": "晴陣雨伴雷", "160": "晴短暫雪或雨",
    "200": "多雲", "201": "多雲時晴", "202": "多雲短暫雨", "203": "多雲時陣雨",
    "204": "多雲短暫雪", "205": "多雲時陣雪", "206": "多雲短暫雨或雪",
    "209": "霧", "210": "多雲後晴", "211": "多雲後晴", "212": "多雲後短暫雨",
    "213": "多雲後陣雨", "214": "多雲後雨", "215": "多雲後短暫雪",
    "216": "多雲後陣雪", "217": "多雲後雪", "219": "多雲後雷雨",
    "220": "多雲晨晚短暫雨", "221": "多雲晨晚短暫雨", "222": "多雲傍晚短暫雨",
    "224": "多雲午後短暫雨", "225": "多雲傍晚起短暫雨", "226": "多雲入夜短暫雨",
    "228": "多雲午後陣雪", "231": "多雲海上有霧", "240": "多雲陣雨伴雷",
    "300": "雨", "301": "雨時晴", "302": "雨時停", "303": "雨轉雪",
    "304": "雨或雪", "306": "大雨", "308": "雨伴強風", "309": "雨短暫雪",
    "311": "雨後晴", "313": "雨後多雲", "314": "雨後轉雪", "315": "雨後雪",
    "316": "雨或雪後晴", "317": "雨或雪後多雲", "320": "晨雨後晴",
    "321": "晨雨後多雲", "323": "雨午前轉晴", "324": "雨午後轉晴",
    "325": "雨傍晚轉晴", "328": "雨一時強降雨", "329": "雨短暫雨雪",
    "340": "雪或雨", "350": "雷雨", "361": "雪或雨後晴", "371": "雪或雨後多雲",
    "400": "雪", "401": "雪時晴", "402": "雪時停", "403": "雪或雨",
    "406": "風雪", "411": "雪後晴", "413": "雪後多雲", "414": "雪後雨",
    "425": "雪一時強降雪", "426": "雪轉雨雪", "427": "雪一時雨雪",
}
COARSE_ZH = {"1": "晴", "2": "多雲", "3": "雨", "4": "雪"}


def fail(msg):
    print("[X] " + msg, file=sys.stderr)
    sys.exit(1)


def fetch(url):
    last = None
    for attempt in range(RETRIES):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            last = exc
            if attempt < RETRIES - 1:
                time.sleep(RETRY_WAIT * (attempt + 1))
    raise last


def telop(code):
    if code is None:
        return None
    if code in TELOP_ZH:
        return TELOP_ZH[code]
    return COARSE_ZH.get(str(code)[:1])


def _dates(time_series):
    """timeDefines → date（JST）。"""
    return [
        datetime.fromisoformat(t).astimezone(JST).date()
        for t in time_series["timeDefines"]
    ]


def _area(time_series, code):
    for a in time_series["areas"]:
        if a["area"]["code"] == code:
            return a
    return None


def _at(values, idx):
    """取值；氣象廳用空字串表示「這格沒有」，一律當成沒有。"""
    if idx >= len(values):
        return None
    return values[idx] or None


def forecast_for(doc, day_date, cfg):
    """回傳 (天氣, 最低溫, 最高溫, 降雨機率, 是否為週間預報)；整天都抓不到回 None。"""
    detail = doc[0]
    weekly = doc[1] if len(doc) > 1 else None
    weather = low = high = pop = None
    from_weekly = False

    # ① 三日預報：地區細到「福岡地方」「阿蘇地方」，優先用
    ts = detail["timeSeries"][0]
    area = _area(ts, cfg["area"])
    if area:
        days = _dates(ts)
        if day_date in days:
            weather = telop(_at(area["weatherCodes"], days.index(day_date)))

    # 降雨機率是 6 小時一格；同一天取最大值——要回答的是「今天要不要帶傘」
    ts = detail["timeSeries"][1]
    area = _area(ts, cfg["area"])
    if area:
        vals = [int(v) for d, v in zip(_dates(ts), area["pops"]) if d == day_date and v]
        if vals:
            pop = max(vals)

    # 三日預報的溫度：同一天有兩格，00 時是最低溫、09 時是最高溫。
    # 「今天」的最低溫已經過去了，氣象廳那一格填的是最高溫，會變成「36–36°C」，所以跳過
    ts = detail["timeSeries"][2]
    area = _area(ts, cfg["spot"])
    if area:
        today = datetime.now(JST).date()
        for iso, v in zip(ts["timeDefines"], area["temps"]):
            dt = datetime.fromisoformat(iso).astimezone(JST)
            if dt.date() != day_date or not v:
                continue
            if dt.hour == 0 and day_date > today:
                low = int(v)
            elif dt.hour == 9:
                high = int(v)

    # ② 週間預報：補三日預報看不到的第 4～7 天。只到府縣層級，溫度也只有代表地點
    if weekly:
        ts = weekly["timeSeries"][0]
        area = ts["areas"][0]
        days = _dates(ts)
        if day_date in days:
            i = days.index(day_date)
            if weather is None:
                weather = telop(_at(area["weatherCodes"], i))
                from_weekly = weather is not None
            if pop is None:
                v = _at(area["pops"], i)
                pop = int(v) if v else None
        ts = weekly["timeSeries"][1]
        area = ts["areas"][0]
        days = _dates(ts)
        if day_date in days:
            i = days.index(day_date)
            if low is None:
                v = _at(area["tempsMin"], i)
                low = int(v) if v else None
            if high is None:
                v = _at(area["tempsMax"], i)
                high = int(v) if v else None

    if weather is None and high is None and low is None:
        return None
    return weather, low, high, pop, from_weekly


def compose(label, weather, low, high, pop):
    """組成手冊上那一行。缺哪一項就跳過哪一項，不要留空欄位。"""
    parts = []
    if low is not None and high is not None:
        parts.append(f"{low}–{high}°C")
    elif high is not None:
        parts.append(f"最高 {high}°C")
    elif low is not None:
        parts.append(f"最低 {low}°C")
    if weather:
        parts.append(weather)
    if pop is not None:
        parts.append(f"降雨 {pop}%")
    return f"{label} " + "・".join(parts) if parts else label


def main():
    trip_path = Path(sys.argv[1] if len(sys.argv) > 1 else "trip.json")
    if not trip_path.exists():
        fail(f"找不到 {trip_path}")

    data = json.loads(trip_path.read_text(encoding="utf-8"))
    days = [d for d in data.get("days", []) if d.get("weather_area")]
    if not days:
        fail("trip.json 的 days 都沒有 weather_area，不知道要抓哪一區")

    cache = {}
    changed = 0
    for day in days:
        cfg = day["weather_area"]
        office = cfg["office"]
        if office not in cache:
            try:
                cache[office] = fetch(API.format(office=office))
            except Exception as exc:  # noqa: BLE001 — 印出原因後直接紅燈
                fail(f"抓不到氣象廳 {office} 預報：{exc}")
        doc = cache[office]
        got = forecast_for(doc, date.fromisoformat(day["date"]), cfg)
        if got is None:
            print(f"[-] {day['date']} {cfg['label']}：還在預報範圍外（氣象廳只給到 7 天後）")
            continue
        weather, low, high, pop, from_weekly = got
        live = {
            "text": compose(cfg["label"], weather, low, high, pop),
            "updated": datetime.fromisoformat(doc[0]["reportDatetime"])
            .astimezone(JST)
            .strftime("%m/%d %H:%M"),
        }
        if from_weekly:
            # 週間預報是府縣層級的粗預報，別讓人以為是當地細分區的數字
            live["scope"] = "週間預報"
        if day.get("weather_live") == live:
            print(f"[=] {day['date']} {live['text']}（無變化）")
            continue
        day["weather_live"] = live
        changed += 1
        print(f"[v] {day['date']} {live['text']}")

    if not changed:
        print("沒有需要寫入的變更")
        return
    trip_path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"[v] 已更新 {trip_path}：{changed} 天")


if __name__ == "__main__":
    main()
