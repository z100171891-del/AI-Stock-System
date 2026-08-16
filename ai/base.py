"""
ai/base.py
-----------
所有 AI 分析模型的共用介面與 prompt 樣板。
每個 provider 模組（gemini_analysis.py / openai_analysis.py / ...）
只需要實作 analyze(stock_id, context) -> dict，回傳格式統一，
方便 multi_model.py 做彙整與可插拔擴充。
"""

PROMPT_TEMPLATE = """你是一位專業的台股分析師。請根據以下量化資料，給出簡潔的分析：

股票代號：{stock_id}
收盤價：{close}
技術面判讀：{tech_trend}（{tech_signals}）
籌碼面判讀：{chip_trend}（{chip_signals}）

請用三句話以內，給出：
1. 你對後市的看法（偏多/中性/偏空，請在開頭明確標註其中一個詞）
2. 一個關鍵觀察重點
3. 一個風險提示

請用繁體中文回答，不要使用條列符號。
"""


def build_prompt(stock_id: str, context: dict) -> str:
    return PROMPT_TEMPLATE.format(
        stock_id=stock_id,
        close=context.get("close", "N/A"),
        tech_trend=context.get("tech_trend", "N/A"),
        tech_signals="；".join(context.get("tech_signals", [])) or "無",
        chip_trend=context.get("chip_trend", "N/A"),
        chip_signals="；".join(context.get("chip_signals", [])) or "無",
    )


def extract_sentiment(text: str) -> str:
    """從 AI 回覆文字中粗略判斷情緒傾向（偏多/中性/偏空），用於綜合評分。"""
    if not text:
        return "中性"
    if "偏多" in text[:20] or "看多" in text[:20]:
        return "偏多"
    if "偏空" in text[:20] or "看空" in text[:20]:
        return "偏空"
    return "中性"


class AIProviderError(Exception):
    """AI provider 呼叫失敗時拋出，由呼叫端捕捉並優雅降級。"""
