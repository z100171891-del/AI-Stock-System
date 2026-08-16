"""
analysis/technical.py
------------------------
把技術指標數值轉成「偏多 / 中性 / 偏空」的判讀文字，
以及籌碼面（三大法人 / 融資融券）的偏多偏空判讀。
"""
import pandas as pd

BULLISH = "偏多"
BEARISH = "偏空"
NEUTRAL = "中性"


def judge_technical(df: pd.DataFrame) -> dict:
    """
    根據最新一筆技術指標，回傳技術面綜合判讀。

    Returns:
        {
          "trend": "偏多/偏空/中性",
          "signals": [...文字說明...],
          "support": float or None,
          "resistance": float or None,
          "close": float,
          "volume": float,
        }
    """
    if df is None or df.empty:
        return {
            "trend": NEUTRAL,
            "signals": ["資料不足，無法判讀"],
            "support": None,
            "resistance": None,
            "close": None,
            "volume": None,
        }

    latest = df.iloc[-1]
    signals = []
    bullish_count = 0
    bearish_count = 0

    # 均線排列
    if pd.notna(latest.get("MA5")) and pd.notna(latest.get("MA20")):
        if latest["MA5"] > latest["MA20"]:
            signals.append("MA5 站上 MA20，短期均線偏多")
            bullish_count += 1
        else:
            signals.append("MA5 跌破 MA20，短期均線偏空")
            bearish_count += 1

    if pd.notna(latest.get("Close")) and pd.notna(latest.get("MA60")):
        if latest["Close"] > latest["MA60"]:
            signals.append("股價在季線（MA60）之上，中期偏多")
            bullish_count += 1
        else:
            signals.append("股價在季線（MA60）之下，中期偏空")
            bearish_count += 1

    # RSI
    rsi = latest.get("RSI")
    if pd.notna(rsi):
        if rsi >= 70:
            signals.append(f"RSI={rsi:.1f}，接近超買區")
            bearish_count += 1
        elif rsi <= 30:
            signals.append(f"RSI={rsi:.1f}，接近超賣區，留意反彈")
            bullish_count += 1
        else:
            signals.append(f"RSI={rsi:.1f}，處於中性區間")

    # MACD
    macd_hist = latest.get("MACD_HIST")
    if pd.notna(macd_hist):
        if macd_hist > 0:
            signals.append("MACD 柱狀圖翻正，動能偏多")
            bullish_count += 1
        else:
            signals.append("MACD 柱狀圖翻負，動能偏空")
            bearish_count += 1

    # 成交量
    vol_ratio = latest.get("VOL_RATIO")
    if pd.notna(vol_ratio):
        if vol_ratio >= 1.5:
            signals.append(f"成交量為近期均量的 {vol_ratio:.1f} 倍，明顯放量")
        elif vol_ratio <= 0.5:
            signals.append(f"成交量僅為近期均量的 {vol_ratio:.1f} 倍，量能萎縮")

    if bullish_count > bearish_count:
        trend = BULLISH
    elif bearish_count > bullish_count:
        trend = BEARISH
    else:
        trend = NEUTRAL

    # 簡易支撐壓力：近 20 日最低/最高
    recent = df.tail(20)
    support = float(recent["Low"].min()) if "Low" in recent and not recent["Low"].isna().all() else None
    resistance = float(recent["High"].max()) if "High" in recent and not recent["High"].isna().all() else None

    return {
        "trend": trend,
        "signals": signals,
        "support": support,
        "resistance": resistance,
        "close": float(latest["Close"]) if pd.notna(latest.get("Close")) else None,
        "volume": float(latest["Volume"]) if pd.notna(latest.get("Volume")) else None,
    }


# FinMind「TaiwanStockInstitutionalInvestorsBuySell」資料集裡 name 欄位的原始分類，
# 對應到一般俗稱的「三大法人」三個群組。
# 外資自營商（Foreign_Dealer_Self）併入外資；自營商避險（Dealer_Hedging）併入自營商。
INSTITUTION_GROUPS = {
    "Foreign_Investor": "外資",
    "Foreign_Dealer_Self": "外資",
    "Investment_Trust": "投信",
    "Dealer_self": "自營商",
    "Dealer_Hedging": "自營商",
    "Dealer": "自營商",  # 舊版（2018年以前）合併分類，保留相容
}
INSTITUTION_ORDER = ["外資", "投信", "自營商"]


