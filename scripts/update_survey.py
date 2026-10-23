#!/usr/bin/env python3
"""抓旅後回饋問卷的回覆，寫成 survey_responses.json。

問卷本體在手冊最底下（trip.json 的 survey），送出時直接寫進 Google 表單；
表單連結的試算表設成「知道連結可檢視」，這裡用它的 CSV 匯出網址抓回來。

網址放在環境變數 SURVEY_CSV_URL（GitHub Secret），不寫進 repo：
    https://docs.google.com/spreadsheets/d/<試算表ID>/export?format=csv&gid=<回應分頁gid>

只用 Python 標準庫。網址沒設或抓失敗就印訊息、退出碼 1，不動既有檔案。
"""
import csv
import io
import json
import os
import sys
import urllib.error
import urllib.request


def main(out_path):
    url = os.environ.get("SURVEY_CSV_URL", "").strip()
    if not url:
        print("沒有 SURVEY_CSV_URL，略過問卷")
        return 1
    req = urllib.request.Request(url, headers={"User-Agent": "kyushu-handbook"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            body = r.read().decode("utf-8-sig")
    except urllib.error.HTTPError as e:
        # 401/403：試算表沒開「知道連結可檢視」
        print(f"抓試算表失敗 HTTP {e.code}：確認試算表設成「知道連結的使用者可檢視」")
        return 1
    # 試算表沒開分享時 Google 會回登入頁（HTML），不是 CSV
    if body.lstrip().startswith("<"):
        print("拿到的是 HTML 不是 CSV：試算表可能沒設成「知道連結可檢視」")
        return 1

    rows = list(csv.reader(io.StringIO(body)))
    if not rows:
        print("CSV 是空的")
        return 1
    header, data = rows[0], rows[1:]
    responses = [
        {h: v for h, v in zip(header, row) if v.strip()}
        for row in data
        if any(c.strip() for c in row)
    ]

    doc = {"count": len(responses), "responses": responses}
    new = json.dumps(doc, ensure_ascii=False, indent=2) + "\n"
    try:
        old = open(out_path, encoding="utf-8").read()
    except FileNotFoundError:
        old = None
    if new == old:
        print(f"問卷沒有新回覆（共 {len(responses)} 份）")
        return 0
    with open(out_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(new)
    print(f"問卷回覆更新：共 {len(responses)} 份")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "survey_responses.json"))
