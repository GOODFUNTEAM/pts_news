"""
即時新聞推播主腳本
功能：多來源整合(公視+報導者) / 相似新聞合併 / Gemini AI 摘要 /
      重大新聞(即時、快訊)優先通知 / 靜音時段(00:00-07:00不推播，順延)
"""

from news_common import (
    load_json_set, save_json_set, fetch_all_items, group_similar_items,
    ai_summarize, send_ntfy, is_urgent, is_quiet_hours, append_daily_log,
    SENT_LINKS_FILE, MAX_PUSH_PER_RUN, MAX_AGE_HOURS, now_taipei,
)
from datetime import timedelta


def main():
    if is_quiet_hours():
        print(f"目前是靜音時段(台北時間 {now_taipei().strftime('%H:%M')})，本次不推播，"
              f"新文章會保留到靜音結束後自動補推")
        return

    sent_links = load_json_set(SENT_LINKS_FILE)
    all_items = fetch_all_items()

    # 用 UTC 比較，避免時區混淆
    from datetime import timezone
    cutoff_utc = now_taipei().astimezone(timezone.utc) - timedelta(hours=MAX_AGE_HOURS)

    def is_recent(item):
        return item["published"] is None or item["published"] >= cutoff_utc

    new_items = [
        item for item in all_items
        if item["link"] not in sent_links and is_recent(item)
    ]

    if not new_items:
        print("沒有新文章，本次不推播")
        return

    groups = group_similar_items(new_items)
    # 重大新聞優先：把含「即時／快訊」關鍵字的組別排到前面
    groups.sort(key=lambda g: not any(is_urgent(it) for it in g))
    groups = groups[:MAX_PUSH_PER_RUN]

    pushed = 0
    for group in groups:
        primary = group[0]
        urgent = any(is_urgent(it) for it in group)
        sources = sorted(set(it["source"] for it in group))

        title = primary["title"]
        if len(sources) > 1:
            title += f"（{'/'.join(sources)} 同步報導）"
        if urgent:
            title = "🚨 " + title

        summary = ai_summarize(group)

        send_ntfy(title=title, body=summary, click_link=primary["link"], urgent=urgent)

        for it in group:
            sent_links.add(it["link"])
        append_daily_log(primary)
        pushed += 1

    save_json_set(SENT_LINKS_FILE, sent_links)
    print(f"已推播 {pushed} 則新聞（合併自 {len(new_items)} 篇原始文章）")


if __name__ == "__main__":
    main()
