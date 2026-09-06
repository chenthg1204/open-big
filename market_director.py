import datetime
import html
import logging
import os
import re
import time
import feedparser
import requests
from groq import Groq

# 從環境變數安全讀取金鑰
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_OPENBIG_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")

RSS_SOURCES = {
    "鉅亨網 即時頭條": "https://news.cnyes.com/api/v1/news/xml/headline",
    "WSJ 國際市場即時": "https://feeds.a.dj.com/rss/RSSMarketsMain.xml",
    "MarketWatch 即時外電": "https://feeds.content.dowjones.io/public/rss/mw_topstories",
    "CNBC 財經即時": "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=10000664",
}

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
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
                    entry_time = datetime.datetime(*published_parsed[:6], tzinfo=datetime.timezone.utc)
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

    return "\n".join(collected_articles) if collected_articles else "暫未取得 24 小時內之最新即時外電。"

def analyze_with_groq(news_context: str) -> str:
    if not GROQ_API_KEY:
        return "⚠️ 未設定 GROQ_API_KEY。"
    if "暫未取得" in news_context:
        return "⚠️ 未抓取到即時外電，為避免模型幻覺已中斷生成。"

    client = Groq(api_key=GROQ_API_KEY)
    today_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")

    system_prompt = (
        f"你是一位專精全球半導體與總經市場的資深策略師。當前時間為 {today_str}。"
        "【重要準則】你必須完全嚴格基於下方提供的即時外電清單進行分析，絕對嚴禁捏造任何歷史事件。"
    )

    user_prompt = f"""
當前基準時間：{today_str}

以下是過去 24 小時內篩選的真實外電：
{news_context}

請從上述外電中，挑選對「美股、台股、日股、韓股」具備實質市場連動的關鍵事件，進行多空影響與產業鏈分析：

格式規範：
1. 標題：全球市場風向球 - 晨間市場風向球晨報 ({today_str})
2. 每個事件依序呈現：
   - 【事件核心動態】：一句話陳述真實外電事實。
   - 【主要影響市場】：美股 / 台股 / 日股 / 韓股（指明實質受影響之供應鏈）。
   - 【市場衝擊指數】：1~10 分。
   - 【多空方向】：強力偏多 / 偏多 / 中性 / 偏空 / 強力偏空。
   - 【實質因應對策】：指明具體族群（如先進製程封裝、高階散熱、特定原物料等）或避險策略。
3. 文末附上「今日核心操作方針」（精簡 3 點）。

請使用繁體中文，格式清晰整齊。
"""

    candidate_models = ["llama-3.3-70b-versatile", "llama-3.1-8b-instant"]
    for model_name in candidate_models:
        try:
            logger.info(f"使用模型 [{model_name}] 生成報告...")
            res = client.chat.completions.create(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                model=model_name,
                temperature=0.2,
                max_tokens=2500,
            )
            return res.choices[0].message.content
        except Exception as e:
            logger.warning(f"模型 [{model_name}] 異常: {e}")
            time.sleep(0.5)

    return "⚠️ 報告生成失敗，請檢查 API。"

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
        requests.post(url, json=payload, timeout=15)

if __name__ == "__main__":
    logger.info("啟動晨間新聞分析任務...")
    news = fetch_latest_news()
    report = analyze_with_groq(news)
    send_telegram(report)
    logger.info("推播完成，排程結束。")
