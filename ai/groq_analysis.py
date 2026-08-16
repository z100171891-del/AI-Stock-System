"""
ai/groq_analysis.py
----------------------
Groq 分析模組（高速推論，OpenAI 相容 API 格式）。
"""
import logging

import requests

import config
from ai.base import AIProviderError, build_prompt, extract_sentiment

logger = logging.getLogger(__name__)

API_URL = "https://api.groq.com/openai/v1/chat/completions"
MODEL = "llama-3.1-8b-instant"


def analyze(stock_id: str, context: dict) -> dict:
    api_key = config.AI_PROVIDERS.get("groq")
    if not api_key:
        raise AIProviderError("未設定 GROQ_API_KEY")

    prompt = build_prompt(stock_id, context)
    try:
        resp = requests.post(
            API_URL,
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "model": MODEL,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.4,
            },
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        text = data["choices"][0]["message"]["content"].strip()
    except Exception as exc:  # noqa: BLE001
        logger.error("groq_analysis: 呼叫失敗: %s", exc)
        raise AIProviderError(str(exc)) from exc

    return {
        "provider": "Groq",
        "text": text,
        "sentiment": extract_sentiment(text),
    }
