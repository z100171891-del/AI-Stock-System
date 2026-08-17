"""
analysis/momentum_screener.py
--------------------------------
選股模組：「超級動能與飆股捕捉（Momentum Breakout）」。

參考市場常見的 VCP（Volatility Contraction Pattern，波動收斂形態）與動能點火手法，
專門抓取「盤整蓄勢完畢、當天帶量噴出」的短線爆發股。用 pandas 對單一個股的
價量 DataFrame（需含 Close/High/Low/Volume，並已算過 MA10/MA20）與三大法人
買賣超明細做篩選，五個條件同時成立才算命中：

  1. 均線多頭排列：今日收盤 > MA10 > MA20（確認股價已站上短中期均線之上，處於強勢推進）
  2. 波動收斂洗盤結束：今日之前 10 個交易日的高低區間震幅 < 10%，且這 10 天的平均量
     比再往前 10 天的平均量更低（籌碼沉澱、賣壓減輕）
  3. 極端動能突破：今日收盤創近 20 個交易日以來的新高
  4. 資金暴力點火：今日成交量 > 今日之前 20 日均量 × 2.5（主力主動買盤湧入）
  5. 大戶籌碼印證：今日三大法人合計買超（股）÷ 今日成交量（股）> 10%
     （排除純隔日沖或散戶自嗨，確認法人資金跟隨）

在檢查以上五個條件之前，還會先做三個「絕對流動性與股價」前置過濾（不是相對倍數，
是絕對門檻），排除「相對倍數符合、但本身是冷門/低價股」的假訊號——例如一檔平時
乏人問津的股票，今天量能只是從 10 張暴增到 30 張，用「倍數」看很嚇人（3倍），
但成交量絕對數字小到根本沒有實際進出場流動性：
  A. 絕對成交量底線：今日成交量 >= 2,000 張
  B. 常態流動性底線：過去 20 日平均成交量 >= 500 張（排除平時一灘死水、偶爾才跳動的股票）
  C. 股價過濾：今日收盤價 >= 15 元（排除容易被單一主力隨意操弄的低價雞蛋水餃股）

不修改傳入的 DataFrame／list，回傳 None 代表未命中；命中時回傳的 dict 一定帶有
「突破價位」（breakout_price）與「今日法人買超佔比」（institutional_buy_ratio），
供前端儀表板直接高亮顯示。

---------------------------------------------------------------------------
全市場掃描用的兩階段拆分（Phase A / Phase B）
---------------------------------------------------------------------------
全市場（約 1000 檔上市股票）掃描時，若對每一檔都呼叫 FinMind 抓三大法人資料，
會產生大量 API 請求、拖慢整體執行時間、也容易撞到免費額度上限。因此把五個條件
拆成兩段，讓 main.py 可以組成一個「漏斗」：

  Phase A：check_price_volume_conditions() — 條件 1~4，純看價量歷史，
           不需要 FinMind，可以對全部 1000 檔跑。
  Phase B：check_institutional_condition() — 條件 5，只需要對 Phase A
           通過的少數個股呼叫 FinMind、檢查籌碼。

screen_momentum_breakout() 本身其實就是 Phase A + Phase B 串起來的封裝，
既有的 15 檔龍頭股流程完全不用改，行為與呼叫方式都不變。

---------------------------------------------------------------------------
第二套策略：N字型量縮回測（Pullback）
---------------------------------------------------------------------------
「超級動能與飆股捕捉」抓的是「當天噴出」的瞬間，隔天/隔日執行的系統要追進去，
常常已經是漲停或漲幅過大的位置，停損空間過寬、風險太高。這套策略反過來抓
「已經表態過的強勢股，回檔洗盤但守住防線」的安全進場點，做波段（持有1-2週）
用，跟動能突破策略雙軌並行，各自獨立判斷、互不影響：

  1. 近期曾表態：過去 5 個交易日內（不含今日），至少有一天出現過「成交量 >
     20日均量2.5倍」且「當日漲幅 > 5%」的強勢紅K（稱為「表態日」）。若有
     多天符合，取離今日最近的一天當表態日（跟今日的價量關係最相關）。
  2. 量縮洗盤：今日成交量 < 表態日成交量 * 50%（賣壓極輕，代表主力沒有出貨，
     只是短線獲利了結後的正常換手）。
  3. 守住防線：今日收盤價 > 表態日那根紅K的最低價（確認防守成功，沒有跌破
     表態當天買盤進場的位置），且今日收盤價站在 10 日均線之上。
  4. 流動性底線：過去20日均量 >= 1,000 張（比動能突破策略的500張門檻更高，
     因為回測進場需要留倉1-2週，對日常流動性的要求更嚴格），且今日收盤價
     >= 15 元。

check_pullback_conditions() 本身就是完整的四個條件（不需要額外的 FinMind
籌碼資料），跟 check_price_volume_conditions()（動能突破的條件1-4）互相
獨立：全市場掃描時，一檔股票只要「動能突破」或「量縮回測」任一策略命中就
算是本次掃描的命中標的，兩者都命中時兩個徽章會同時顯示。
"""
import pandas as pd

