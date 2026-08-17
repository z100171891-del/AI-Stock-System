"""
ai/gemini_analysis.py
------------------------
Google Gemini 分析模組。用純 REST API 呼叫（generativelanguage.googleapis.com），
不依賴 google-generativeai SDK，減少額外套件相依。

這個模組的 prompt 比其他 AI provider（openai/deepseek/groq，見 ai/base.py 的
共用 PROMPT_TEMPLATE）多一段「🔥 核心發動題材」要求（見 GEMINI_THEME_INSTRUCTION）：
目的是抓「題材股」，希望 AI 能直接點出這檔股票近期市場在炒作什麼消息面題材
（例如：AI伺服器散熱、法說會利多、運價上漲），而不是只給技術面/籌碼面的量化判讀。

這段題材摘要如果只靠語言模型憑訓練資料回答，很容易「編故事」——訓練資料有
時間差，且模型不知道「這幾天」市場實際在討論什麼，講得再肯定也可能是幻覺。
所以這裡預設會在請求裡加上 Google Search grounding 工具（見 config.py 的
GEMINI_ENABLE_GROUNDING），讓 Gemini 在回答前先搜尋，回答才有機會對應到
真實近期消息；搜尋不到明確題材時，prompt 也明確要求誠實承認查無題材，
不要編造。
"""
import logging
import time

import requests

import config
from ai.base import AIProviderError, build_prompt, extract_sentiment

logger = logging.getLogger(__name__)

# Google 會不定期淘汰舊版模型（例如 gemini-1.5-flash 已於 2025 年下架）。
# 這裡列一組候選模型，由新到舊依序嘗試，其中一個失效時能自動改用下一個，
# 避免又因為單一模型名稱過期而整個 AI 分析失效。
MODEL_CANDIDATES = ["gemini-3.5-flash", "gemini-2.5-flash", "gemini-2.0-flash"]
API_URL_TEMPLATE = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

# 同一個模型如果是被「暫時限流」（HTTP 429，通常是免費額度的每分鐘請求數上限），
# 代表模型本身是好的、金鑰也對，只是打太快，值得原地等一下重試，而不是直接
# 放棄跳去下一個候選模型（下一個候選常常是已經停用的舊模型，跳過去也只會是
# 白白浪費一次 404，並不會真的拿到 AI 分析結果）。
MAX_RETRIES_PER_MODEL = 3
RETRY_BACKOFF_SECONDS = [5, 15, 30]  # 第1次等5秒、第2次等15秒、第3次等30秒（指數退避）

# 加在共用 PROMPT_TEMPLATE 之後、只給 Gemini 用的「題材面挖掘」額外指示。
# 特別要求：(1) 獨立段落、固定開頭方便前端/使用者一眼辨識；(2) 跟後面的正式
# 分析（偏多/中性/偏空開頭）之間空一行——ai/base.py 的 extract_sentiment() 只
# 掃文字最前面20個字找情緒關鍵字，這裡刻意保留「空一行」的段落邊界，讓
# _extract_sentiment_for_gemini() 能明確切出「題材段落」之後的正式分析段落
# 再交給 extract_sentiment() 判斷，避免題材段落把情緒關鍵字擠到20字之外而
# 誤判成中性；(3) 明確要求查無題材時要誠實說，不要編造新聞。
GEMINI_THEME_INSTRUCTION = """
另外，請在你上面這份分析報告的「最前面」，額外加一個獨立段落，開頭固定寫
「🔥 核心發動題材：」，用 1-2 句話具體指出這檔股票最近市場在炒作的題材或
消息面驅動因素（例如：AI伺服器散熱、特定法說會利多、運價上漲、政策題材等），
幫助判斷是否有實質題材支撐這 1-2 週的走勢。這段話單獨成一段，跟後面「偏多／
中性／偏空」開頭的正式分析之間空一行。如果搜尋不到近期有明確的具體題材，
請誠實寫「🔥 核心發動題材：目前查無明確消息面題材，本波動能可能以技術面／
籌碼面因素為主，請留意評估基本面風險」，絕對不要編造不存在的新聞或消息。
"""


