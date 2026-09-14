import datetime
import html
import logging
import os
import re
import time
import feedparser
from groq import Groq
import requests

# 讀取 GitHub Secrets 環境變數
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")

RSS_SOURCES = {
    "鉅亨網 即時頭條": "https://news.cnyes.com/api/v1/news/xml/headline",
    "WSJ 國際市場即時": "https://feeds.a.dj.com/rss/RSSMarketsMain.xml",
    "MarketWatch 即時外電": (
        "https://feeds.content.dowjones.io/public/rss/mw_topstories"
    ),
    "CNBC 財經即時": (
        "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=10000664"
    ),
}

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


def clean_html(raw_html: str) -> str:
    return html.unescape(re.sub(r"<.*?>", "", raw_html)).strip()


def fetch_latest_news() -> str:
    collected_articles = []
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    now_utc = datetime.datetime.now(datetime.timezone.utc)
    max_age_seconds = 24 * 3600

    for source_name, base_url in RSS_SOURCES.items():
        try:
            cache_bust_url = f"{base_url}?_t={int(time.time())}"
            feed = feedparser.parse(cache_bust_url, request_headers=headers)
            for entry in feed.entries[:8]:
                title = clean_html(entry.get("title", ""))
                summary = clean_html(entry.get("summary", ""))[:120]
                published_parsed = entry.get("published_parsed")
                if published_parsed:
                    entry_time = datetime.datetime(
                        *published_parsed[:6], tzinfo=datetime.timezone.utc
                    )
                    if (now_utc - entry_time).total_seconds() > max_age_seconds:
                        continue
                    pub_str = entry_time.strftime("%m-%d %H:%M")
                else:
                    pub_str = "即時"

                if title:
                    line = f"• [{source_name}] ({pub_str}) {title}"
                    if summary:
                        line += f" ｜ 摘要: {summary}"
                    collected_articles.append(line)
        except Exception as e:
            logger.warning(f"抓取 {source_name} 異常: {e}")

    return (
        "\n".join(collected_articles)
        if collected_articles
        else "暫未取得 24 小時內之最新即時外電。"
    )


def analyze_with_groq(news_context: str) -> str:
    if not GROQ_API_KEY:
        return "⚠️ 未設定 GROQ_API_KEY。"
    if "暫未取得" in news_context:
        return "⚠️ 未抓取到即時外電，為避免模型幻覺已中斷生成。"

    client = Groq(api_key=GROQ_API_KEY)
    today_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")

    system_prompt = (
        f"你是一位專精全球半導體與總經市場的資深策略師。當前時間為 {today_str}。\n"
        "【語言規範】全程必須使用「繁體中文（台灣財經用語）」撰寫，"
        "嚴禁輸出任何英文段落或分析（公司代號如 TSMC、NVDA、ASML、HBM 等專有名詞除外）。\n"
        "【嚴謹求實】必須完全根據下方提供的 24 小時即時外電進行推論，嚴禁無中生有捏造歷史或過期事件。"
    )

    user_prompt = f"""
基準時間：{today_str}

以下是過去 24 小時內篩選的即時外電：
{news_context}

請閱讀上述外電，挑選 5~7 則對「美股、台股、日股、韓股」具備實質連動影響力的關鍵事件，進行多空評估與供應鏈連動解析。

請嚴格依照以下格式以「繁體中文」輸出：
1. 標題：全球市場風向球 - 晨間市場風向球晨報 ({today_str})
2. 條列式列出事件，每個事件格式：
   - 【事件核心動態】：一句話陳述外電真實動態。
   - 【主要影響市場】：美股 / 台股 / 日股 / 韓股（指明具體受影響的供應鏈環節）。
   - 【市場衝擊指數】：1~10 分。
   - 【多空方向】：強力偏多 / 偏多 / 中性 / 偏空 / 強力偏空。
   - 【實質因應對策】：指明具體看好/承壓族群（如 CoWoS 設備、先進製程耗材、高階液冷散熱、伺服器代工等）或避險思維。
3. 文末附上「今日核心操作方針」（精簡列出 3 點盤面重點，全繁體中文）。

請直接輸出報告本體，不要加入任何開場白或額外寒暄。
"""

    # 優先指定的繁中生成主力模型池（嚴格過濾特殊模型）
    preferred_models = [
        "qwen/qwen3.6-27b",
        "llama-3.1-8b-instant",
    ]

    target_models = []
    try:
        # 排除語音、安全防護、嵌入以及阿拉伯語特化等非通用模型
        blocked_keywords = [
            "guard",
            "whisper",
            "embed",
            "safeguard",
            "canopylabs",
            "allam",
        ]
        online_chat_models = [
            m.id
            for m in client.models.list().data
            if not any(bad in m.id.lower() for bad in blocked_keywords)
        ]

        for m in preferred_models:
            if m in online_chat_models:
                target_models.append(m)

        for m in online_chat_models:
            if m not in target_models:
                target_models.append(m)
    except Exception as e:
        logger.warning(f"動態獲取模型清單異常，使用預設配置: {e}")
        target_models = preferred_models

    for model_name in target_models:
        try:
            logger.info(f"使用模型 [{model_name}] 生成報告...")
            # max_tokens 設為 950，完全符合 Groq 免費層 1000 OTPM 限制，徹底避免 429 報錯
            res = client.chat.completions.create(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                model=model_name,
                temperature=0.2,
                max_tokens=950,
            )
            return res.choices[0].message.content
        except Exception as e:
            logger.warning(f"模型 [{model_name}] 呼叫異常，切換至備援模型: {e}")
            time.sleep(1)

    return "⚠️ 所有主力模型生成皆未成功，請確認 API 連線狀態。"


def send_telegram(text: str):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logger.error("未設定 Telegram 金鑰，取消傳送")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    chunk_size = 3500
    for i in range(0, len(text), chunk_size):
        payload = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": text[i : i + chunk_size],
            "disable_web_page_preview": True,
        }
        try:
            res = requests.post(url, json=payload, timeout=15)
            if res.status_code == 200:
                logger.info("Telegram 訊息分段推播成功！")
            else:
                logger.error(
                    f"Telegram 推播失敗，狀態碼 {res.status_code}: {res.text}"
                )
        except Exception as e:
            logger.error(f"Telegram 連線異常: {e}")


if __name__ == "__main__":
    logger.info("啟動晨間新聞分析任務...")
    news = fetch_latest_news()
    report = analyze_with_groq(news)
    send_telegram(report)
    logger.info("推播完成，排程結束。")
