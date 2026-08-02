#!/usr/bin/env python3
"""抓高千穗峽貸しボート當日運行狀態，寫進 trip.json 的 live_status。

用法：python scripts/update_takachiho.py [trip.json 路徑]

離開碼：
  0  已更新，或抓到的日期不是今天（JST）而略過不寫
  1  抓取或解析失敗（讓 GitHub Actions 顯示紅燈）

抓取失敗時一律不動 trip.json，也不沿用舊值。
"""
import html
import json
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

SOURCE = "https://takachiho-kanko.info/"
ITEM_KEYWORD = "高千穗峽"
JST = timezone(timedelta(hours=9))
UA = "Mozilla/5.0 (compatible; kyushu-handbook-bot/1.0; +https://github.com/WeiCho/kyushu-2026-joeemma)"

# 官網 <div class="box_boat"> 區塊的結構（2026-08 確認）
RE_BLOCK = re.compile(r'<div class="box_boat">(.*?)</small>', re.S)
RE_UPDATED = re.compile(r"更新時間：<span>\s*(\d{4})年(\d{1,2})月(\d{1,2})日\s*(.*?)\s*</span>")
RE_DATE = re.compile(r'<p class="time">\s*(\d{1,2})/(\d{1,2})\s*<span class="week">\s*\[(\w+)\]')
RE_STATUS = re.compile(r'<p class="(?:red|blue|green|gray)?"[^>]*>(.*?)</p>\s*</div>', re.S)
RE_NOTE = re.compile(r'<small class="note">(.*?)$', re.S)

# ── 中文欄位 ─────────────────────────────────────────────
# 來源網站是日文，手冊要顯示中文。這裡做規則式轉換，不接翻譯 API：
# Actions 不該依賴外部服務，而且營運狀態的用語就那幾種，規則涵蓋得住。
# 轉不出來就不寫該欄位，樣板會自動退回顯示日文原文——寧可露出日文，也不要湊出錯的中文。
TITLE_ZH = "高千穗峽　划船運行狀況"

# 狀態：整句對照。key 為出現在官網狀態字串裡的片段
STATUS_ZH = (
    ("通常営業", "正常營運"),
    ("営業中止", "停止出租"),
    ("運航中止", "停駛"),
    ("運休", "停駛"),
    ("中止", "停止"),
    ("営業", "營運"),
)
# 停駛原因
REASON_ZH = (
    ("増水", "水位上漲"),
    ("減水", "水位過低"),
    ("荒天", "天候不佳"),
    ("悪天", "天候不佳"),
    ("大雨", "大雨"),
    ("台風", "颱風"),
    ("強風", "強風"),
    ("点検", "設施檢修"),
    ("清掃", "清掃"),
)


def status_to_zh(status):
    """運行狀態日文 → 中文。轉不出來回傳 None。"""
    core = None
    for ja, zh in STATUS_ZH:
        if ja in status:
            core = zh
            break
    if core is None:
        return None
    reason = next((zh for ja, zh in REASON_ZH if ja in status), None)
    return f"{reason}，{core}" if reason else core


def _hm(text):
    """「2時間30分」→「2 小時 30 分」；「3時間」→「3 小時」。"""
    m = re.fullmatch(r"(\d+)時間(?:(\d+)分)?", text)
    if not m:
        return None
    h, mi = m.group(1), m.group(2)
    return f"{h} 小時 {mi} 分" if mi else f"{h} 小時"


def detail_to_zh(detail):
    """公告內文 → 最多兩行中文重點。抓不到任何一項就回傳 None。

    只挑「當天還買不買得到票、要怎麼買」這兩件事——這是速查卡，不是公告全文。
    """
    # 全形標點一併正規化。漏了「】」會讓等候時間抓不到，而那是最有用的一項
    text = (detail.replace("：", ":").replace("（", "(").replace("）", ")")
                  .replace("【", "[").replace("】", "]"))
    tickets = []

    # 今日票況：完售 / 所剩不多 / 尚有 N 張
    m = re.search(r"(\d{1,2})/(\d{1,2})\D{0,6}?(\d{1,2}:\d{2})?\s*(?:→|->)?\s*当日券完売", text)
    if m:
        when = f"（{m.group(3)}）" if m.group(3) else ""
        tickets.append(f"當日券已完售{when}")
    elif "残りわずか" in text:
        tickets.append("當日券所剩不多")

    # 其他日期的餘票
    m = re.search(r"(\d{1,2})/(\d{1,2})\D{0,6}?約\s*(\d+)\s*枚", text)
    if m:
        board = ""
        m2 = re.search(r"(\d{1,2}:\d{2})以降の乗船", text)
        if m2:
            board = f"、限 {m2.group(1)} 之後乘船"
        tickets.append(f"{int(m.group(1))}/{int(m.group(2))} 約 {m.group(3)} 張{board}")

    # 現場等候時間
    m = re.search(r"現在待ち時間\]?\s*([\d時間分]+)\s*(?:～|~|-)\s*([\d時間分]+)", text)
    if m:
        a, b = _hm(m.group(1)), _hm(m.group(2))
        if a and b:
            tickets.append(f"現在等候 {a}～{b}")

    # 怎麼買
    how = []
    m = re.search(r"当日の朝\s*(\d{1,2}:\d{2})\s*より", text)
    if m:
        how.append(f"當日券僅當天早上 {m.group(1)} 起於現場販售")
    if "事前ネット予約のみ" in text:
        how.append("事前預約只走網路")
    if "電話でのご予約" in text and "承っており" in text:
        how.append("不接受電話預約")

    lines = []
    if tickets:
        lines.append("，".join(tickets) + "。")
    if how:
        lines.append("，".join(how) + "。")
    return "\n".join(lines[:2]) or None


