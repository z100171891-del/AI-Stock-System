"""
analysis/indicators.py
------------------------
純技術指標計算：MA5 / MA20 / MA60、RSI、MACD、成交量比。
只用 pandas/numpy 實作，不依賴額外的技術分析套件，降低環境相依風險。
"""
import numpy as np
import pandas as pd


def add_moving_averages(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["MA5"] = df["Close"].rolling(window=5).mean()
    df["MA20"] = df["Close"].rolling(window=20).mean()
    df["MA60"] = df["Close"].rolling(window=60).mean()
    return df


def add_rsi(df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
    df = df.copy()
    delta = df["Close"].diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    avg_gain = gain.rolling(window=period).mean()
    avg_loss = loss.rolling(window=period).mean()

    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    df["RSI"] = rsi.fillna(50)  # 資料不足時給中性值
    return df


def add_macd(df: pd.DataFrame, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.DataFrame:
    df = df.copy()
    ema_fast = df["Close"].ewm(span=fast, adjust=False).mean()
    ema_slow = df["Close"].ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    df["MACD"] = macd_line
    df["MACD_SIGNAL"] = signal_line
    df["MACD_HIST"] = macd_line - signal_line
    return df


def add_volume_ratio(df: pd.DataFrame, window: int = 5) -> pd.DataFrame:
    """今日成交量 / 近 N 日均量，>1 代表爆量"""
    df = df.copy()
    avg_vol = df["Volume"].rolling(window=window).mean()
    df["VOL_RATIO"] = df["Volume"] / avg_vol.replace(0, np.nan)
    return df


def compute_all_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """一次套用所有技術指標，回傳新的 DataFrame"""
    if df is None or df.empty:
        return df
    df = add_moving_averages(df)
    df = add_rsi(df)
    df = add_macd(df)
    df = add_volume_ratio(df)
    return df