def _extract_sentiment_for_gemini(full_text: str) -> str:
    """
    ai/base.py 的 extract_sentiment() 假設「偏多/中性/偏空」會出現在整段文字
    最前面 20 個字內。Gemini 現在開頭多了一段「🔥 核心發動題材」，把原本的
    假設往後推了，直接對整段文字套用 extract_sentiment() 容易誤判成中性。

    這裡改成：找到「核心發動題材」段落的位置，取它之後、以空行分隔的下一段
    （也就是 prompt 要求的正式分析段落），只對那一段套用 extract_sentiment()，
    維持原本的判斷邏輯與準確度。找不到題材段落（例如模型沒有照格式回覆）時，
    直接退回對整段文字判斷，行為與其他 provider 一致。
    """
    marker = "核心發動題材"
    idx = full_text.find(marker)
    if idx == -1:
        return extract_sentiment(full_text)
    rest = full_text[idx:]
    parts = rest.split("\n\n", 1)
    analysis_part = parts[1] if len(parts) > 1 else rest
    return extract_sentiment(analysis_part)


def _call_model(model: str, api_key: str, prompt: str) -> dict:
    """對單一模型呼叫一次 Gemini API，成功回傳結果 dict，失敗直接拋例外給呼叫端處理。"""
    body = {"contents": [{"parts": [{"text": prompt}]}]}
    if config.GEMINI_ENABLE_GROUNDING:
        # Google Search grounding：讓模型在回答前先搜尋，「核心發動題材」段落
        # 才有機會對應到真實近期消息，而不是憑訓練資料亂猜。可用
        # .env 的 GEMINI_ENABLE_GROUNDING=false 關閉（例如想省搜尋額度）。
        body["tools"] = [{"google_search": {}}]

    # 2026 年起 Google 新核發的金鑰是 "AQ." 開頭的 Authentication Key，
    # 不能像舊版 "AIzaSy..." Key 那樣放在網址 ?key= 參數裡（會被誤判成 404/401），
    # 必須改用 x-goog-api-key 這個 HTTP Header 傳遞金鑰。
    resp = requests.post(
        API_URL_TEMPLATE.format(model=model),
        headers={
            "x-goog-api-key": api_key,
            "Content-Type": "application/json",
        },
        json=body,
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    text = data["candidates"][0]["content"]["parts"][0]["text"].strip()
    return {
        "provider": f"Gemini ({model})",
        "text": text,
        "sentiment": _extract_sentiment_for_gemini(text),
    }


def analyze(stock_id: str, context: dict) -> dict:
    api_key = config.AI_PROVIDERS.get("gemini")
    if not api_key:
        raise AIProviderError("未設定 GEMINI_API_KEY")

    prompt = build_prompt(stock_id, context) + GEMINI_THEME_INSTRUCTION
    last_error = None

    for model in MODEL_CANDIDATES:
        for attempt in range(MAX_RETRIES_PER_MODEL):
            try:
                return _call_model(model, api_key, prompt)
            except requests.exceptions.HTTPError as exc:
                last_error = exc
                status = exc.response.status_code if exc.response is not None else None
                if status == 429 and attempt < MAX_RETRIES_PER_MODEL - 1:
                    wait = RETRY_BACKOFF_SECONDS[attempt]
                    logger.warning(
                        "gemini_analysis: 模型 %s 被限流(429)，%d 秒後重試（第 %d/%d 次），"
                        "不會馬上跳去舊模型",
                        model, wait, attempt + 1, MAX_RETRIES_PER_MODEL,
                    )
                    time.sleep(wait)
                    continue
                logger.warning(
                    "gemini_analysis: 模型 %s 呼叫失敗（HTTP %s），改用下一個候選模型: %s",
                    model, status, exc,
                )
                break
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                logger.warning("gemini_analysis: 模型 %s 呼叫失敗，改用下一個候選模型: %s", model, exc)
                break

    logger.error("gemini_analysis: 所有候選模型皆呼叫失敗: %s", last_error)
    raise AIProviderError(str(last_error))