def fail(msg):
    print(f"❌ {msg}", file=sys.stderr)
    sys.exit(1)


def clean(raw):
    """HTML 片段 → 純文字，<br> 變換行。"""
    text = re.sub(r"<br\s*/?>", "\n", raw)
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    lines = [re.sub(r"[\s　]+", " ", ln).strip() for ln in text.split("\n")]
    return "\n".join(ln for ln in lines if ln)


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read().decode("utf-8", "replace")


def parse(page):
    block = RE_BLOCK.search(page)
    if not block:
        fail("找不到 box_boat 區塊，官網結構可能改版了")
    body = block.group(1)

    m_date = RE_DATE.search(body)
    if not m_date:
        fail("解析不到日期（<p class=\"time\">），官網結構可能改版了")
    month, day, week = int(m_date.group(1)), int(m_date.group(2)), m_date.group(3)

    m_status = RE_STATUS.search(body)
    if not m_status:
        fail("解析不到運行狀態，官網結構可能改版了")
    status = clean(m_status.group(1))
    if not status:
        fail("運行狀態是空的，官網結構可能改版了")

    m_upd = RE_UPDATED.search(body)
    if m_upd:
        y, mo, d, clock = m_upd.groups()
        updated = f"{int(y):04d}-{int(mo):02d}-{int(d):02d} {clock}"
    else:
        updated = datetime.now(JST).strftime("%Y-%m-%d %H:%M") + " (抓取時間)"

    m_note = RE_NOTE.search(body)
    detail = clean(m_note.group(1)) if m_note else ""

    parsed = {
        "date_label": f"{month}/{day}[{week}]",
        "status": status,
        "updated": updated,
        "detail": detail,
        "source": SOURCE,
        "_md": (month, day),
    }

    # 中文欄位：轉得出來才寫，轉不出來就留空讓樣板退回日文原文
    parsed["title_zh"] = TITLE_ZH
    status_zh = status_to_zh(status)
    if status_zh:
        parsed["status_zh"] = status_zh
    summary_zh = detail_to_zh(detail)
    if summary_zh:
        parsed["summary_zh"] = summary_zh
    return parsed


def find_item(data):
    for day in data.get("days", []):
        for item in day.get("items", []):
            if ITEM_KEYWORD in item.get("name", ""):
                return item
    return None


def main():
    trip_path = Path(sys.argv[1] if len(sys.argv) > 1 else "trip.json")
    if not trip_path.exists():
        fail(f"找不到 {trip_path}")

    try:
        page = fetch(SOURCE)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        fail(f"抓不到 {SOURCE}：{exc}")

    parsed = parse(page)
    month, day = parsed.pop("_md")

    today = datetime.now(JST)
    if (month, day) != (today.month, today.day):
        # 官網每早 8 點左右才更新，偶爾會慢；不是錯誤，但也不能拿舊資料當今天的
        print(
            f"⏭  略過不寫：官網顯示 {month}/{day}，今天（JST）是 "
            f"{today.month}/{today.day}"
        )
        return

    data = json.loads(trip_path.read_text(encoding="utf-8"))
    item = find_item(data)
    if item is None:
        fail(f"trip.json 裡找不到 name 含「{ITEM_KEYWORD}」的 item")

    if item.get("live_status") == parsed:
        print(f"＝ 內容無變化（{parsed['date_label']} {parsed['status']}），不寫入")
        return

    item["live_status"] = parsed
    trip_path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"✅ 已更新 {trip_path}：{parsed['date_label']} {parsed['status']}")
    print(parsed["detail"])


if __name__ == "__main__":
    main()