# ---------------------------------------------------------------------------
# 篩選門檻常數，全部集中在這裡，之後要調整敏感度（例如把 2.5 倍改成 3 倍）
# 只需要改這裡，不用動篩選邏輯本體。
# ---------------------------------------------------------------------------
MIN_HISTORY_DAYS = 30          # 至少要有這麼多天的資料才夠算所有條件（含均線暖身）
CONSOLIDATION_WINDOW = 10      # 波動收斂觀察窗口（今日之前 N 天）
CONSOLIDATION_RANGE_PCT = 10.0  # 波動收斂區間震幅上限（%）
BREAKOUT_LOOKBACK = 20         # 「近 N 日新高」的 N
VOLUME_BASELINE_WINDOW = 20    # 量能基準窗口（今日之前 N 天）
VOLUME_SPIKE_MULTIPLE = 2.5    # 今日量 > 量能基準 × 這個倍數
INSTITUTIONAL_BUY_RATIO_PCT = 10.0  # 法人合計買超佔今日成交量的門檻（%）

# ---------------------------------------------------------------------------
# 絕對流動性與股價的前置過濾門檻。台股慣例以「張」（1張=1,000股）報價量，
# 這裡的門檻沿用這個習慣單位，換算成股數時統一乘以 SHARES_PER_LOT。
# ---------------------------------------------------------------------------
SHARES_PER_LOT = 1000            # 1 張 = 1,000 股
MIN_VOLUME_TODAY_LOTS = 2000     # 今日成交量門檻（張）：排除量能倍數符合但絕對量太小的訊號
MIN_AVG_VOLUME_20D_LOTS = 500    # 過去20日平均成交量門檻（張）：排除平時一灘死水的冷門股
MIN_CLOSE_PRICE = 15.0           # 股價門檻（元）：排除容易被單一主力操弄的低價雞蛋水餃股

# ---------------------------------------------------------------------------
# 「N字型量縮回測（Pullback）」策略的門檻常數。
# ---------------------------------------------------------------------------
PULLBACK_LOOKBACK_DAYS = 5              # 表態日搜尋範圍：過去N個交易日（不含今日）
PULLBACK_STRONG_VOLUME_MULTIPLE = 2.5   # 表態日成交量門檻：> 20日均量的倍數
PULLBACK_STRONG_GAIN_PCT = 5.0          # 表態日單日漲幅門檻（%）
PULLBACK_VOLUME_SHRINK_RATIO = 0.5      # 量縮門檻：今日量 < 表態日量 * 此比例
PULLBACK_MIN_AVG_VOLUME_20D_LOTS = 1000  # 過去20日均量門檻（張），比動能突破策略更高


