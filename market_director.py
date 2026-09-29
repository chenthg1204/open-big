import datetime
import html
import logging
import os
import re
import time
import feedparser
from groq import Groq
import requests

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


def clean_html(raw_html: str) -> str:
  return html.unescape(re.sub(r"<.*?>", "", raw_html)).strip()


def fetch_latest_news() -> str:
  """抓取半導體科技、美債殖利率、中東地緣與原油航運之過去 18 小時最新外電"""
  collected = []
  headers = {
      "User-Agent": (
          "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
          " (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
      ),
      "Accept": "application/json, application/xml, text/xml, */*",
  }

  now_utc = datetime.datetime.now(datetime.timezone.utc)
  max_age_seconds = 18 * 3600

  # 1. 鉅亨網即時快訊 API (提高上限至 20 則，涵蓋總經、美債與台美即時公告)
  try:
    cnyes_api = f"https://news.cnyes.com/api/v1/news?limit=20&_t={int(time.time())}"
    res = requests.get(cnyes_api, headers=headers, timeout=10)
    if res.status_code == 200:
      items = res.json().get("items", {}).get("data", [])
      cnyes_count = 0
      for item in items:
        pub_at = item.get("publishAt", 0)
        entry_time = datetime.datetime.fromtimestamp(
            pub_at, tz=datetime.timezone.utc
        )
        if (now_utc - entry_time).total_seconds() <= max_age_seconds:
          title = clean_html(item.get("title", ""))
          summary = clean_html(item.get("summary", ""))[:130]
          pub_str = entry_time.strftime("%m-%d %H:%M")
          collected.append(
              f"• [鉅亨即時快訊] ({pub_str}) {title} ｜ 摘要: {summary}"
          )
          cnyes_count += 1
      logger.info(f"鉅亨即時 API 成功取得 {cnyes_count} 則外電")
  except Exception as e:
    logger.warning(f"鉅亨 API 讀取異常: {e}")

  # 2. 針對「半導體、美債殖利率、中東地緣與能源」專屬高頻 RSS
  rss_sources = {
      "CNBC 科技半導體": (
          "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=19854910"
      ),
      "CNBC 美債與總經利率": (
          "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=20910258"
      ),
      "CNBC 全球市場焦點": (
          "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=100003114"
      ),
      "CNBC 中東地緣與能源政治": (
          "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=10000115"
      ),
      "Investing 國際股市與總經": "https://tw.investing.com/rss/news_285.rss",
  }

  for source_name, base_url in rss_sources.items():
    try:
      sep = "&" if "?" in base_url else "?"
      feed_url = f"{base_url}{sep}_t={int(time.time())}"
      res = requests.get(feed_url, headers=headers, timeout=10)
      if res.status_code != 200:
        continue

      feed = feedparser.parse(res.content)
      valid_count = 0
      for entry in feed.entries[:8]:
        title = clean_html(entry.get("title", ""))
        summary = clean_html(entry.get("summary", ""))[:140]
        parsed = entry.get("published_parsed")

        if parsed:
          entry_time = datetime.datetime(
              *parsed[:6], tzinfo=datetime.timezone.utc
          )
          if (now_utc - entry_time).total_seconds() > max_age_seconds:
            continue
          pub_str = entry_time.strftime("%m-%d %H:%M")
        else:
          pub_str = "最新"

        if title:
          line = f"• [{source_name}] ({pub_str}) {title}"
          if summary:
            line += f" ｜ 摘要: {summary}"
          collected.append(line)
          valid_count += 1
      logger.info(f"[{source_name}] 擷取到 {valid_count} 則即時外電")
    except Exception as e:
      logger.warning(f"抓取 {source_name} 異常: {e}")

  logger.info(f"總計篩選出 {len(collected)} 則最新即時外電")
  return (
      "\n".join(collected)
      if collected
      else "暫無 18 小時內之最新市場重大外電。"
  )


