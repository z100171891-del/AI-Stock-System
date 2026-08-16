"""
ai/multi_model.py
--------------------
多模型 AI 分析引擎的統一入口。

- 依 config.ACTIVE_AI_PROVIDERS（有填金鑰的模型）逐一呼叫對應模組
- 任一模型呼叫失敗，不影響其他模型（單獨捕捉例外）
- 若完全沒有設定任何 API 金鑰，改用規則式（rule-based）摘要，
  確保系統在「先做好架構、之後再填金鑰」的情境下仍可端到端運作
- 回傳彙整後的多模型觀點列表 + 綜合情緒傾向（供 scoring.py 使用）
"""
import logging

import config
from ai.base import AIProviderError

logger = logging.getLogger(__name__)

_PROVIDER_MODULES = {
    "gemini": "ai.gemini_analysis",
    "openai": "ai.openai_analysis",
    "deepseek": "ai.deepseek_analysis",
    "groq": "ai.groq_analysis",
}


def _rule_based_fallback(stock_id: str, context: dict) -> dict:
    """沒有任何 AI 金鑰時的備援：直接把技術面/籌碼面判讀轉成一段摘要文字。"""
    tech_trend = context.get("tech_trend", "中性")
    chip_trend = context.get("chip_trend", "中性")
    signals = context.get("tech_signals", []) + context.get("chip_signals", [])
    highlight = signals[0] if signals else "目前無明顯訊號"

    if tech_trend == chip_trend:
        overall = tech_trend
    else:
        overall = "中性"

    text = (
        f"{overall}。根據量化規則判讀（尚未啟用 AI 模型）：{highlight}。"
        "提醒：本結果為技術面與籌碼面規則式綜合，未經 AI 語意分析，建議申請 AI 金鑰以取得更完整的解讀。"
    )
    return {"provider": "規則式摘要（無AI金鑰）", "text": text, "sentiment": overall}


def analyze_all(stock_id: str, context: dict) -> dict:
    """
    Returns:
        {
          "opinions": [ {provider, text, sentiment}, ... ],
          "overall_sentiment": "偏多/中性/偏空",
        }
    """
    opinions = []

    if not config.ACTIVE_AI_PROVIDERS:
        opinions.append(_rule_based_fallback(stock_id, context))
    else:
        import importlib

        for provider in config.ACTIVE_AI_PROVIDERS:
            module_path = _PROVIDER_MODULES.get(provider)
            if not module_path:
                continue
            try:
                module = importlib.import_module(module_path)
                result = module.analyze(stock_id, context)
                opinions.append(result)
            except AIProviderError as exc:
                logger.warning("multi_model: %s 分析略過（%s）", provider, exc)
            except Exception as exc:  # noqa: BLE001 - 任何模型出錯都不應中斷整體流程
                logger.error("multi_model: %s 發生未預期錯誤: %s", provider, exc)

        if not opinions:
            # 所有模型都失敗（例如金鑰失效、網路問題），一樣退回規則式摘要
            opinions.append(_rule_based_fallback(stock_id, context))

    # 綜合情緒傾向：多數決，平手則中性
    sentiments = [o["sentiment"] for o in opinions]
    bullish = sentiments.count("偏多")
    bearish = sentiments.count("偏空")
    if bullish > bearish:
        overall = "偏多"
    elif bearish > bullish:
        overall = "偏空"
    else:
        overall = "中性"

    return {"opinions": opinions, "overall_sentiment": overall}