def _compute_institution_breakdown(inst: pd.DataFrame) -> list:
    """
    把逐日、逐分類的三大法人買賣超資料，整理成每個法人「今日買賣超」+「連買/連賣幾天」。

    做法：
      1. 用 INSTITUTION_GROUPS 把細分類別（外資/外資自營商...）合併成外資/投信/自營商三組
      2. 依日期加總各組的淨買賣超（buy - sell）
      3. 由最新一天往回看，只要買賣超同號（同為買超或同為賣超）就算連續天數，
         遇到 0（平盤）或轉向就中止

    Returns:
        [{"name": "外資", "net": int, "action": "買超/賣超/買賣平衡",
          "streak_days": int, "streak_type": "buy/sell/flat"}, ...]
    """
    df = inst.copy()
    df["group"] = df["name"].map(INSTITUTION_GROUPS)
    df = df.dropna(subset=["group"])
    if df.empty:
        return []

    df["buy"] = pd.to_numeric(df["buy"], errors="coerce").fillna(0)
    df["sell"] = pd.to_numeric(df["sell"], errors="coerce").fillna(0)
    df["net"] = df["buy"] - df["sell"]

    daily = df.groupby(["date", "group"], as_index=False)["net"].sum()

    results = []
    for group_name in INSTITUTION_ORDER:
        g = daily[daily["group"] == group_name].sort_values("date", ascending=False)
        if g.empty:
            continue

        latest_net = g.iloc[0]["net"]

        # 由最新一天往回累計連續同號天數
        streak_days = 0
        streak_type = "flat"
        for _, row in g.iterrows():
            net = row["net"]
            cur_sign = "buy" if net > 0 else ("sell" if net < 0 else "flat")
            if streak_days == 0 and streak_type == "flat":
                if cur_sign == "flat":
                    break
                streak_type = cur_sign
                streak_days = 1
            elif cur_sign == streak_type:
                streak_days += 1
            else:
                break

        if latest_net > 0:
            action = "買超"
        elif latest_net < 0:
            action = "賣超"
        else:
            action = "買賣平衡"

        results.append({
            "name": group_name,
            "net": int(latest_net),
            "action": action,
            "streak_days": streak_days,
            "streak_type": streak_type,
        })

    return results


def judge_chip(chip_data: dict) -> dict:
    """
    根據 FinMind 籌碼資料判讀三大法人（外資/投信/自營商）各自的買賣超與連買/連賣天數，
    以及融資融券概況。
    chip_data 為 None 時（未設定 FINMIND_TOKEN）回傳「資料未提供」。

    Returns:
        {
          "trend": "偏多/偏空/中性",
          "signals": [...文字說明，含每個法人的買賣超與連買/連賣天數...],
          "institutions": [{"name","net","action","streak_days","streak_type"}, ...],
        }
    """
    if not chip_data:
        return {
            "trend": NEUTRAL,
            "signals": ["未設定 FinMind 金鑰，籌碼面資料未提供"],
            "institutions": [],
            "margin_available": False,
        }

    signals = []
    institutions = []
    bullish_count = 0
    bearish_count = 0

    inst = chip_data.get("institutional")
    if inst is not None and not inst.empty:
        try:
            institutions = _compute_institution_breakdown(inst)
            for item in institutions:
                streak_text = ""
                if item["streak_days"] > 1:
                    streak_label = "連買" if item["streak_type"] == "buy" else "連賣"
                    streak_text = f"，{streak_label} {item['streak_days']} 天"
                signals.append(
                    f"{item['name']}{item['action']} {abs(item['net']):,} 股{streak_text}"
                )
                if item["action"] == "買超":
                    bullish_count += 1
                elif item["action"] == "賣超":
                    bearish_count += 1
        except Exception as exc:  # noqa: BLE001
            signals.append(f"法人資料格式異常，略過判讀（{exc}）")

    margin = chip_data.get("margin")
    margin_available = margin is not None and not margin.empty
    if margin_available:
        signals.append("已取得融資融券資料（詳見原始數據）")

    trend = BULLISH if bullish_count > bearish_count else (BEARISH if bearish_count > bullish_count else NEUTRAL)
    if not signals:
        signals = ["籌碼資料為空"]

    return {
        "trend": trend,
        "signals": signals,
        "institutions": institutions,
        "margin_available": margin_available,
    }
