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

# 篩選對雲端伺服器（GitHub Actions）友善且絕不擋 IP 的財經與半導體新聞來源
RSS_SOURCES = {
    "CNBC 全球市場焦點": (
        "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=100003114"
    ),
    "CNBC 科技半導體": (
        "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=19854910"
    ),
    "Yahoo 國際財經即時": (
        "https://finance.yahoo.com/news/rssindex"
    ),
    "BBC 商業與全球經濟": (
        "http://feeds.bbci.co.uk/news/business/rss.xml"
    ),
    "鉅亨網 頭條新聞": (
        "https://news.cnyes.com/api/v1/news/xml/headline"
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
  headers = {
      "User-Agent": (
          "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
          " (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
      )
  }
  now_utc = datetime.datetime.now(datetime.timezone.utc)
  max_age_seconds = 36 * 3600  # 放寬至 36 小時以容納美股收盤至隔日清晨跨時區差異

  for source_name, base_url in RSS_SOURCES.items():
    try:
      cache_bust_url = f"{base_url}?_t={int(time.time())}"
      feed = feedparser.parse(cache_bust_url, request_headers=headers)
      
      valid_in_source = 0
      for entry in feed.entries[:6]:
        title = clean_html(entry.get("title", ""))
        summary = clean_html(entry.get("summary", ""))[:120]
        published_parsed = entry.get("published_parsed")

        pub_str = "即時"
        if published_parsed:
          entry_time = datetime.datetime(
              *published_parsed[:6], tzinfo=datetime.timezone.utc
          )
          # 超過 36 小時的舊文章跳過
          if (now_utc - entry_time).total_seconds() > max_age_seconds:
            continue
          pub_str = entry_time.strftime("%m-%d %H:%M")

        if title:
          line = f"• [{source_name}] ({pub_str}) {title}"
          if summary:
            line += f" ｜ 摘要: {summary}"
          collected_articles.append(line)
          valid_in_source += 1

      logger.info(f"來源 [{source_name}] 成功擷取 {valid_in_source} 則外電")
    except Exception as e:
      logger.warning(f"抓取 {source_name} 異常: {e}")

  logger.info(f"總共累計有效外電：{len(collected_articles)} 則")
  return (
      "\n".join(collected_articles)
      if collected_articles
      else "暫未取得有效即時外電。"
  )


def analyze_with_groq(news_context: str) -> str:
  if not GROQ_API_KEY:
    return "⚠️ 未設定 GROQ_API_KEY。"
  if "暫未取得" in news_context:
    return "⚠️ 外部新聞來源連線受限，未抓取到有效外電。"

  client = Groq(api_key=GROQ_API_KEY)
  today_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")

  system_prompt = (
      f"你是一位專精全球半導體、AI 算力基礎設施與總經市場的資深策略師。當前時間為 {today_str}。\n"
      "【語言鐵律】全程必須使用「繁體中文（台灣財經習慣用語）」撰寫，"
      "嚴禁輸出任何英文段落或英文解說（公司代號如 TSMC、NVDA、ASML、CoWoS、HBM 等專有名詞除外）。\n"
      "【嚴禁思維鏈】直接輸出最終報告本體，絕對不要輸出 <think> 標籤或任何思考過程。\n"
      "【嚴謹求實】必須完全依據下方提供的外電進行多空研判，嚴禁憑空編造不實歷史事件。"
  )

  user_prompt = f"""
基準時間：{today_str}

以下是剛抓取到的即時國際財經外電：
{news_context}

請閱讀上述外電，挑選 5~7 則對「美股、台股、日股、韓股」具備實質市場連動影響力的關鍵事件，進行多空評估與供應鏈連動解析。

請嚴格依照以下格式以「繁體中文」輸出：
1. 標題：全球市場風向球 - 晨間市場風向球晨報 ({today_str})
2. 條列式列出事件，每個事件格式：
   - 【事件核心動態】：一句話陳述外電真實動態。
   - 【主要影響市場】：美股 / 台股 / 日股 / 韓股（指明具體受影響的供應鏈環節）。
   - 【市場衝擊指數】：1~10 分。
   - 【多空方向】：強力偏多 / 偏多 / 中性 / 偏空 / 強力偏空。
   - 【實質因應對策】：指明具體看好/承壓族群（如 CoWoS 設備、先進製程耗材、高階液冷散熱、伺服器代工等）或避險思維。
3. 文末附上「今日核心操作方針」（精簡列出 3 點盤面重點，全繁體中文）。

請直接輸出報告，不要加入任何開場白或額外寒暄。
"""

  preferred_models = [
      "qwen/qwen3.6-27b",
      "llama-3.1-8b-instant",
  ]

  target_models = []
  try:
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
      res = client.chat.completions.create(
          messages=[
              {"role": "system", "content": system_prompt},
              {"role": "user", "content": user_prompt},
          ],
          model=model_name,
          temperature=0.2,
          max_tokens=950,
      )
      raw_content = res.choices[0].message.content
      cleaned_content = re.sub(
          r"<think>.*?</think>", "", raw_content, flags=re.DOTALL
      ).strip()
      return cleaned_content
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
