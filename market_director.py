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

    # Groq 最強旗艦模型優先順序表
    preferred_models = [
        "llama-3.3-70b-versatile",   # 最強 70B 旗艦，推論與繁中解析最佳
        "llama-3.1-70b-versatile",   # 次選 70B 旗艦
        "qwen-2.5-32b",              # 亞洲市場、中文語境表現頂級
        "mixtral-8x7b-32768",        # 長上下文混合專家模型
        "llama-3.1-8b-instant"       # 極速備援輕量模型
    ]

    # 動態比對你當前帳號有權限使用的模型
    target_models = []
    try:
        online_models = [m.id for m in client.models.list().data if not m.id.startswith("whisper")]
        for m in preferred_models:
            if m in online_models:
                target_models.append(m)
        # 若清單都沒對應到，直接取線上第一款可用文字模型
        if not target_models and online_models:
            target_models = [online_models[0]]
    except Exception as e:
        logger.warning(f"動態獲取模型清單異常，直接使用預設順序: {e}")
        target_models = preferred_models

    for model_name in target_models:
        try:
            logger.info(f"使用最強模型 [{model_name}] 生成報告...")
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
            logger.warning(f"模型 [{model_name}] 異常，嘗試切換下一款: {e}")
            time.sleep(0.5)

    return "⚠️ 所有旗艦模型生成皆失敗，請檢查 API 金鑰與連線配額。"