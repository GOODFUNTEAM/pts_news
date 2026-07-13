"""
每日新聞總結腳本
在台北時間 22:00 執行，把當天所有已推播過的新聞標題整理成一則總覽通知。
"""

from datetime import datetime
from news_common import load_daily_log, save_daily_log, send_ntfy, now_taipei


def main():
    records = load_daily_log()
    today_str = now_taipei().strftime("%Y-%m-%d")

    today_records = [r for r in records if r["time"].startswith(today_str)]
    other_records = [r for r in records if not r["time"].startswith(today_str)]

    if not today_records:
        print("今天沒有推播過任何新聞，不送總結")
        # 仍清掉舊資料，避免檔案無限增長
        save_daily_log(other_records[-500:])
        return

    lines = [f"📋 今日新聞總覽（{today_str}）共 {len(today_records)} 則\n"]
    for i, r in enumerate(today_records, 1):
        lines.append(f"{i}. [{r['source']}] {r['title']}")

    body = "\n".join(lines)
    if len(body) > 3800:
        body = body[:3800] + "\n...(內容過長，已截斷，完整清單請見 repo 的 daily_log.json 或各則即時推播)"

    send_ntfy(
        title=f"今日新聞總覽 {today_str}",
        body=body,
        click_link="https://news.pts.org.tw/",
        urgent=False,
    )

    # 當天資料已經總結完，從記錄中移除，避免隔天重複出現、檔案無限增長
    save_daily_log(other_records[-500:])
    print(f"已送出今日總結，共 {len(today_records)} 則")


if __name__ == "__main__":
    main()
