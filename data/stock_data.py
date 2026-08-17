"""
data/stock_data.py
--------------------
資料取得層：
1. fetch_price_data()  -> 用 yfinance 抓取台股價量資料（不需金鑰）
2. fetch_chip_data()   -> 用 FinMind API 抓取籌碼面資料（三大法人買賣超 / 融資融券）
                          需要 FINMIND_TOKEN，若未提供則回傳 None，其餘功能不受影響。

所有函式對網路例外都做防呆處理，避免單一股票失敗就讓整個系統中斷。
"""
import io
import logging
import re
from datetime import datetime, timedelta

import pandas as pd
import requests
import yfinance as yf

import config

logger = logging.getLogger(__name__)

FINMIND_URL = "https://api.finmindtrade.com/api/v4/data"

# 全市場當日行情：刻意用 www.twse.com.tw 的舊版介面，而不是 openapi.twse.com.tw
# 的同名端點 —— 這兩個端點都提供「全部上市股票、一次全部回傳」的行情，但
# openapi 版本的「漲跌」欄位是不帶正負號的（本專案先前已實測驗證過這件事），
# 沒辦法用來判斷紅漲綠跌；這裡用的舊版介面「漲跌價差」欄位有正負號，才能正確
# 上色。
TWSE_DAILY_QUOTE_URL = "https://www.twse.com.tw/rwd/zh/afterTrading/STOCK_DAY_ALL?response=json"
# 全市場公司基本資料（含產業別代碼），openapi 版本一次回傳全部上市公司。
TWSE_COMPANY_INDUSTRY_URL = "https://openapi.twse.com.tw/v1/opendata/t187ap03_L"

