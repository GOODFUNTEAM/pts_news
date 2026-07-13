"""
公視新聞 RSS -> ntfy.sh 自動推播(去重複版本)
不需要任何帳號或 API Key。用 sent_links.json 記錄已推播過的新聞連結，
每次執行只推播「還沒推播過」的新文章。
"""

import html
import json
import os
import re
import requests
import feedparser

RSS_URL = "https://news.pts.org.tw/xml/newsfeed.xml"
MAX_ITEMS = 20          # 每次最多檢查最新幾則(避免漏抓)
MAX_PUSH_PER_RUN = 8    # 每次最多實際推播幾則，避免一次爆量通知
STATE_FILE = "sent_links.json"
MAX_HISTORY = 300       # 記錄檔最多保留幾筆，避免無限增長

NTFY_TOPIC = "pts-news"
NTFY_URL = f"https://ntfy.sh/{NTFY_TOPIC}"


def clean_html(raw_html: str) -> str:
    text = re.sub(r"<[^>]+>", "", raw_html or "")
    return html.unescape(text).strip()


def load_sent_links():
    if not os.path.exists(STATE_FILE):
        return set()
    with open(STATE_FILE, "r", encoding="utf-8") as f:
        try:
            return set(json.load(f))
        except json.JSONDecodeError:
            return set()


def save_sent_links(links):
    # 只保留最近 MAX_HISTORY 筆，避免檔案無限增長
    trimmed = list(links)[-MAX_HISTORY:]
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(trimmed, f, ensure_ascii=False, indent=2)


def fetch_news():
    feed = feedparser.parse(RSS_URL)
    items = []
    for entry in feed.entries[:MAX_ITEMS]:
        title = clean_html(entry.get("title", ""))
        summary = clean_html(entry.get("summary", "") or entry.get("description", ""))
        link = entry.get("link", "")
        items.append({"title": title, "summary": summary, "link": link})
    return items


def send_ntfy(item, index, total):
    body = item["summary"] if item["summary"] else "(無摘要)"
    resp = requests.post(
        NTFY_URL,
        data=body.encode("utf-8"),
        headers={
            "Title": f"({index}/{total}) {item['title']}".encode("utf-8"),
            "Click": item["link"],
            "Tags": "newspaper",
        },
    )
    resp.raise_for_status()


def main():
    sent_links = load_sent_links()
    all_items = fetch_news()

    new_items = [item for item in all_items if item["link"] not in sent_links]

    if not new_items:
        print("沒有新文章，本次不推播")
        return

    to_push = new_items[:MAX_PUSH_PER_RUN]
    total = len(to_push)
    for i, item in enumerate(to_push, 1):
        send_ntfy(item, i, total)
        sent_links.add(item["link"])

    save_sent_links(sent_links)
    print(f"已推播 {total} 則新文章（共發現 {len(new_items)} 則新文章）")


if __name__ == "__main__":
    main()
