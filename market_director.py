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

# 聚焦半導體、AI 算力、美股財報與總經政策的高純度 RSS
RSS_SOURCES = {
    "CNBC 科技半導體": (
        "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=19854910"
    ),
    "CNBC 全球市場焦點": (
        "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=100003114"
    ),
    "鉅亨網 美股頭條": "https://news.cnyes.com/api/v1/news/xml/headline",
    "Yahoo 國際財經焦點": "https://finance.yahoo.com/news/rssindex",
}

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


def clean_html(raw_html: str) -> str:
  return html.unescape(re.sub(r"<.*?>", "", raw_html)).strip()


def fetch_latest_news() -> str:
  collected = []
  headers = {
      "User-Agent": (
          "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
          " (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
      )
  }
  now_utc = datetime.datetime.now(datetime.timezone.utc)
  max_age_seconds = 36 * 3600

  for source_name, base_url in RSS_SOURCES.items():
    try:
      feed = feedparser.parse(
          f"{base_url}?_t={int(time.time())}", request_headers=headers
      )
      valid_count = 0
      for entry in feed.entries[:8]:
        title = clean_html(entry.get("title", ""))
        summary = clean_html(entry.get("summary", ""))[:140]
        parsed = entry.get("published_parsed")

        pub_str = "即時"
        if parsed:
          entry_time = datetime.datetime(*parsed[:6], tzinfo=datetime.timezone.utc)
          if (now_utc - entry_time).total_seconds() > max_age_seconds:
            continue
          pub_str = entry_time.strftime("%m-%d %H:%M")

        if title:
          line = f"• [{source_name}] ({pub_str}) {title}"
          if summary:
            line += f" ｜ 內文摘要: {summary}"
          collected.append(line)
          valid_count += 1
      logger.info(f"來源 [{source_name}] 擷取 {valid_count} 則外電")
    except Exception as e:
      logger.warning(f"抓取 {source_name} 異常: {e}")

  return "\n".join(collected) if collected else "暫未取得有效外電。"


def analyze_with_groq(news_context: str) -> str:
  if not GROQ_API_KEY:
    return "⚠️ 未設定 GROQ_API_KEY。"
  if "暫未取得" in news_context:
    return "⚠️ 外電抓取失敗，為免模型幻覺已中斷。"

  client = Groq(api_key=GROQ_API_KEY)
  today_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")

  system_prompt = (
      f"你是一位頂尖科技避險基金的資深策略長，專精全球晶圓代工、先進封裝、AI伺服器零組件與總經市場連動。\n"
      f"基準時間：{today_str}。\n"
      "【輸出鐵律】：\n"
      "1. 全程使用「正體中文（台灣財經與半導體用語）」，除英文代號（如 NVDA、TSMC、CoWoS、CPO、HBM、ASIC）外嚴禁英文語句。\n"
      "2. 嚴禁空泛總結，每一項分析都必須明確指出「實質受惠/受害的供應鏈次產業」與「指標個股代號（美股/台股）」。\n"
      "3. 嚴格基於所附外電推論，直接輸出報告正文，不要加入任何開場白或推理草稿。"
  )

  user_prompt = f"""
基準時間：{today_str}

以下是過去 24 小時篩選的即時科技與總經外電：
{news_context}

請從上述外電中，挑選 4~5 則對「美股科技股、台股供應鏈、日韓半導體」具備「實質交易與定價影響力」的核心事件，產出一份具備實戰指引價值的操盤風向球。

格式規範：
全球市場風向球 - 晨間操盤風向球 ({today_str})

針對每則事件，依照下列結構輸出：
◆【事件核心動態】：一句話精準點出外電核心事實。
◆【實質連動鏈條】：美股（核心個股代號） ➔ 台灣/日韓直接受惠/受害環節（明確指出製程、零組件，如 CoWoS 封裝設備、水冷散熱、伺服器滑軌、HBM 等）。
◆【衝擊評級與多空】：衝擊指數 (1~10 分) ｜ 【強力偏多 / 偏多 / 中性 / 偏空 / 強力偏空】
◆【具體操作指引】：
  - 核心觀察標的：明確點名 2~3 檔美股或台股指標代號（例如：NVDA、台積電 2330、奇鋐 3017、廣達 2382）。
  - 進出場/風控思維：一句話指出關鍵防守均線、位階防守或避險建議。

──────────────────────
【今日核心操盤方針】
1. 資金輪動觀察：（一句話點出目前資金在大盤權值 vs 中小型題材股的偏向）
2. 持股水位與風控建議：（明確給出建議持股水位百分比，如 50%、70% 與防守重點）
3. 當日盤面關鍵防守線：（提出大盤或台指期的短線判斷思維）

請直接輸出繁體中文報告，不要包含任何開場白。
"""

  # 優先採用深度推理旗艦模型（無 think 標籤干擾）
  preferred_models = [
      "openai/gpt-oss-120b",
      "openai/gpt-oss-20b",
      "llama-3.1-8b-instant",
  ]

  target_models = []
  try:
    blocked = [
        "guard",
        "whisper",
        "embed",
        "safeguard",
        "canopylabs",
        "allam",
    ]
    online = [
        m.id
        for m in client.models.list().data
        if not any(b in m.id.lower() for b in blocked)
    ]
    for m in preferred_models:
      if m in online:
        target_models.append(m)
    for m in online:
      if m not in target_models:
        target_models.append(m)
  except Exception:
    target_models = preferred_models

  for model_name in target_models:
    try:
      logger.info(f"使用模型 [{model_name}] 生成深度實戰分析...")
      res = client.chat.completions.create(
          messages=[
              {"role": "system", "content": system_prompt},
              {"role": "user", "content": user_prompt},
          ],
          model=model_name,
          temperature=0.2,
          max_tokens=1500,
      )
      content = res.choices[0].message.content
      # 雙重過濾：確保任何潛在思考標籤都不會外漏
      content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL)
      if "<think>" in content:
        content = re.sub(r"<think>.*", "", content, flags=re.DOTALL)
      return content.strip()
    except Exception as e:
      logger.warning(f"模型 [{model_name}] 異常: {e}")
      time.sleep(1)

  return "⚠️ 報告生成失敗，請確認 API 連線狀態。"


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
        logger.error(f"Telegram 發送失敗: {res.text}")
    except Exception as e:
      logger.error(f"Telegram 連線異常: {e}")


if __name__ == "__main__":
  logger.info("啟動晨間新聞分析任務...")
  news = fetch_latest_news()
  report = analyze_with_groq(news)
  send_telegram(report)
  logger.info("推播完成，排程結束。")
