"""
analysis/scoring.py
----------------------
把技術面 / 籌碼面 / AI 分析的判讀結果，彙整成 0~100 的 AI 綜合評分。

評分邏輯（可依需求調整權重）：
    - 技術面 (40%)：偏多 100 分、中性 50 分、偏空 0 分
    - 籌碼面 (30%)：偏多 100 分、中性 50 分、偏空 0 分（無資料視為中性）
    - AI 情緒面 (30%)：由各 AI 模型判讀的多空傾向平均而得（無模型時視為中性）
"""

TREND_SCORE = {"偏多": 100, "中性": 50, "偏空": 0}

WEIGHTS = {"technical": 0.4, "chip": 0.3, "ai": 0.3}


def compute_score(technical: dict, chip: dict, ai_sentiment: str) -> int:
    """
    Args:
        technical: analysis.technical.judge_technical() 的回傳值
        chip: analysis.technical.judge_chip() 的回傳值
        ai_sentiment: "偏多" / "中性" / "偏空"，來自 AI 多模型分析的綜合傾向

    Returns:
        0~100 的整數評分
    """
    tech_score = TREND_SCORE.get(technical.get("trend"), 50)
    chip_score = TREND_SCORE.get(chip.get("trend"), 50)
    ai_score = TREND_SCORE.get(ai_sentiment, 50)

    total = (
        tech_score * WEIGHTS["technical"]
        + chip_score * WEIGHTS["chip"]
        + ai_score * WEIGHTS["ai"]
    )
    return int(round(total))


def score_to_label(score: int) -> str:
    if score >= 70:
        return "偏多"
    if score <= 30:
        return "偏空"
    return "中性"
