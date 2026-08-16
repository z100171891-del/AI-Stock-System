"""
ai/gemini_analysis.py
------------------------
Google Gemini 分析模組。用純 REST API 呼叫（generativelanguage.googleapis.com），
不依賴 google-generativeai SDK，減少額外套件相依。
"""
import logging

import requests

import config
from ai.base import AIProviderError, build_prompt, extract_sentiment

logger = logging.getLogger(__name__)

# Google 會不定期淘汰舊版模型（例如 gemini-1.5-flash 已於 2025 年下架）。
# 這裡列一組候選模型，由新到舊依序嘗試，其中一個失效時能自動改用下一個，
# 避免又因為單一模型名稱過期而整個 AI 分析失效。
MODEL_CANDIDATES = ["gemini-3.5-flash", "gemini-2.5-flash", "gemini-2.0-flash"]
API_URL_TEMPLATE = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"


def analyze(stock_id: str, context: dict) -> dict:
    api_key = config.AI_PROVIDERS.get("gemini")
    if not api_key:
        raise AIProviderError("未設定 GEMINI_API_KEY")

    prompt = build_prompt(stock_id, context)
    last_error = None

    for model in MODEL_CANDIDATES:
        try:
            # 2026 年起 Google 新核發的金鑰是 "AQ." 開頭的 Authentication Key，
            # 不能像舊版 "AIzaSy..." Key 那樣放在網址 ?key= 參數裡（會被誤判成 404/401），
            # 必須改用 x-goog-api-key 這個 HTTP Header 傳遞金鑰。
            resp = requests.post(
                API_URL_TEMPLATE.format(model=model),
                headers={
                    "x-goog-api-key": api_key,
                    "Content-Type": "application/json",
                },
                json={"contents": [{"parts": [{"text": prompt}]}]},
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()
            text = data["candidates"][0]["content"]["parts"][0]["text"].strip()
            return {
                "provider": f"Gemini ({model})",
                "text": text,
                "sentiment": extract_sentiment(text),
            }
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            logger.warning("gemini_analysis: 模型 %s 呼叫失敗，改用下一個候選模型: %s", model, exc)
            continue

    logger.error("gemini_analysis: 所有候選模型皆呼叫失敗: %s", last_error)
    raise AIProviderError(str(last_error))