def check_price_volume_conditions(df: pd.DataFrame) -> dict:
    """
    Phase A：只檢查條件 1~4（均線多頭排列／波動收斂洗盤／創新高突破／爆量），
    完全不需要籌碼資料，可以對全市場每一檔股票跑，用來當作全市場掃描的第一層漏斗。

    Args:
        df: 價量 DataFrame，需含 Close/High/Low/Volume 欄位，時間由舊到新排序，
            最後一列是「今日」。若尚未算過 MA10/MA20 會自動補算。

    Returns:
        未命中回傳 None；命中回傳 dict（欄位同 screen_momentum_breakout()，
        但不含 institutional_buy_ratio，因為條件 5 還沒檢查）：
        {
          "breakout_price": float, "volume_multiple": float, "range_pct": float,
          "ma10": float, "ma20": float, "volume_today": int,
          "baseline_avg_volume": float,
        }
    """
    if df is None or len(df) < MIN_HISTORY_DAYS:
        return None

    d = df.copy()
    if "MA10" not in d.columns:
        d["MA10"] = d["Close"].rolling(window=10).mean()
    if "MA20" not in d.columns:
        d["MA20"] = d["Close"].rolling(window=20).mean()

    latest = d.iloc[-1]
    close = latest.get("Close")
    ma10 = latest.get("MA10")
    ma20 = latest.get("MA20")
    volume_today = latest.get("Volume")

    if pd.isna(close) or pd.isna(ma10) or pd.isna(ma20) or pd.isna(volume_today):
        return None

    # ---- 前置過濾 A：絕對成交量底線，今日成交量 >= 2,000 張 ----
    if (volume_today / SHARES_PER_LOT) < MIN_VOLUME_TODAY_LOTS:
        return None

    # ---- 前置過濾 C：股價過濾，今日收盤 >= 15 元 ----
    if close < MIN_CLOSE_PRICE:
        return None

    # ---- 前置過濾 B：常態流動性底線，過去 20 日平均成交量 >= 500 張 ----
    # 這裡算出來的 baseline_avg_volume（過去20日均量）之後條件4也會用到同一份，
    # 不用重算第二次。
    baseline_vol_window = d["Volume"].iloc[-(VOLUME_BASELINE_WINDOW + 1):-1]
    if len(baseline_vol_window) < VOLUME_BASELINE_WINDOW:
        return None
    baseline_avg_volume = baseline_vol_window.mean()
    if pd.isna(baseline_avg_volume) or baseline_avg_volume <= 0:
        return None
    if (baseline_avg_volume / SHARES_PER_LOT) < MIN_AVG_VOLUME_20D_LOTS:
        return None

    # ---- 條件 1：均線多頭排列 ----
    if not (close > ma10 > ma20):
        return None

    # ---- 條件 2：波動收斂洗盤結束（今日之前 10 天 vs 再前 10 天） ----
    consolidation = d.iloc[-(CONSOLIDATION_WINDOW + 1):-1]          # 今日之前 10 天
    baseline_window = d.iloc[-(CONSOLIDATION_WINDOW * 2 + 1):-(CONSOLIDATION_WINDOW + 1)]  # 再往前 10 天
    if len(consolidation) < CONSOLIDATION_WINDOW or len(baseline_window) < CONSOLIDATION_WINDOW:
        return None

    range_high = consolidation["High"].max()
    range_low = consolidation["Low"].min()
    if pd.isna(range_high) or pd.isna(range_low) or range_low <= 0:
        return None
    range_pct = (range_high - range_low) / range_low * 100
    if range_pct >= CONSOLIDATION_RANGE_PCT:
        return None

    consolidation_avg_vol = consolidation["Volume"].mean()
    baseline_avg_vol_for_shrink = baseline_window["Volume"].mean()
    if pd.isna(consolidation_avg_vol) or pd.isna(baseline_avg_vol_for_shrink) or baseline_avg_vol_for_shrink <= 0:
        return None
    if consolidation_avg_vol >= baseline_avg_vol_for_shrink:
        return None  # 沒有萎縮，洗盤還沒結束

    # ---- 條件 3：極端動能突破，今日收盤創近 20 日新高（含今日） ----
    recent_closes = d["Close"].tail(BREAKOUT_LOOKBACK)
    if len(recent_closes) < BREAKOUT_LOOKBACK or close < recent_closes.max():
        return None
    breakout_price = float(close)

    # ---- 條件 4：資金暴力點火，今日量 > 今日之前 20 日均量 × 2.5 ----
    # baseline_avg_volume 已經在前面「前置過濾 B」算過了，直接重複使用同一份，不用重算。
    volume_multiple = volume_today / baseline_avg_volume
    if volume_multiple <= VOLUME_SPIKE_MULTIPLE:
        return None

    return {
        "breakout_price": round(breakout_price, 2),
        "volume_multiple": round(float(volume_multiple), 2),
        "range_pct": round(float(range_pct), 2),
        "ma10": round(float(ma10), 2),
        "ma20": round(float(ma20), 2),
        "volume_today": int(volume_today),
        "baseline_avg_volume": round(float(baseline_avg_volume), 1),
    }


def _trailing_avg_volume(volume_series: pd.Series, idx: int, window: int = VOLUME_BASELINE_WINDOW):
    """
    計算 volume_series 在位置 idx「之前」window 天的平均成交量（不含 idx 當天本身）。
    idx 之前的歷史天數不足 window 天時回傳 None，平均值算出來是 NaN 或 <=0 也回傳 None
    （呼叫端遇到 None 一律視為「資料不足，不命中」，不會因為除以 0 / NaN 而拋例外）。
    """
    start = idx - window
    if start < 0:
        return None
    window_slice = volume_series.iloc[start:idx]
    if len(window_slice) < window:
        return None
    avg = window_slice.mean()
    if pd.isna(avg) or avg <= 0:
        return None
    return float(avg)


