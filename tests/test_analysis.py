"""
tests/test_analysis.py
-------------------------
針對技術指標計算、技術面/籌碼面判讀、AI 備援摘要、綜合評分的單元測試。
不依賴網路（不呼叫 yfinance/FinMind/AI API），使用人工建構的假資料，
確保在 CI 環境（GitHub Actions）也能穩定執行。
"""
import numpy as np
import pandas as pd
import pytest

from analysis import indicators, technical, scoring
from ai import multi_model


def _make_price_df(n=80, trend="up"):
    """建立一份模擬價量資料，trend='up' 產生上升趨勢、'down' 產生下降趨勢"""
    dates = pd.date_range("2024-01-01", periods=n, freq="D")
    if trend == "up":
        base = np.linspace(100, 160, n)
    else:
        base = np.linspace(160, 100, n)
    noise = np.random.RandomState(42).normal(0, 0.5, n)
    close = base + noise
    df = pd.DataFrame(
        {
            "Open": close - 0.5,
            "High": close + 1.0,
            "Low": close - 1.0,
            "Close": close,
            "Volume": np.random.RandomState(1).randint(1000, 5000, n),
        },
        index=dates,
    )
    return df


class TestIndicators:
    def test_moving_averages_present(self):
        df = indicators.compute_all_indicators(_make_price_df())
        assert "MA5" in df.columns
        assert "MA20" in df.columns
        assert "MA60" in df.columns
        # 最後一筆應該有值（資料長度足夠）
        assert pd.notna(df["MA60"].iloc[-1])

    def test_rsi_range(self):
        df = indicators.compute_all_indicators(_make_price_df())
        rsi = df["RSI"].dropna()
        assert (rsi >= 0).all() and (rsi <= 100).all()

    def test_macd_columns(self):
        df = indicators.compute_all_indicators(_make_price_df())
        assert {"MACD", "MACD_SIGNAL", "MACD_HIST"}.issubset(df.columns)

    def test_empty_dataframe_safe(self):
        empty = pd.DataFrame()
        result = indicators.compute_all_indicators(empty)
        assert result.empty


class TestTechnicalJudgement:
    def test_uptrend_is_bullish(self):
        df = indicators.compute_all_indicators(_make_price_df(trend="up"))
        result = technical.judge_technical(df)
        assert result["trend"] in ("偏多", "中性")  # 上升趨勢應偏多或至少不偏空
        assert result["close"] is not None

    def test_downtrend_is_bearish(self):
        df = indicators.compute_all_indicators(_make_price_df(trend="down"))
        result = technical.judge_technical(df)
        assert result["trend"] in ("偏空", "中性")

    def test_empty_df_returns_neutral(self):
        result = technical.judge_technical(pd.DataFrame())
        assert result["trend"] == "中性"

    def test_chip_no_data_returns_neutral_with_message(self):
        result = technical.judge_chip(None)
        assert result["trend"] == "中性"
        assert "未提供" in result["signals"][0] or "未設定" in result["signals"][0]


class TestScoring:
    def test_all_bullish_gives_high_score(self):
        tech = {"trend": "偏多"}
        chip = {"trend": "偏多"}
        score = scoring.compute_score(tech, chip, "偏多")
        assert score == 100

    def test_all_bearish_gives_low_score(self):
        tech = {"trend": "偏空"}
        chip = {"trend": "偏空"}
        score = scoring.compute_score(tech, chip, "偏空")
        assert score == 0

    def test_mixed_gives_mid_score(self):
        tech = {"trend": "中性"}
        chip = {"trend": "中性"}
        score = scoring.compute_score(tech, chip, "中性")
        assert score == 50

    def test_score_to_label(self):
        assert scoring.score_to_label(80) == "偏多"
        assert scoring.score_to_label(20) == "偏空"
        assert scoring.score_to_label(50) == "中性"


class TestAIFallback:
    def test_no_api_keys_uses_rule_based_fallback(self, monkeypatch):
        import config
        monkeypatch.setattr(config, "ACTIVE_AI_PROVIDERS", [])
        context = {
            "close": 100.0,
            "tech_trend": "偏多",
            "tech_signals": ["MA5 站上 MA20"],
            "chip_trend": "中性",
            "chip_signals": [],
        }
        result = multi_model.analyze_all("2330", context)
        assert len(result["opinions"]) == 1
        assert "規則式摘要" in result["opinions"][0]["provider"]
        assert result["overall_sentiment"] in ("偏多", "中性", "偏空")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
