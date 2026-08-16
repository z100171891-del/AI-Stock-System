"""
data/stock_data.py
--------------------
資料取得層：
1. fetch_price_data()  -> 用 yfinance 抓取台股價量資料（不需金鑰）
2. fetch_chip_data()   -> 用 FinMind API 抓取籌碼面資料（三大法人買賣超 / 融資融券）
                          需要 FINMIND_TOKEN，若未提供則回傳 None，其餘功能不受影響。

所有函式對網路例外都做防呆處理，避免單一股票失敗就讓整個系統中斷。
"""
import logging
from datetime import datetime, timedelta

import pandas as pd
import requests
import yfinance as yf

import config

logger = logging.getLogger(__name__)

FINMIND_URL = "https://api.finmindtrade.com/api/v4/data"


def _to_yf_symbol(stock_id: str) -> str:
    """把 '2330' 轉成 yfinance 使用的 '2330.TW'"""
    stock_id = stock_id.strip()
    if stock_id.endswith(".TW") or stock_id.endswith(".TWO"):
        return stock_id
    return f"{stock_id}{config.YFINANCE_SUFFIX}"


def fetch_price_data(stock_id: str, days: int = None) -> pd.DataFrame:
    """
    抓取指定股票近 N 日的日線價量資料。

    Returns:
        DataFrame，欄位包含 Open/High/Low/Close/Volume，index 為日期。
        失敗時回傳空的 DataFrame（不會拋出例外，方便上層批次處理多檔股票）。
    """
    days = days or config.PRICE_HISTORY_DAYS
    symbol = _to_yf_symbol(stock_id)
    try:
        end = datetime.now()
        start = end - timedelta(days=days * 2)  # 抓寬一點，扣除假日後仍足夠算 MA60
        df = yf.download(
            symbol,
            start=start.strftime("%Y-%m-%d"),
            end=end.strftime("%Y-%m-%d"),
            progress=False,
            auto_adjust=False,
        )
        if df is None or df.empty:
            logger.warning("stock_data: %s 無法取得任何價量資料", symbol)
            return pd.DataFrame()

        # yfinance 新版本在單一股票時可能回傳 MultiIndex 欄位，統一攤平
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)

        df = df.tail(days).copy()
        df.index.name = "date"
        return df
    except Exception as exc:  # noqa: BLE001 - 資料來源錯誤不應中斷整體流程
        logger.error("stock_data: 抓取 %s 價量資料失敗: %s", symbol, exc)
        return pd.DataFrame()


def fetch_chip_data(stock_id: str, days: int = 60) -> dict:
    """
    透過 FinMind 抓取籌碼面資料：三大法人買賣超、融資融券餘額。
    需要 config.FINMIND_TOKEN，若未設定則直接回傳 None（籌碼面分析會標示「資料未提供」）。

    days 預設抓 60 個日曆天（約 40 個交易日），足夠計算「連買/連賣幾天」的籌碼動能，
    需要更長天數可在呼叫時自行調整。
    """
    if not config.FINMIND_TOKEN:
        logger.info("stock_data: 未設定 FINMIND_TOKEN，略過籌碼面資料")
        return None

    start_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    headers = {"Authorization": f"Bearer {config.FINMIND_TOKEN}"}

    result = {"institutional": None, "margin": None}
    try:
        # 三大法人買賣超
        resp = requests.get(
            FINMIND_URL,
            params={
                "dataset": "TaiwanStockInstitutionalInvestorsBuySell",
                "data_id": stock_id,
                "start_date": start_date,
            },
            headers=headers,
            timeout=15,
        )
        resp.raise_for_status()
        payload = resp.json().get("data", [])
        if payload:
            result["institutional"] = pd.DataFrame(payload)
    except Exception as exc:  # noqa: BLE001
        logger.error("stock_data: 抓取 %s 法人資料失敗: %s", stock_id, exc)

    try:
        # 融資融券
        resp = requests.get(
            FINMIND_URL,
            params={
                "dataset": "TaiwanStockMarginPurchaseShortSale",
                "data_id": stock_id,
                "start_date": start_date,
            },
            headers=headers,
            timeout=15,
        )
        resp.raise_for_status()
        payload = resp.json().get("data", [])
        if payload:
            result["margin"] = pd.DataFrame(payload)
    except Exception as exc:  # noqa: BLE001
        logger.error("stock_data: 抓取 %s 融資融券資料失敗: %s", stock_id, exc)

    if result["institutional"] is None and result["margin"] is None:
        return None
    return result