def check_pullback_conditions(df: pd.DataFrame) -> dict:
    """
    「N字型量縮回測（Pullback）」策略：抓「已表態的強勢股回檔洗盤、守住防線」的
    波段安全進場點，四個條件同時成立才算命中（詳見本檔開頭的模組說明）。

    Args:
        df: 價量 DataFrame，需含 Close/High/Low/Volume 欄位，時間由舊到新排序，
            最後一列是「今日」。若尚未算過 MA10 會自動補算。

    Returns:
        未命中回傳 None；命中回傳：
        {
          "entry_price": float,              # 今日收盤（進場參考價）
          "trigger_date_offset": int,        # 表態日距離今日幾個交易日（1~5）
          "trigger_close": float,            # 表態日收盤價
          "trigger_low": float,              # 表態日最低價（今日收盤要守住的防線）
          "trigger_volume": int,             # 表態日成交量（股）
          "trigger_gain_pct": float,         # 表態日單日漲幅（%）
          "trigger_volume_multiple": float,  # 表態日量能倍數（表態日量 ÷ 表態日之前20日均量）
          "today_volume": int,               # 今日成交量（股）
          "volume_shrink_ratio": float,      # 今日量 ÷ 表態日量（越小代表洗盤越乾淨）
          "ma10": float,                     # 今日10日均線
          "avg_volume_20d": float,           # 今日之前20日均量（股）
          "stop_loss_pct": float,            # 潛在停損幅度（%，恆為負值＝(防守價-進場價)/進場價）
        }
    """
    if df is None or len(df) < MIN_HISTORY_DAYS:
        return None

    d = df.copy()
    if "MA10" not in d.columns:
        d["MA10"] = d["Close"].rolling(window=10).mean()

    today_idx = len(d) - 1
    latest = d.iloc[today_idx]
    close_today = latest.get("Close")
    ma10_today = latest.get("MA10")
    volume_today = latest.get("Volume")

    if pd.isna(close_today) or pd.isna(ma10_today) or pd.isna(volume_today):
        return None

    # ---- 前置過濾：流動性底線（過去20日均量 >= 1,000張、股價 >= 15元） ----
    if close_today < MIN_CLOSE_PRICE:
        return None
    avg_volume_20d = _trailing_avg_volume(d["Volume"], today_idx, VOLUME_BASELINE_WINDOW)
    if avg_volume_20d is None:
        return None
    if (avg_volume_20d / SHARES_PER_LOT) < PULLBACK_MIN_AVG_VOLUME_20D_LOTS:
        return None

    # ---- 條件1：過去5個交易日內找「表態日」（不含今日），由近到遠找第一個符合的 ----
    trigger_idx = None
    for offset in range(1, PULLBACK_LOOKBACK_DAYS + 1):
        idx = today_idx - offset
        if idx <= 0:
            continue  # 需要 idx-1 那天的收盤價才能算當日漲幅，資料不夠就跳過
        row = d.iloc[idx]
        prev_close = d.iloc[idx - 1].get("Close")
        vol = row.get("Volume")
        close_i = row.get("Close")
        low_i = row.get("Low")
        if pd.isna(prev_close) or prev_close <= 0 or pd.isna(vol) or pd.isna(close_i) or pd.isna(low_i):
            continue
        baseline = _trailing_avg_volume(d["Volume"], idx, VOLUME_BASELINE_WINDOW)
        if baseline is None:
            continue
        gain_pct = (close_i - prev_close) / prev_close * 100
        volume_multiple = vol / baseline
        if volume_multiple > PULLBACK_STRONG_VOLUME_MULTIPLE and gain_pct > PULLBACK_STRONG_GAIN_PCT:
            trigger_idx = idx
            break

    if trigger_idx is None:
        return None

    trigger_row = d.iloc[trigger_idx]
    trigger_close = trigger_row.get("Close")
    trigger_low = trigger_row.get("Low")
    trigger_volume = trigger_row.get("Volume")
    if pd.isna(trigger_low) or pd.isna(trigger_volume) or trigger_volume <= 0:
        return None

    # ---- 條件2：量縮洗盤，今日量 < 表態日量 * 50% ----
    volume_shrink_ratio = volume_today / trigger_volume
    if volume_shrink_ratio >= PULLBACK_VOLUME_SHRINK_RATIO:
        return None

    # ---- 條件3：守住防線，今日收盤 > 表態日最低價，且今日收盤 > MA10 ----
    if not (close_today > trigger_low):
        return None
    if not (close_today > ma10_today):
        return None

    trigger_prev_close = d.iloc[trigger_idx - 1].get("Close")
    trigger_gain_pct = (trigger_close - trigger_prev_close) / trigger_prev_close * 100
    trigger_baseline = _trailing_avg_volume(d["Volume"], trigger_idx, VOLUME_BASELINE_WINDOW)
    trigger_volume_multiple = trigger_volume / trigger_baseline

    # 潛在停損幅度：進場參考價（今日收盤）到嚴格防守價（表態日最低價）的跌幅。
    # 條件3已經保證 close_today > trigger_low，所以這個數字恆為負值（回檔跌破防守
    # 價就代表這筆交易停損出場，虧損上限就是這個百分比）——用來讓使用者一眼看到
    # 「這筆交易最多會賠多少」，落實大賺小賠。
    stop_loss_pct = (trigger_low - close_today) / close_today * 100

    return {
        "entry_price": round(float(close_today), 2),
        "trigger_date_offset": today_idx - trigger_idx,
        "trigger_close": round(float(trigger_close), 2),
        "trigger_low": round(float(trigger_low), 2),
        "trigger_volume": int(trigger_volume),
        "trigger_gain_pct": round(float(trigger_gain_pct), 2),
        "trigger_volume_multiple": round(float(trigger_volume_multiple), 2),
        "today_volume": int(volume_today),
        "volume_shrink_ratio": round(float(volume_shrink_ratio), 3),
        "ma10": round(float(ma10_today), 2),
        "avg_volume_20d": round(float(avg_volume_20d), 1),
        "stop_loss_pct": round(float(stop_loss_pct), 2),
    }


