"""
ai/groq_analysis.py
----------------------
Groq 分析模組（高速推論，OpenAI 相容 API 格式）。
"""
import logging
import time

import requests

import config
from ai.base import AIProviderError, build_prompt, extract_sentiment

logger = logging.getLogger(__name__)

API_URL = "https://api.groq.com/openai/v1/chat/completions"
# 2026-08-16 起 Groq 已將 llama-3.1-8b-instant 除役（2026-06-17 email 通知）。
# 這裡改用 llama-3.3-70b-versatile，而不是官方建議的 openai/gpt-oss-20b：
# gpt-oss 系列是「推理模型」，就算輸出的答案很短，也會在背後產生大量隱藏的
# reasoning token，很容易把免費方案每分鐘 8,000 token 的額度用光（實測：
# 分析十幾檔股票後就開始跳 429 Too Many Requests）。llama-3.3-70b-versatile
# 是一般對話模型、沒有隱藏推理 token，且免費方案的 TPM 上限更高（12,000），
# 更適合這種「短答案、高呼叫頻率」的用法。
# 參考：https://console.groq.com/docs/deprecations、https://console.groq.com/docs/rate-limits
MODEL = "llama-3.3-70b-versatile"

# 免費方案的限制是「每分鐘」重置（RPM/TPM），所以 429 時原地等一下再用同一個
# 模型重試就有機會成功，不需要換模型。等待時間要足夠跨過一次「整分鐘」邊界，
# 避免等了半天還在同一個計費分鐘內。
MAX_RETRIES = 3
RETRY_BACKOFF_SECONDS = [20, 40, 60]


def analyze(stock_id: str, context: dict) -> dict:
    api_key = config.AI_PROVIDERS.get("groq")
    if not api_key:
        raise AIProviderError("未設定 GROQ_API_KEY")

    prompt = build_prompt(stock_id, context)
    last_error = None

    for attempt in range(MAX_RETRIES):
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
            return {
                "provider": "Groq",
                "text": text,
                "sentiment": extract_sentiment(text),
            }
        except requests.exceptions.HTTPError as exc:
            last_error = exc
            status = exc.response.status_code if exc.response is not None else None
            if status == 429 and attempt < MAX_RETRIES - 1:
                wait = RETRY_BACKOFF_SECONDS[attempt]
                logger.warning(
                    "groq_analysis: 被限流(429，免費額度每分鐘上限)，%d 秒後重試"
                    "（第 %d/%d 次）",
                    wait, attempt + 1, MAX_RETRIES,
                )
                time.sleep(wait)
                continue
            logger.error("groq_analysis: 呼叫失敗（HTTP %s）: %s", status, exc)
            raise AIProviderError(str(exc)) from exc
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            logger.error("groq_analysis: 呼叫失敗: %s", exc)
            raise AIProviderError(str(exc)) from exc

    logger.error("groq_analysis: 重試 %d 次後仍然失敗: %s", MAX_RETRIES, last_error)
    raise AIProviderError(str(last_error))