def analyze_with_groq(news_context: str) -> str:
  if not GROQ_API_KEY:
    return "⚠️ 未設定 GROQ_API_KEY。"
  if "暫無" in news_context:
    return "⚠️ 過去 18 小時內未偵測到符合時效之即時外電，為杜絕舊聞與 AI 幻覺，本輪暫停推播。"

  client = Groq(api_key=GROQ_API_KEY)
  today_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")

  system_prompt = (
      f"你是一位管理百億資產的頂尖總經與科技對沖基金投資總監。當前時間：{today_str}。\n"
      "【核心職責】：整合「全球半導體AI算力鏈」、「美國公債殖利率與利率預期」、「中東地緣政治與原油航運」三大維度，評估對當日台股、美股盤面之實質資金衝擊。\n"
      "【輸出鐵律】：\n"
      "1. 全程使用繁體中文（台灣財經操盤術語），嚴禁英文語句（專有名詞 TSMC, NVDA, CoWoS, HBM, CPO, ASIC, US10Y 等除外）。\n"
      "2. 實戰交易導向：嚴禁空泛陳述！針對中東地緣需點名原油/航運/塑化影響；針對美債需點名高估值科技股壓力與防守債券ETF；針對半導體需點名具體製程與個股代號。\n"
      "3. 絕對嚴禁輸出 <think> 或推理草稿，開頭直接輸出標題。"
  )

  user_prompt = f"""
基準時間：{today_str}

以下是過去 18 小時內最新發布的外電清單（含半導體、美債利率、中東地緣與能源）：
{news_context}

請從上述外電中，挑選 5 則對今日盤面（美股、台股、日韓市場）具備「實質交易與定價權」的核心重大事件（請平衡涵蓋半導體產業鏈、美債殖利率變動、中東地緣原油局勢）：

報告格式規範：
全球市場風向球 - 即時晨間操盤風向球 ({today_str})

針對每則事件，依照下列結構輸出：
◆【最新核心事件】：一句話精準摘要最新外電事實（指明時間與核心數據/動態）。
◆【實質連動鏈條】：核心事件/美債/地緣 ➔ 資本市場傳導路徑（如：美債殖利率跳升 ➔ 壓抑高估值科技成長股；中東衝突升溫 ➔ 推升原油運價，衝擊航空航運與塑化原料）。
◆【多空評級與衝擊】：衝擊指數 (1~10 分) ｜ 【強力偏多 / 偏多 / 中性 / 偏空 / 強力偏空】
◆【短線操盤與個股指引】：
  - 核心觀察標的：明確點名 2~3 檔美股或台股指標代號（例如：NVDA、台積電 2330、長榮 2603、元大美債20年 00679B、奇鋐 3017 等）。
  - 風控與進出場思維：一句話指出短期關鍵支撐/均線防守、避險思維或原物料波段應對。

──────────────────────
【今日核心操盤方針】
1. 總經與地緣風險評估：（簡述中東局勢與美債殖利率對當前資金成本的實質壓制或推升）
2. 盤面資金流向觀察：（一句話點出資金在大盤權值、AI硬體族群與防禦型/避險題材股的分佈）
3. 建議持股水位與關鍵防守線：（明確給出建議持股水位百分比，如 50%、70% 與台股/台指期短線防守重點）

請直接輸出繁體中文報告，不要包含任何開場白。
"""

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
      logger.info(f"使用模型 [{model_name}] 解讀即時最新外電...")
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
        logger.info("Telegram 即時風向球推播成功！")
      else:
        logger.error(f"Telegram 發送失敗: {res.text}")
    except Exception as e:
      logger.error(f"Telegram 連線異常: {e}")


if __name__ == "__main__":
  logger.info("啟動晨間即時新聞分析任務...")
  news = fetch_latest_news()
  report = analyze_with_groq(news)
  send_telegram(report)
  logger.info("推播完成，排程結束。")
