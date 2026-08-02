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

    return {
        "date_label": f"{month}/{day}[{week}]",
        "status": status,
        "updated": updated,
        "detail": detail,
        "source": SOURCE,
        "_md": (month, day),
    }


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
