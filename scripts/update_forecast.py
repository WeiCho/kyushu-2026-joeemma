#!/usr/bin/env python3
"""抓 Open-Meteo 的點預報，寫進 trip.json 每天的 forecast（氣溫、降雨機率、逐時）。

用法：python scripts/update_forecast.py [trip.json 路徑]

離開碼：
  0  已更新，或行程還在預報範圍外／已經結束（正常情況，不寫檔）
  1  抓取或解析失敗（讓 GitHub Actions 顯示紅燈）

為什麼不是 Windy：Windy 的 Point Forecast API 免費版會回傳「刻意打亂、修改過」的數字，
正式版一年 990 歐元；Premium 會員只解鎖網站與 App，不含 API。所以手冊上自動顯示的數字
用 Open-Meteo（免金鑰、個人非商業免費），細看再點手冊上的 Windy 連結。

座標用 days[].weather_area 的 lat／lon（與 Windy 連結同一點），寫進 days[].forecast。
同一天要多看幾個點（例如高千穗那天的草千里，海拔差 800 m、氣溫差 5°C 以上），
在 days[].forecast_areas 列 [{"label", "lat", "lon"}]，依序寫進 days[].forecast_extra（每筆多帶 label）。
Open-Meteo 最多預報 16 天，超出範圍的日子不寫，樣板會退回氣象廳或靜態描述。

手冊上只列一個模型（長輩看到三組氣溫只會更不知道信哪個），依距離自動挑：
  3 天內      JMA MSM（氣象廳 5km 高解析，山區溫度最準）；它沒有降雨機率，降雨借 ECMWF
  4～15 天    ECMWF IFS 9 km（中長期最穩；跟 Windy 的 ECMWF 同一個解析度）
  ECMWF 之外  GFS（16 天，先頂著，ECMWF 涵蓋到就自動換掉）
某模型只要那一天 24 小時有任何一格缺，就當它沒涵蓋那天——只涵蓋半天的日最低／最高溫是錯的。
另外比對 ECMWF 與 GFS：最低或最高溫差 4°C 以上就記下 spread，手冊提醒「各模式差異大」。

逐時的粒度跟著預報距離走：3 天內的高解析模型是真的每小時一筆，就列每小時；
更遠的日子模型本身只有 3～6 小時一筆，逐時數字是插補出來的，列出來只是假精確，
所以改成每 3 小時一筆。

氣溫是 Open-Meteo 依實際海拔修正過的（預設行為）：模型格點把周圍山地平均進去，
高千穗那格約 700 m，實際町區 305 m，不修正會整排低 2～3°C。Windy 等網站顯示格點高度的值時
會比手冊冷，這是正常的。
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
SPREAD_C = 4          # ECMWF 與 GFS 溫差到這個度數就提醒

# Open-Meteo 的模型代號 → 手冊上顯示的名字
MSM, ECMWF, GFS = "jma_msm", "ecmwf_ifs", "gfs_seamless"
MODELS = (MSM, ECMWF, GFS)
LABEL = {MSM: "JMA MSM", ECMWF: "ECMWF", GFS: "GFS"}

# WMO 天氣代碼 → 中文。Open-Meteo 的每日代碼是「當天最嚴重的那個」，
# 一天裡只要飄過毛毛雨就會是 51，所以摘要改用逐時代碼算（見 summarize）
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
DAYTIME = range(8, 18)   # 摘要的「晴／陰」看白天這幾個小時


def is_wet(code):
    """降水類代碼（毛毛雨、雨、雪、陣雨、雷雨）；0–48 是晴、雲、霧。"""
    return code is not None and code >= 51


def summarize(codes):
    """把白天逐時的天氣代碼濃縮成一句話。

    每日代碼是「最嚴重的那個」，一小時毛毛雨就整天變「短暫毛毛雨」，看不出是晴是陰；
    所以改看白天：天空取最常出現的狀態，下雨的時數另外講。
    """
    codes = [c for c in codes if c is not None]
    if not codes:
        return None
    sky = [c for c in codes if not is_wet(c)]
    wet = [c for c in codes if is_wet(c)]
    if len(wet) * 2 >= len(codes):          # 白天一半以上在下
        return WMO_ZH.get(max(set(wet), key=wet.count))
    # 晴 0 和大致晴 1 合併算，免得兩者各半時被「晴時多雲」搶走
    bucket = [min(c, 1) if c <= 1 else c for c in sky]
    top = max(set(bucket), key=bucket.count)
    text = "晴" if top <= 1 else WMO_ZH.get(top)
    return f"{text}，短暫有雨" if wet else text


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


# 晴陰分歧用的粗分級：晴 0、晴時多雲 1、陰／霧 2、雨 3。兩個模型差兩級以上
# （晴 vs 陰、晴時多雲 vs 雨）才提醒；只差一級在雲量預報裡算正常誤差
SKY_GAP = 2


def sky_level(text):
    if text is None:
        return None
    if text == "晴":
        return 0
    if text == "晴時多雲":
        return 1
    if text in ("陰", "霧"):
        return 2
    return 3   # 各種雨雪，以及「晴，短暫有雨」這類


def daytime_text(loc, model, iso):
    """某模型白天的晴陰摘要；白天時段沒涵蓋滿就回 None（MSM 第 3 天常只到中午）。"""
    h = loc["hourly"]
    codes = {int(t[11:13]): h[f"weather_code_{model}"][j]
             for j, t in enumerate(h["time"]) if t.startswith(iso)}
    day = [codes.get(hh) for hh in DAYTIME]
    if any(c is None for c in day):
        return None
    return summarize([int(c) for c in day])


def day_series(loc, model, iso):
    """某模型在某一天的逐時 [(時, 氣溫, 降雨機率, 天氣代碼)]；24 小時沒有全部涵蓋就回 None。"""
    h = loc["hourly"]
    temps = h[f"temperature_2m_{model}"]
    pops = h[f"precipitation_probability_{model}"]
    codes = h[f"weather_code_{model}"]
    rows = [(int(t[11:13]), temps[j], pops[j], codes[j])
            for j, t in enumerate(h["time"]) if t.startswith(iso)]
    if len(rows) != 24 or any(r[1] is None for r in rows):
        return None
    return rows


def daily_of(loc, model, iso, field):
    d = loc["daily"]
    return d[f"{field}_{model}"][d["time"].index(iso)]


def build(loc, day_date, lead):
    """從一個地點的多模型回應組出某一天的 forecast；沒有任何模型涵蓋那天回 None。"""
    iso = day_date.isoformat()
    if iso not in loc["daily"]["time"]:
        return None
    series = {m: day_series(loc, m, iso) for m in MODELS}

    if lead <= HOURLY_WITHIN and series[MSM]:
        model = MSM
    elif series[ECMWF]:
        model = ECMWF
    elif series[GFS]:
        model = GFS
    else:
        return None
    rows = series[model]
    # 降雨機率：MSM 沒有，借 ECMWF（再不行 GFS）
    rain_model = model if model != MSM else (ECMWF if series[ECMWF] else GFS)
    rain_rows = {r[0]: r[2] for r in series[rain_model] or []}

    # 晴／陰跟氣溫用同一個模型：各模型對雲量常常差很多（MSM 晴、ECMWF 陰），
    # 混著用會出現「圖示晴、氣溫卻是陰天的曲線」。某小時缺代碼才依序借別的模型
    def code_at(hh):
        for m in (model, rain_model, ECMWF, GFS):
            c = next((r[3] for r in series[m] or [] if r[0] == hh), None)
            if c is not None:
                return int(c)
        return None

    temps = [r[1] for r in rows]
    step = 1 if lead <= HOURLY_WITHIN else 3
    hours = [
        {"h": f"{hh:02d}", "t": round_or_none(t), "p": rain_rows.get(hh), "w": code_at(hh)}
        for hh, t, _, _ in rows
        if hh in HOURS and (hh - HOURS.start) % step == 0
    ]
    text = summarize([code_at(hh) for hh in DAYTIME])
    if text is None:
        # 逐時代碼全缺才退回每日代碼；模型最後一天常常是空的，依序找別的模型補
        code = next((c for c in (daily_of(loc, m, iso, "weather_code")
                                 for m in (rain_model, ECMWF, GFS, MSM)) if c is not None), None)
        text = WMO_ZH.get(code)
    fc = {
        "model": LABEL[model],
        "text": text,
        "tmin": round_or_none(min(temps)),
        "tmax": round_or_none(max(temps)),
        "pop": daily_of(loc, rain_model, iso, "precipitation_probability_max"),
        "step": step,
        "hours": hours,
    }

    # 晴陰分歧：白天涵蓋滿的模型都拿來比，差兩級以上就把各模型的說法列出來
    skies = [(LABEL[m], t) for m in MODELS if (t := daytime_text(loc, m, iso))]
    levels = [sky_level(t) for _, t in skies]
    if len(skies) >= 2 and max(levels) - min(levels) >= SKY_GAP:
        fc["sky_spread"] = [{"model": m, "text": t} for m, t in skies]

    # 模式分歧：ECMWF 與 GFS 都涵蓋時才比
    if series[ECMWF] and series[GFS]:
        e = [r[1] for r in series[ECMWF]]
        g = [r[1] for r in series[GFS]]
        for name, fn in (("最低溫", min), ("最高溫", max)):
            lo, hi = sorted((round_or_none(fn(e)), round_or_none(fn(g))))
            if hi - lo >= SPREAD_C:
                fc["spread"] = {"what": name, "lo": lo, "hi": hi}
                break
    return fc


def main():
    trip_path = Path(sys.argv[1] if len(sys.argv) > 1 else "trip.json")
    if not trip_path.exists():
        fail(f"找不到 {trip_path}")

    data = json.loads(trip_path.read_text(encoding="utf-8"))
    today = datetime.now(JST).date()
    last_day = today + timedelta(days=HORIZON - 1)

    def point(a):
        return (a["lat"], a["lon"]) if a.get("lat") is not None and a.get("lon") is not None else None

    targets = []   # (day, 日期, weather_area 座標或 None, [(label, 座標)] 給 forecast_areas)
    for day in data.get("days", []):
        d = date.fromisoformat(day["date"])
        # 已經過去的日子不再覆寫：留著最後一次的預報，回頭看也知道當天大概怎樣
        if not today <= d <= last_day:
            continue
        main = point(day.get("weather_area") or {})
        extra = [(a["label"], point(a)) for a in day.get("forecast_areas", []) if point(a)]
        if main or extra:
            targets.append((day, d, main, extra))

    if not targets:
        print("＝ 行程不在 Open-Meteo 預報範圍內（太遠或已結束），不寫入")
        return

    # 同一個座標只抓一次；Open-Meteo 接受逗號分隔的多點，一次請求就夠
    points = sorted({c for _, _, main, extra in targets
                     for c in [main] + [c for _, c in extra] if c})
    query = urllib.parse.urlencode({
        "latitude": ",".join(f"{lat:.3f}" for lat, _ in points),
        "longitude": ",".join(f"{lon:.3f}" for _, lon in points),
        "daily": "weather_code,precipitation_probability_max",
        "hourly": "temperature_2m,precipitation_probability,weather_code",
        "models": ",".join(MODELS),
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

    def strip(fc):
        return {k: v for k, v in (fc or {}).items() if k != "updated"}

    for day, d, main_c, extra_c in targets:
        def fc_at(c, label):
            try:
                return build(by_point[c], d, (d - today).days)
            except (KeyError, IndexError, TypeError) as exc:
                fail(f"{day['date']} {label} 的回應格式看不懂：{exc}")

        main = fc_at(main_c, day["weather_area"].get("label")) if main_c else None
        extra = [dict(fc, label=label) for label, c in extra_c if (fc := fc_at(c, label))]
        same_main = main is None or strip(main) == strip(day.get("forecast"))
        same_extra = [strip(f) for f in extra] == [strip(f) for f in day.get("forecast_extra", [])]
        if same_main and same_extra:
            continue
        shown = []
        if main:
            day["forecast"] = dict(main, updated=stamp)
            shown.append((day["weather_area"].get("label"), main))
        if extra:
            day["forecast_extra"] = [dict(f, updated=stamp) for f in extra]
            shown += [(f["label"], f) for f in extra]
        changed += 1
        for label, fc in shown:
            sp = fc.get("spread")
            print(f"✅ {day['date']} {label} [{fc['model']}] "
                  f"{fc['tmin']}–{fc['tmax']}°C {fc['text'] or ''} 降雨 {fc['pop']}%（每 {fc['step']} 小時）"
                  + (f" ⚠ {sp['what']} {sp['lo']}–{sp['hi']}°C" if sp else "")
                  + (" ⚠ 晴陰：" + "、".join(f"{x['model']} {x['text']}" for x in fc["sky_spread"])
                     if fc.get("sky_spread") else ""))

    if not changed:
        print("＝ 預報內容無變化，不寫入")
        return
    trip_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"已更新 {changed} 天")


if __name__ == "__main__":
    main()