def check_institutional_condition(volume_today, institutions: list):
    """
    Phase B：只檢查條件 5（大戶籌碼印證）。

    Args:
        volume_today: 今日成交量（股）。通常直接用 check_price_volume_conditions()
            回傳 dict 裡的 "volume_today"。
        institutions: judge_chip() 回傳結果裡的 institutions list，每筆
            {"name","net","action",...}；net 為該法人今日買賣超（股，正值＝買超）。
            沒有籌碼資料就傳 None 或空 list。

    Returns:
        未命中（含無籌碼資料、合計是賣超、佔比不足 10%）回傳 None；
        命中回傳 institutional_buy_ratio（float，四捨五入到小數 2 位）。
    """
    if not institutions:
        return None
    total_institutional_net = sum(item.get("net", 0) or 0 for item in institutions)
    if not volume_today or volume_today <= 0:
        return None
    institutional_buy_ratio = total_institutional_net / volume_today * 100
    if institutional_buy_ratio <= INSTITUTIONAL_BUY_RATIO_PCT:
        return None
    return round(float(institutional_buy_ratio), 2)


def screen_momentum_breakout(df: pd.DataFrame, institutions: list = None) -> dict:
    """
    對單一個股跑「超級動能與飆股捕捉」五條件篩選（Phase A + Phase B 一次跑完）。
    既有的 15 檔龍頭股流程呼叫方式完全不變。

    Args:
        df: 見 check_price_volume_conditions()。
        institutions: 見 check_institutional_condition()。

    Returns:
        未命中回傳 None；命中回傳：
        {
          "breakout_price": float,              # 突破價位（今日收盤）
          "institutional_buy_ratio": float,      # 今日法人合計買超 ÷ 今日成交量（%）
          "volume_multiple": float,              # 今日量 ÷ 過去20日均量（倍）
          "range_pct": float,                    # 洗盤期間高低區間震幅（%）
          "ma10": float, "ma20": float,
          "volume_today": int,
          "baseline_avg_volume": float,          # 過去20日均量（今日之前，不含今日）
        }
    """
    pv_result = check_price_volume_conditions(df)
    if pv_result is None:
        return None

    ratio = check_institutional_condition(pv_result["volume_today"], institutions)
    if ratio is None:
        return None

    return {**pv_result, "institutional_buy_ratio": ratio}


def screen_universe(candidates: list) -> list:
    """
    對多檔股票批次跑篩選，只回傳有命中的清單，依「法人買超佔比」由高到低排序
    （佔比越高代表法人跟隨力道越強，適合前端儀表板優先高亮）。

    Args:
        candidates: [{"stock_id": str, "df": DataFrame, "institutions": list}, ...]

    Returns:
        [{"stock_id": str, **screen_momentum_breakout() 的回傳欄位}, ...]
    """
    hits = []
    for c in candidates or []:
        result = screen_momentum_breakout(c.get("df"), c.get("institutions"))
        if result:
            hits.append({"stock_id": c.get("stock_id"), **result})
    hits.sort(key=lambda h: h["institutional_buy_ratio"], reverse=True)
    return hits