# TWSE 的 www.twse.com.tw 端點對沒有瀏覽器特徵的請求不太友善：實測發現用
# requests 預設的 User-Agent（沒有 Referer）打過去，有機會拿到空的回應內容
# （resp.text 是空字串），導致 resp.json() 直接噴 "Expecting value" 這種看起來
# 莫名其妙的錯誤。這裡補上瀏覽器常見的標頭，降低被擋的機率。
_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/csv, text/plain, */*",
    "Referer": "https://www.twse.com.tw/zh/trading/historical/stock-day-all.html",
}

# 一般上市股票代號固定 4 碼數字；用來排除特別股（如 2891A）、ETF、TDR 等
# 代號格式不同、yfinance/FinMind 不見得抓得到資料的項目。
_STOCK_CODE_RE = re.compile(r"^\d{4}$")

# TWSE 產業別代碼 → 中文名稱對照表（台灣證交所公開的產業分類代碼）。
# 說明：此表為一般公開的分類代碼，並非本次連線即時逐碼核對過，如果上線後
# 發現某些代碼對應的產業名稱有誤或代碼不在表內，麻煩回報一下，我們再校正；
# 對應不到的代碼會顯示為空白，不影響行情本身（收盤價／漲跌幅／成交量）。
INDUSTRY_CODE_NAME_MAP = {
    "01": "水泥工業", "02": "食品工業", "03": "塑膠工業", "04": "紡織纖維",
    "05": "電機機械", "06": "電器電纜", "08": "玻璃陶瓷", "09": "造紙工業",
    "10": "鋼鐵工業", "11": "橡膠工業", "12": "汽車工業", "13": "電子工業",
    "14": "建材營造業", "15": "航運業", "16": "觀光事業", "17": "金融保險業",
    "18": "貿易百貨業", "20": "其他業", "21": "化學工業", "22": "生技醫療業",
    "23": "油電燃氣業", "24": "半導體業", "25": "電腦及週邊設備業", "26": "光電業",
    "27": "通信網路業", "28": "電子零組件業", "29": "電子通路業", "30": "資訊服務業",
    "31": "其他電子業", "32": "文化創意業", "33": "農業科技業", "34": "電子商務業",
    "35": "觀光餐旅業", "36": "數位雲端業", "37": "運動休閒業", "38": "綠能環保業",
    "80": "存託憑證(DR)",
}

# 大盤指數等特殊項目：在 STOCK_LIST 裡打這些代號（不分大小寫），
# 會自動轉成對應的 yfinance 指數代碼，籌碼面（三大法人）分析會自動略過，
# 因為指數沒有個股層級的法人買賣超資料。
INDEX_ALIASES = {
    "TAIEX": "^TWII",       # 加權指數（大盤）
    "^TWII": "^TWII",
    "OTC": "^TWOII",         # 櫃買指數
    "^TWOII": "^TWOII",
}
INDEX_DISPLAY_NAMES = {
    "^TWII": "加權指數（大盤）",
    "^TWOII": "櫃買指數",
}


def is_index_symbol(stock_id: str) -> bool:
    """判斷是不是大盤等指數代號（而非個股），指數沒有三大法人籌碼資料可抓。"""
    return stock_id.strip().upper() in INDEX_ALIASES


def get_display_name(stock_id: str) -> str:
    """回傳報告上要顯示的名稱：指數顯示中文名稱，個股顯示原始代號。"""
    key = stock_id.strip().upper()
    if key in INDEX_ALIASES:
        return INDEX_DISPLAY_NAMES.get(INDEX_ALIASES[key], stock_id)
    return stock_id


def _to_yf_symbol(stock_id: str) -> str:
    """把 '2330' 轉成 yfinance 使用的 '2330.TW'；指數代號（如 TAIEX/^TWII）直接回傳對應指數代碼"""
    stock_id = stock_id.strip()
    key = stock_id.upper()
    if key in INDEX_ALIASES:
        return INDEX_ALIASES[key]
    if stock_id.endswith(".TW") or stock_id.endswith(".TWO"):
        return stock_id
    return f"{stock_id}{config.YFINANCE_SUFFIX}"


def _to_float(value):
    """安全轉 float：TWSE 常見的 '--'／空字串／None 一律視為缺值（回傳 None）"""
    if value is None:
        return None
    s = str(value).replace(",", "").strip()
    if s in ("", "--", "---", "nan", "None"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _parse_market_quote_response(resp) -> pd.DataFrame:
    """
    TWSE_DAILY_QUOTE_URL 網址雖然帶 ?response=json，但實測發現不保證每次都真的
    回傳 JSON —— 依請求標頭/當下狀況不同，有時會拿到 CSV 純文字格式（欄位跟 JSON
    版本相同，只是逗號分隔＋每個欄位都用雙引號包住）。這裡兩種格式都嘗試解析，
    確保不管 TWSE 那天回什麼格式，只要有拿到內容就解析得出來。

    Returns: DataFrame（欄位為 TWSE 原始中文欄名）；解析不出來回傳空 DataFrame。
    """
    text = resp.text or ""
    stripped = text.strip()

    if stripped.startswith("{"):
        try:
            payload = resp.json()
            fields = payload.get("fields") or []
            rows = payload.get("data") or []
            if fields and rows:
                return pd.DataFrame(rows, columns=fields)
        except Exception as exc:  # noqa: BLE001
            logger.warning("stock_data: 全市場當日行情回應看似 JSON 但解析失敗，改嘗試 CSV 格式: %s", exc)

    if stripped:
        try:
            csv_df = pd.read_csv(io.StringIO(text))
            csv_df.columns = [str(c).strip() for c in csv_df.columns]
            if not csv_df.empty:
                return csv_df
        except Exception as exc:  # noqa: BLE001
            logger.warning("stock_data: 全市場當日行情回應 CSV 格式解析也失敗: %s", exc)

    return pd.DataFrame()


def fetch_market_snapshot() -> pd.DataFrame:
    """
    全市場當日行情快照：一次抓取「全部上市股票」當天的收盤價、漲跌(%)、
    成交量、產業別，整合成一個 DataFrame。全市場動能掃描（用來當 Phase A
    的候選名單）與「全市場報價表」（前端搜尋/排序用）共用這份資料，一天只需要
    各打一次 API，不用對 1000 檔股票逐檔查詢。

    Returns:
        DataFrame，欄位：stock_id, name, sector, close, change, change_pct, volume。
        只保留 4 碼數字的一般股票代號（排除特別股/ETF/TDR 等格式不同的項目）。
        任一來源抓取失敗都不會拋例外：行情抓不到就整批回傳空 DataFrame；
        產業別抓不到則行情資料照常回傳，只是 sector 欄位全部是空字串。
        上層應把「今天抓不到全市場快照」視為可容忍情況，直接跳過全市場相關功能。
    """
    try:
        resp = requests.get(TWSE_DAILY_QUOTE_URL, headers=_BROWSER_HEADERS, timeout=30)
        resp.raise_for_status()
    except Exception as exc:  # noqa: BLE001
        logger.error("stock_data: 抓取全市場當日行情失敗（連線層級）: %s", exc)
        return pd.DataFrame()

    quote_df = _parse_market_quote_response(resp)
    if quote_df.empty:
        # 解析失敗時把足夠的診斷資訊寫進 log（狀態碼／Content-Type／回應前200字），
        # 下次再失敗可以直接從 log 判斷原因，不用再靠猜的。
        snippet = (resp.text or "")[:200].replace("\n", " ")
        logger.error(
            "stock_data: 全市場當日行情回應無法解析成表格（HTTP %s, Content-Type=%s）。"
            "可能今天休市、TWSE 介面已調整、或請求被擋。回應前200字元：%r",
            resp.status_code, resp.headers.get("Content-Type", "?"), snippet,
        )
        return pd.DataFrame()

    required_cols = ["證券代號", "證券名稱", "收盤價", "漲跌價差", "成交股數"]
    missing = [c for c in required_cols if c not in quote_df.columns]
    if missing:
        logger.error("stock_data: 全市場當日行情缺少必要欄位 %s，可能 TWSE 介面已變更；現有欄位=%s",
                      missing, list(quote_df.columns))
        return pd.DataFrame()

    records = []
    for _, row in quote_df.iterrows():
        code = str(row["證券代號"]).strip()
        if not _STOCK_CODE_RE.match(code):
            continue
        close = _to_float(row["收盤價"])
        if close is None:
            continue
        change = _to_float(row["漲跌價差"])
        volume = _to_float(row["成交股數"])
        prev_close = (close - change) if change is not None else None
        change_pct = (change / prev_close * 100) if (change is not None and prev_close) else None
        records.append({
            "stock_id": code,
            "name": str(row["證券名稱"]).strip(),
            "close": close,
            "change": change,
            "change_pct": round(change_pct, 2) if change_pct is not None else None,
            "volume": int(volume) if volume is not None else None,
        })

    if not records:
        logger.error("stock_data: 全市場當日行情解析後沒有任何有效資料列")
        return pd.DataFrame()

    market_df = pd.DataFrame(records)

    # 補上產業別（來源：t187ap03_L）。這一步失敗不影響行情資料本身，只是
    # sector 欄位留空 —— 全市場報價表寧可少一欄產業別，也不要整批不能用。
    try:
        resp = requests.get(TWSE_COMPANY_INDUSTRY_URL, headers=_BROWSER_HEADERS, timeout=30)
        resp.raise_for_status()
        industry_payload = resp.json()
        industry_map = {}
        for item in industry_payload or []:
            code = str(item.get("公司代號", "")).strip()
            sector_code = str(item.get("產業別", "")).strip()
            if code:
                industry_map[code] = INDUSTRY_CODE_NAME_MAP.get(sector_code, "")
        market_df["sector"] = market_df["stock_id"].map(industry_map).fillna("")
    except Exception as exc:  # noqa: BLE001
        logger.warning("stock_data: 抓取全市場產業別失敗，本次全市場資料將不含產業別欄位: %s", exc)
        market_df["sector"] = ""

    market_df = market_df[["stock_id", "name", "sector", "close", "change", "change_pct", "volume"]]
    logger.info("stock_data: 全市場當日行情快照完成，共 %d 檔", len(market_df))
    return market_df


def fetch_price_data_bulk(stock_ids: list, days: int = 90, chunk_size: int = 150) -> dict:
    """
    批次抓取多檔股票的日線價量資料（全市場動能掃描 Phase A 用）。

    用 yfinance 的多股票批次下載（yf.download 一次傳一批 ticker，等於一次
    網路請求抓一整批股票的歷史 K 線），而不是對每檔股票各呼叫一次
    fetch_price_data()，這樣掃描 1000 檔股票不會變成 1000 次個別請求。

    雖然 yfinance 技術上可以把 1000 檔 ticker 一次全塞進單一次 yf.download()
    呼叫（內部會自動用多執行緒平行抓），但實務上還是以 chunk_size 檔為一批分開
    下載比較穩：(1) 避免單一次請求量過大被 Yahoo Finance 限流/逾時、
    導致整批資料連帶失敗；(2) 可以每批印一次進度 log，掃描 1000 檔股票時
    比較看得出來程式是「正在跑」還是「卡住了」；(3) 單一批下載失敗時，只會
    損失那一批，其餘批次仍可正常取得資料。

    Args:
        stock_ids: 股票代號清單（不含 .TW，會自動補上）。
        days: 每檔股票要取的天數（依收盤資料筆數計算，非日曆天）。全市場掃描
            只需要算 MA10/MA20 與近 20 日新高/量能基準，用不到 MA60，所以預設
            比 config.PRICE_HISTORY_DAYS（120，給龍頭股個別分析用，需要算 MA60）
            短，抓 90 天資料（約可對應 60 個交易日）已足夠且更快。
        chunk_size: 每批下載幾檔股票，預設 150。

    Returns:
        {stock_id: DataFrame}，只包含成功取得且至少有一列資料的股票；
        任何一批下載失敗、或個別股票在批次結果中解析失敗，都只會跳過該批/該檔
        （記 log），不會讓整個流程中斷。DataFrame 格式與 fetch_price_data()
        相同（Open/High/Low/Close/Volume，index 為日期），已 tail(days)。

    註：此函式依賴 yfinance 對台股的多股票批次下載，在本開發環境的沙盒中無法
    連線 Yahoo Finance 實測，語法與資料整理邏輯已比照 fetch_price_data() 既有
    的單股邏輯（含 MultiIndex 欄位攤平），但仍建議部署後先用少量股票代號跑一次
    確認回傳格式與預期一致。
    """
    result = {}
    if not stock_ids:
        return result

    id_to_symbol = {sid: _to_yf_symbol(sid) for sid in stock_ids}
    symbol_to_id = {v: k for k, v in id_to_symbol.items()}
    all_symbols = list(id_to_symbol.values())

    end = datetime.now()
    start = end - timedelta(days=days * 2)  # 抓寬一點，扣除假日/休市後仍有足夠交易日

    total_batches = (len(all_symbols) + chunk_size - 1) // chunk_size
    for batch_idx, i in enumerate(range(0, len(all_symbols), chunk_size), start=1):
        batch = all_symbols[i:i + chunk_size]
        logger.info("stock_data: 批次下載歷史K線 第 %d/%d 批（本批 %d 檔）", batch_idx, total_batches, len(batch))
        try:
            data = yf.download(
                batch,
                start=start.strftime("%Y-%m-%d"),
                end=end.strftime("%Y-%m-%d"),
                progress=False,
                auto_adjust=False,
                group_by="ticker",
                threads=True,
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("stock_data: 批次下載第 %d/%d 批失敗，略過本批 %d 檔: %s", batch_idx, total_batches, len(batch), exc)
            continue

        if data is None or data.empty:
            logger.warning("stock_data: 第 %d/%d 批下載結果為空，略過本批", batch_idx, total_batches)
            continue

        for symbol in batch:
            try:
                if len(batch) == 1:
                    df = data
                elif isinstance(data.columns, pd.MultiIndex):
                    top_level = data.columns.get_level_values(0)
                    if symbol not in top_level:
                        continue
                    df = data[symbol]
                else:
                    # 理論上多檔股票時 yfinance 一定回傳 MultiIndex；如果不是，
                    # 代表這批只有一檔真的抓到資料，保守略過以免誤配到別檔股票。
                    continue

                df = df.dropna(how="all")
                if df.empty:
                    continue
                df = df.tail(days).copy()
                df.index.name = "date"
                result[symbol_to_id[symbol]] = df
            except Exception as exc:  # noqa: BLE001
                logger.warning("stock_data: 整理 %s 批次下載資料失敗，略過: %s", symbol, exc)
                continue

    logger.info("stock_data: 批次下載歷史K線完成，成功取得 %d/%d 檔", len(result), len(stock_ids))
    return result


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
    if is_index_symbol(stock_id):
        logger.info("stock_data: %s 為大盤指數，無個股籌碼資料，略過", stock_id)
        return None

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
