"""
共用模組：多來源新聞抓取、Gemini AI 摘要、ntfy 推播、狀態存取
"""

import html
import json
import os
import re
import time
import difflib
from datetime import datetime, timezone, timedelta

import requests
import feedparser

try:
    from zoneinfo import ZoneInfo
except ImportError:  # Python < 3.9 備用
    ZoneInfo = None

# ---------- 基本設定 ----------

SOURCES = [
    {"name": "公視新聞", "url": "https://news.pts.org.tw/xml/newsfeed.xml"},
    {"name": "報導者", "url": "https://www.twreporter.org/a/rss2.xml"},
]

NTFY_TOPIC = "pts-news"
NTFY_URL = f"https://ntfy.sh/{NTFY_TOPIC}"

MAX_ITEMS_PER_SOURCE = 20
MAX_PUSH_PER_RUN = 8
MAX_AGE_HOURS = 24
SIMILARITY_THRESHOLD = 0.55  # 標題相似度達此值視為同一事件，合併推播

URGENT_KEYWORDS = ["即時", "快訊"]

SENT_LINKS_FILE = "sent_links.json"
DAILY_LOG_FILE = "daily_log.json"
MAX_HISTORY = 400

TAIPEI_TZ = ZoneInfo("Asia/Taipei") if ZoneInfo else timezone(timedelta(hours=8))

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    "gemini-2.5-flash:generateContent"
)


# ---------- 工具函式 ----------

def clean_html(raw_html: str) -> str:
    text = re.sub(r"<[^>]+>", "", raw_html or "")
    return html.unescape(text).strip()


def now_taipei():
    return datetime.now(timezone.utc).astimezone(TAIPEI_TZ)


def is_quiet_hours() -> bool:
    """台北時間 00:00-07:00 視為靜音時段"""
    hour = now_taipei().hour
    return 0 <= hour < 7


def parse_published(entry):
    for key in ("published_parsed", "updated_parsed"):
        struct = entry.get(key)
        if struct:
            return datetime.fromtimestamp(time.mktime(struct), tz=timezone.utc)
    return None


def is_urgent(item) -> bool:
    text = item["title"] + item["summary"]
    return any(kw in text for kw in URGENT_KEYWORDS)


# ---------- 狀態存取 ----------

def load_json_set(path):
    if not os.path.exists(path):
        return set()
    with open(path, "r", encoding="utf-8") as f:
        try:
            return set(json.load(f))
        except json.JSONDecodeError:
            return set()


def save_json_set(path, values):
    trimmed = list(values)[-MAX_HISTORY:]
    with open(path, "w", encoding="utf-8") as f:
        json.dump(trimmed, f, ensure_ascii=False, indent=2)


def load_daily_log():
    if not os.path.exists(DAILY_LOG_FILE):
        return []
    with open(DAILY_LOG_FILE, "r", encoding="utf-8") as f:
        try:
            return json.load(f)
        except json.JSONDecodeError:
            return []


def save_daily_log(records):
    with open(DAILY_LOG_FILE, "w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=2)


def append_daily_log(item):
    records = load_daily_log()
    records.append({
        "title": item["title"],
        "source": item["source"],
        "link": item["link"],
        "time": now_taipei().isoformat(),
    })
    save_daily_log(records)


# ---------- 抓取 ----------

def fetch_all_items():
    items = []
    for src in SOURCES:
        feed = feedparser.parse(src["url"])
        for entry in feed.entries[:MAX_ITEMS_PER_SOURCE]:
            title = clean_html(entry.get("title", ""))
            summary = clean_html(entry.get("summary", "") or entry.get("description", ""))
            link = entry.get("link", "")
            published = parse_published(entry)
            items.append({
                "title": title,
                "summary": summary,
                "link": link,
                "source": src["name"],
                "published": published,
            })
    return items


# ---------- 相似新聞分組 ----------

def group_similar_items(items):
    """把標題相似的文章（不同媒體報同一事件）合併成一組"""
    groups = []
    used = [False] * len(items)
    for i, item in enumerate(items):
        if used[i]:
            continue
        group = [item]
        used[i] = True
        for j in range(i + 1, len(items)):
            if used[j]:
                continue
            ratio = difflib.SequenceMatcher(None, item["title"], items[j]["title"]).ratio()
            if ratio >= SIMILARITY_THRESHOLD:
                group.append(items[j])
                used[j] = True
        groups.append(group)
    return groups


# ---------- Gemini AI 摘要 ----------

def ai_summarize(group) -> str:
    """
    用 Gemini API 針對一組(可能來自多家媒體的同一事件)文章寫一段摘要。
    若沒有 API Key 或呼叫失敗，退回使用 RSS 原始描述拼接。
    """
    fallback = " ".join(dict.fromkeys(it["summary"] for it in group if it["summary"]))[:60] or "(無摘要)"

    if not GEMINI_API_KEY:
        return fallback

    sources_text = "\n".join(
        f"[{it['source']}] 標題：{it['title']}\n內容：{it['summary']}"
        for it in group
    )
    prompt = (
        "你是新聞編輯。請根據以下同一則新聞事件、來自不同媒體的報導內容，"
        "用繁體中文寫一段30到40字的中立客觀摘要，只根據提供的內容摘寫，"
        "不要加入未提及的資訊，不要條列，不要加標點以外的格式，直接輸出摘要文字本身：\n\n"
        f"{sources_text}"
    )

    try:
        resp = requests.post(
            f"{GEMINI_URL}?key={GEMINI_API_KEY}",
            json={
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {"maxOutputTokens": 100},
            },
            timeout=20,
        )
        resp.raise_for_status()
        data = resp.json()
        text = data["candidates"][0]["content"]["parts"][0]["text"].strip()
        if len(text) > 60:  # 保險截斷，避免模型沒遵守字數限制
            text = text[:60] + "…"
        return text if text else fallback
    except Exception as e:
        print(f"Gemini 摘要失敗，改用原始描述：{e}")
        return fallback


# ---------- ntfy 推播 ----------

def send_ntfy(title: str, body: str, click_link: str, urgent: bool = False):
    headers = {
        "Title": title.encode("utf-8"),
        "Click": click_link,
        "Tags": ("rotating_light,newspaper" if urgent else "newspaper"),
    }
    if urgent:
        headers["Priority"] = "urgent"

    resp = requests.post(NTFY_URL, data=body.encode("utf-8"), headers=headers)
    resp.raise_for_status()
