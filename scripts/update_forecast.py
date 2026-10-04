#!/usr/bin/env python3
"""抓 Open-Meteo 的點預報，寫進 trip.json 每天的 forecast（氣溫、降雨機率、逐時）。

用法：python scripts/update_forecast.py [trip.json 路徑]

離開碼：
  0  已更新，或行程還在預報範圍外／已經結束（正常情況，不寫檔）
  1  抓取或解析失敗（讓 GitHub Actions 顯示紅燈）

為什麼不是 Windy：Windy 的 Point Forecast API 免費版會回傳「刻意打亂、修改過」的數字，
正式版一年 990 歐元；Premium 會員只解鎖網站與 App，不含 API。所以手冊上自動顯示的數字
用 Open-Meteo（免金鑰、個人非商業免費），細看再點手冊上的 Windy 連結。

座標用 days[].weather_area 的 lat／lon（與 Windy 連結同一點）。
Open-Meteo 最多預報 16 天，超出範圍的日子不寫，樣板會退回氣象廳或靜態描述。

逐時的粒度跟著預報距離走：3 天內的高解析模型是真的每小時一筆，就列每小時；
更遠的日子模型本身只有 3～6 小時一筆，逐時數字是插補出來的，列出來只是假精確，
所以改成每 3 小時一筆。
"""
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

API = "https://api.open-meteo.com/v1/forecast"
JST = timezone(timedelta(hours=9))
UA = "Mozilla/5.0 (compatible; kyushu-handbook-bot/1.0; +https://github.com/WeiCho/kyushu-2026-joeemma)"
TIMEOUT = 30
RETRIES = 3
RETRY_WAIT = 10
HORIZON = 16          # Open-Meteo 的最長預報天數
HOURLY_WITHIN = 3     # 幾天內列每小時
HOURS = range(6, 22)  # 只列白天到晚上（06–21 時），半夜的數字沒人看

# WMO 天氣代碼 → 中文。Open-Meteo 的每日代碼是「當天最嚴重的那個」，
# 所以一天裡只要飄過毛毛雨就會是 51——要搭配降雨機率一起看
WMO_ZH = {
    0: "晴", 1: "大致晴", 2: "晴時多雲", 3: "陰",
    45: "霧", 48: "霧",
    51: "短暫毛毛雨", 53: "毛毛雨", 55: "毛毛雨",
    56: "凍毛雨", 57: "凍毛雨",
    61: "小雨", 63: "雨", 65: "大雨", 66: "凍雨", 67: "凍雨",
    71: "小雪", 73: "雪", 75: "大雪", 77: "霰",
    80: "短暫陣雨", 81: "陣雨", 82: "強陣雨",
    85: "陣雪", 86: "強陣雪",
    95: "雷雨", 96: "雷雨夾冰雹", 99: "雷雨夾冰雹",
}


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


def round_or_none(v):
    return None if v is None else int(round(v))


def build(loc, day_date, lead):
    """從一個地點的回應組出某一天的 forecast；那天不在回應裡回 None。"""
    iso = day_date.isoformat()
    daily = loc["daily"]
    if iso not in daily["time"]:
        return None
    i = daily["time"].index(iso)
    code = daily["weather_code"][i]
    tmax = round_or_none(daily["temperature_2m_max"][i])
    tmin = round_or_none(daily["temperature_2m_min"][i])
    if tmax is None and tmin is None:
        return None

    step = 1 if lead <= HOURLY_WITHIN else 3
    hourly = loc["hourly"]
    hours = []
    for j, t in enumerate(hourly["time"]):
        if not t.startswith(iso):
            continue
        h = int(t[11:13])
        if h not in HOURS or (h - HOURS.start) % step:
            continue
        temp = round_or_none(hourly["temperature_2m"][j])
        if temp is None:
            continue
        hours.append({
            "h": f"{h:02d}",
            "t": temp,
            "p": hourly["precipitation_probability"][j],
        })

    return {
        "text": WMO_ZH.get(code),
        "tmin": tmin,
        "tmax": tmax,
        "pop": daily["precipitation_probability_max"][i],
        "step": step,
        "hours": hours,
    }


def main():
    trip_path = Path(sys.argv[1] if len(sys.argv) > 1 else "trip.json")
    if not trip_path.exists():
        fail(f"找不到 {trip_path}")

    data = json.loads(trip_path.read_text(encoding="utf-8"))
    today = datetime.now(JST).date()
    last_day = today + timedelta(days=HORIZON - 1)

    targets = []
    for day in data.get("days", []):
        wa = day.get("weather_area") or {}
        if wa.get("lat") is None or wa.get("lon") is None:
            continue
        d = date.fromisoformat(day["date"])
        # 已經過去的日子不再覆寫：留著最後一次的預報，回頭看也知道當天大概怎樣
        if today <= d <= last_day:
            targets.append((day, d, (wa["lat"], wa["lon"])))

    if not targets:
        print("＝ 行程不在 Open-Meteo 預報範圍內（太遠或已結束），不寫入")
        return

    # 同一個座標只抓一次；Open-Meteo 接受逗號分隔的多點，一次請求就夠
    points = sorted({c for _, _, c in targets})
    query = urllib.parse.urlencode({
        "latitude": ",".join(f"{lat:.3f}" for lat, _ in points),
        "longitude": ",".join(f"{lon:.3f}" for _, lon in points),
        "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max",
        "hourly": "temperature_2m,precipitation_probability",
        "timezone": "Asia/Tokyo",
        "forecast_days": HORIZON,
    })
    try:
        resp = fetch(f"{API}?{query}")
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        fail(f"抓不到 Open-Meteo：{exc}")
    if isinstance(resp, dict):
        if resp.get("error"):
            fail(f"Open-Meteo 回傳錯誤：{resp.get('reason')}")
        resp = [resp]   # 只有一個點時回傳的是物件不是陣列
    by_point = dict(zip(points, resp))

    stamp = datetime.now(JST).strftime("%m/%d %H:%M")
    changed = 0
    for day, d, c in targets:
        try:
            fc = build(by_point[c], d, (d - today).days)
        except (KeyError, IndexError, TypeError) as exc:
            fail(f"{day['date']} 的回應格式看不懂：{exc}")
        if fc is None:
            continue
        old = {k: v for k, v in (day.get("forecast") or {}).items() if k != "updated"}
        if old == fc:
            continue
        fc["updated"] = stamp
        day["forecast"] = fc
        changed += 1
        print(f"✅ {day['date']} {day['weather_area']['label']} "
              f"{fc['tmin']}–{fc['tmax']}°C {fc['text'] or ''} 降雨 {fc['pop']}%（每 {fc['step']} 小時）")

    if not changed:
        print("＝ 預報內容無變化，不寫入")
        return
    trip_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"已更新 {changed} 天")


if __name__ == "__main__":
    main()
