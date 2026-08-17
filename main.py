"""
main.py
--------
系統主流程進入點。

流程：
  1. 讀取 config.STOCK_LIST 中的每檔股票（產業龍頭），逐檔跑完整分析
  2. data/stock_data.py 取得價量資料（yfinance）與籌碼資料（FinMind，選填）
  3. analysis/indicators.py 計算技術指標
  4. analysis/technical.py 產生技術面/籌碼面判讀
  5. analysis/momentum_screener.py 跑選股，兩套策略雙軌並行：
     - 超級動能與飆股捕捉（VCP 波動收斂 + 動能突破，追噴出）
     - N字型量縮回測（表態後回檔洗盤、守住防線的安全進場點，適合波段持有1-2週）
  6. ai/multi_model.py 呼叫已設定金鑰的 AI 模型（無金鑰則用規則式摘要）
  7. analysis/scoring.py 計算 AI 綜合評分 0~100
  8. 全市場動能掃描（漏斗式）：data/stock_data.py 抓全部上市股票當日行情
     ＋批次歷史K線，用 momentum_screener 的 Phase A（價量，不用 FinMind）先篩過
     全部上市股票（動能突破的條件1-4／量縮回測的四個條件皆屬於此階段），只有
     動能突破候選才需要再進 Phase B（FinMind 籌碼，檢查條件5）；量縮回測本身
     四個條件就已經完整，不需要籌碼資料即可算命中。沒命中任何一套策略的個股
     只保留基礎行情，做成前端可搜尋的全市場報價表。
  9. report/html_report.py 產生 HTML 報告，同時另存一份成 docs/index.html（給 GitHub Pages 當網站首頁）
  10. report/email_report.py 寄送 Email（預設關閉，於 .env 設 ENABLE_EMAIL=true 才會寄）

另外，進入全市場個股篩選之前，_check_market_regime() 會先檢測加權指數（大盤）收盤價
是否站上20日均線（月線）：這套系統是短線策略，回測經驗上大盤跌破月線時做多勝率會
明顯降低，所以這裡不是「選股邏輯」的一部分，而是獨立的風險提示——選股、AI分析、
報告照常產生，只是大盤跌破月線時報告最頂端會強制顯示醒目紅色警告，提醒使用者自己
評估部位大小（不讓系統看起來「有選到股票=可以進場」）。

可直接執行：python main.py
也是 GitHub Actions 每日排程呼叫的進入點。
"""
import logging
import sys

import pandas as pd

import config
from data import stock_data
from analysis import indicators, technical, scoring, momentum_screener
from ai import multi_model
from report import html_report, email_report

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("main")


def analyze_stock(
    stock_id: str,
    price_df=None,
    chip_raw=None,
    momentum_result="__unset__",
    pullback_result="__unset__",
    display_name: str = "",
) -> dict:
    """
    對單一股票（或指數）跑完整分析：技術面、籌碼面、動能篩選（動能突破＋量縮回測
    兩套策略雙軌並行）、AI 分析、綜合評分。

    price_df / chip_raw / momentum_result / pullback_result 都是選填的「預先抓好
    的資料」注入點，給全市場掃描的漏斗流程用（見 _run_full_market_momentum_scan()）：
    那些個股在 Phase A / Phase B 篩選時就已經抓過價量與籌碼資料、也已經算出動能
    篩選結果了，這裡就不用重複打一次 yfinance/FinMind，只需要接著做 AI 分析與評分。
    momentum_result / pullback_result 沒有特別傳入時用 "__unset__" 當哨兵值，跟
    「明確傳 None」（例如指數）區分開來，這裡才會照原本邏輯重新算一次。
    """
    logger.info("開始分析 %s", stock_id)

    if price_df is None:
        price_df = stock_data.fetch_price_data(stock_id)
    if price_df is None or price_df.empty:
        logger.warning("%s 無價量資料，略過此檔", stock_id)
        return None

    price_df = indicators.compute_all_indicators(price_df)
    tech_result = technical.judge_technical(price_df)

    is_index = stock_data.is_index_symbol(stock_id)
    if is_index:
        # 大盤等指數沒有個股層級的三大法人買賣超資料，籌碼面直接標示不適用
        chip_result = {
            "trend": "中性",
            "signals": ["大盤指數無個股籌碼資料，此欄不適用"],
            "institutions": [],
            "margin_available": False,
        }
        momentum_result = None  # 大盤指數不適用「個股動能突破」選股邏輯
        pullback_result = None  # 大盤指數不適用「量縮回測」選股邏輯
    else:
        if chip_raw is None:
            chip_raw = stock_data.fetch_chip_data(stock_id)
        chip_result = technical.judge_chip(chip_raw)
        if momentum_result == "__unset__":
            momentum_result = momentum_screener.screen_momentum_breakout(
                price_df, chip_result.get("institutions")
            )
        if pullback_result == "__unset__":
            pullback_result = momentum_screener.check_pullback_conditions(price_df)

    ai_context = {
        "close": tech_result.get("close"),
        "tech_trend": tech_result.get("trend"),
        "tech_signals": tech_result.get("signals", []),
        "chip_trend": chip_result.get("trend"),
        "chip_signals": chip_result.get("signals", []),
    }
    ai_result = multi_model.analyze_all(stock_id, ai_context)

    score = scoring.compute_score(tech_result, chip_result, ai_result["overall_sentiment"])

    name = stock_data.get_display_name(stock_id) if is_index else (display_name or "")

    return {
        "stock_id": stock_id,
        "name": name,
        "sector": "大盤指數" if is_index else config.STOCK_SECTOR_MAP.get(stock_id, ""),
        "is_index": is_index,
        "is_market_scan_hit": False,  # 全市場掃描命中的個股會在外面覆寫成 True
        "score": score,
        "technical": tech_result,
        "chip": chip_result,
        "ai": ai_result,
        "momentum": momentum_result,
        "pullback": pullback_result,
    }


def _check_market_regime() -> dict:
    """
    【大盤多空環境濾網】：獨立抓一次加權指數（TAIEX/^TWII）的日線資料，檢查今日
    收盤價有沒有站上 20 日均線（月線）。這套系統做的是短線策略（動能突破／量縮
    回測，目標持有數天到1-2週），大盤一旦跌破月線代表中期趨勢轉弱，同樣的訊號
    在多頭/空頭大盤下勝率差很多——但這裡不負責「擋掉」選股或報告（選股、AI分析、
    報告照常產生），只負責回報大盤狀態，讓 report/html_report.py 決定要不要在
    報告最頂端顯示警告。之所以獨立抓一次資料，而不是重複使用 config.STOCK_LIST
    裡「TAIEX」這檔的分析結果，是因為使用者可能自訂 STOCK_LIST、拿掉了大盤這筆，
    這裡獨立判斷才能保證這個風險提示永遠有效。

    Returns:
        {"status": "bullish"/"bearish"/"unknown", "close": float or None, "ma20": float or None}
          status == "bullish"：收盤價 >= 20日均線（站上月線），報告不顯示警告。
          status == "bearish"：收盤價 < 20日均線（跌破月線），報告最頂端顯示警告。
          status == "unknown"：抓不到資料或資料不足以算20日均線（例如新股剛上市、
            網路問題），寧可不判斷也不要憑不足的資料亂發警告，報告不顯示警告。
    """
    try:
        df = stock_data.fetch_price_data("TAIEX")
        if df is None or df.empty:
            logger.warning("大盤環境濾網：抓不到加權指數資料，本次略過大盤濾網判斷")
            return {"status": "unknown", "close": None, "ma20": None}

        df = indicators.add_moving_averages(df)
        latest = df.iloc[-1]
        close = latest.get("Close")
        ma20 = latest.get("MA20")
        if pd.isna(close) or pd.isna(ma20):
            logger.warning("大盤環境濾網：加權指數資料不足以計算20日均線，本次略過大盤濾網判斷")
            return {"status": "unknown", "close": None, "ma20": None}

        status = "bullish" if close >= ma20 else "bearish"
        logger.info(
            "大盤環境濾網：加權指數收盤 %.2f，20日均線（月線）%.2f → %s",
            close, ma20, "站上月線，正常模式" if status == "bullish" else "🚨 跌破月線，防禦模式",
        )
        return {"status": status, "close": round(float(close), 2), "ma20": round(float(ma20), 2)}
    except Exception as exc:  # noqa: BLE001 - 大盤濾網失敗不應影響選股與報告主流程
        logger.error("大盤環境濾網：檢測發生未預期錯誤，本次略過大盤濾網判斷: %s", exc)
        return {"status": "unknown", "close": None, "ma20": None}


def _run_full_market_momentum_scan(existing_ids: set) -> tuple:
    """
    全市場選股掃描：不只看 config.STOCK_LIST 這 15 檔龍頭股，改成對全部上市股票
    （約1000檔）跑「超級動能與飆股捕捉」＋「N字型量縮回測」兩套策略，才抓得到
    真正符合條件的中小型飆股／安全進場點。用「漏斗式」設計，避免對全市場呼叫
    FinMind 或 AI：

      Phase A（全部上市股票）：只用價量歷史資料，同時檢查兩套策略：
        - 動能突破條件 1~4（均線多頭排列／波動收斂洗盤／創新高突破／爆量），
        - 量縮回測的四個條件（近期表態／量縮洗盤／守住防線／流動性底線）。
        兩者都完全不需要 FinMind，用 yfinance 批次下載一次抓完全部候選股票的
        歷史K線。
      Phase B（只有動能突破 Phase A 通過的個股）：才呼叫 FinMind 補上籌碼資料，
        檢查條件 5（法人買超佔比）。量縮回測不需要這一步——四個條件本身就已經
        是完整判定，Phase A 通過即算命中。
      只要「動能突破全部命中」或「量縮回測全部命中」任一套策略成立，該檔股票
      就會送進 analyze_stock() 做 AI 深度分析——維持「AI 只分析有意義的標的」
      的設計，全市場掃描也不會讓 AI 呼叫次數暴增（依市場狀況，命中數通常是
      個位數到十幾檔，而不是上千次）。兩套策略都命中的股票，報告會同時顯示
      兩個徽章。

    Args:
        existing_ids: 已經在 config.STOCK_LIST 跑過完整分析的股票代號集合，
            全市場候選名單會排除這些，避免重複下載/重複分析。

    Returns:
        (market_hit_results, market_table)
          market_hit_results: 命中任一策略、已完成 AI 分析的 dict 清單，格式與
            analyze_stock() 回傳相同，可以直接併入既有的 results 清單。
          market_table: 全市場「沒有」進入深度分析的個股基礎行情清單，
            [{"stock_id","name","sector","close","change","change_pct","volume"}, ...]，
            給前端「全市場報價表」做搜尋/排序用。

    任何一步失敗（抓不到全市場快照、批次下載失敗等）都只會讓這個函式回傳
    ([], [])，不影響 config.STOCK_LIST 既有的分析流程照常產生報告。
    """
    snapshot_df = stock_data.fetch_market_snapshot()
    if snapshot_df is None or snapshot_df.empty:
        logger.warning("全市場動能掃描：取得不到全市場當日行情，本次略過全市場掃描與報價表")
        return [], []

    snapshot_df = snapshot_df[~snapshot_df["stock_id"].isin(existing_ids)]
    candidate_ids = snapshot_df["stock_id"].tolist()
    id_to_row = {row["stock_id"]: row for _, row in snapshot_df.iterrows()}
    logger.info("全市場動能掃描：候選 %d 檔（已排除既有清單 %d 檔）", len(candidate_ids), len(existing_ids))

    price_map = stock_data.fetch_price_data_bulk(candidate_ids)
    if not price_map:
        logger.warning("全市場動能掃描：批次下載歷史K線沒有取得任何資料，本次略過 Phase A 篩選")
        # 仍然可以把行情快照整批當成「全市場報價表」呈現，只是沒有動能標記
        market_table = snapshot_df.to_dict("records")
        return [], market_table

    # ---- Phase A：動能突破條件1-4 ＋ 量縮回測四個條件，全候選股票都跑，兩策略互相獨立 ----
    phase_a_survivors = []  # [(stock_id, price_df, breakout_pv_result_or_None, pullback_result_or_None)]
    breakout_candidates = 0
    pullback_candidates = 0
    for stock_id, df in price_map.items():
        try:
            df_ma = indicators.add_moving_averages(df)
            breakout_pv_result = momentum_screener.check_price_volume_conditions(df_ma)
            pullback_result = momentum_screener.check_pullback_conditions(df_ma)
            if breakout_pv_result:
                breakout_candidates += 1
            if pullback_result:
                pullback_candidates += 1
            if breakout_pv_result or pullback_result:
                phase_a_survivors.append((stock_id, df, breakout_pv_result, pullback_result))
        except Exception as exc:  # noqa: BLE001
            logger.warning("全市場動能掃描：%s Phase A 篩選發生錯誤，略過: %s", stock_id, exc)

    logger.info(
        "全市場動能掃描：Phase A（價量條件）通過 %d 檔（動能突破候選 %d 檔・量縮回測候選 %d 檔）→ %s",
        len(phase_a_survivors), breakout_candidates, pullback_candidates,
        ", ".join(sid for sid, _, _, _ in phase_a_survivors) or "（無）",
    )

    # ---- Phase B：動能突破候選才需要補抓籌碼、檢查條件5；量縮回測候選本身已是完整命中，不需要籌碼資料 ----
    market_hit_results = []
    hit_ids = set()
    for stock_id, price_df, breakout_pv_result, pullback_result in phase_a_survivors:
        try:
            momentum_result = None
            chip_raw = None
            if breakout_pv_result:
                chip_raw = stock_data.fetch_chip_data(stock_id)
                chip_result = technical.judge_chip(chip_raw)
                ratio = momentum_screener.check_institutional_condition(
                    breakout_pv_result["volume_today"], chip_result.get("institutions")
                )
                if ratio is not None:
                    momentum_result = {**breakout_pv_result, "institutional_buy_ratio": ratio}

            if momentum_result is None and pullback_result is None:
                continue  # 動能突破沒過籌碼條件、量縮回測也沒命中 → 這檔股票這次不算命中

            row = id_to_row.get(stock_id, {})
            hit_labels = []
            if momentum_result:
                hit_labels.append("動能突破")
            if pullback_result:
                hit_labels.append("量縮回測")
            logger.info(
                "全市場動能掃描：%s（%s）命中【%s】，送入 AI 分析",
                stock_id, row.get("name", ""), "、".join(hit_labels),
            )

            result = analyze_stock(
                stock_id,
                price_df=price_df,
                chip_raw=chip_raw,
                momentum_result=momentum_result,
                pullback_result=pullback_result,
                display_name=row.get("name", ""),
            )
            if result:
                result["is_market_scan_hit"] = True
                if not result.get("sector"):
                    result["sector"] = row.get("sector", "")
                market_hit_results.append(result)
                hit_ids.add(stock_id)
        except Exception as exc:  # noqa: BLE001
            logger.error("全市場動能掃描：%s Phase B／AI 分析發生未預期錯誤，略過: %s", stock_id, exc)

    logger.info("全市場動能掃描：最終完成 AI 分析 %d 檔", len(market_hit_results))

    # ---- 全市場報價表：扣掉已經深度分析的個股（既有清單 + 這次命中的），
    #      其餘個股只保留基礎行情，給前端搜尋/排序表格用 ----
    table_df = snapshot_df[~snapshot_df["stock_id"].isin(hit_ids)]
    market_table = table_df.to_dict("records")

    return market_hit_results, market_table


def _sort_key(result: dict):
    """
    排序規則：大盤永遠排第一，其餘個股依「當日漲跌幅」由高到低排序，
    這樣資金/漲幅最集中的龍頭族群會排在報告最前面。
    漲跌幅缺資料時視為 -999，排到最後面而不是誤判成領漲。
    """
    if result.get("is_index"):
        return (0, 0)
    change_pct = result.get("technical", {}).get("change_pct")
    change_pct = change_pct if change_pct is not None else -999
    return (1, -change_pct)


def run():
    if not config.STOCK_LIST:
        logger.error("config.STOCK_LIST 為空，請在 .env 設定 STOCK_LIST")
        sys.exit(1)

    results = []
    for stock_id in config.STOCK_LIST:
        try:
            result = analyze_stock(stock_id)
            if result:
                results.append(result)
        except Exception as exc:  # noqa: BLE001 - 單檔股票出錯不應中斷整批分析
            logger.error("分析 %s 時發生未預期錯誤: %s", stock_id, exc)

    if not results:
        logger.error("所有股票分析皆失敗，未產生報告")
        sys.exit(1)

    # 【大盤多空環境濾網】：進入全市場個股篩選之前，先獨立檢測大盤是否站上月線。
    # 這不會擋掉任何選股邏輯，只是讓報告在大盤轉弱時多一層醒目的風險提示。
    market_regime = _check_market_regime()

    # 全市場「超級動能與飆股捕捉」／「N字型量縮回測」掃描：對全部上市股票跑漏斗式
    # 篩選，只有命中任一策略的個股才會補進 AI 深度分析；沒命中的個股整理成全市場
    # 報價表。可透過 .env 的 ENABLE_FULL_MARKET_SCAN=false 關閉（例如發現太耗時）。
    market_table = []
    if config.ENABLE_FULL_MARKET_SCAN:
        try:
            existing_ids = {r["stock_id"] for r in results}
            market_hit_results, market_table = _run_full_market_momentum_scan(existing_ids)
            results.extend(market_hit_results)
        except Exception as exc:  # noqa: BLE001 - 全市場掃描失敗不應影響既有龍頭股報告
            logger.error("全市場動能掃描發生未預期錯誤，本次僅顯示既有清單分析結果: %s", exc)
    else:
        logger.info("全市場動能掃描已停用（ENABLE_FULL_MARKET_SCAN=false），僅分析 config.STOCK_LIST")

    results.sort(key=_sort_key)

    # 「超級動能與飆股捕捉」：從已經算好的結果裡撈出有命中的股票，
    # 依法人買超佔比由高到低排序，給報告一個獨立的高亮清單。
    momentum_hits = sorted(
        (
            {
                "stock_id": r["stock_id"],
                "name": r.get("name") or "",
                "sector": r.get("sector") or "",
                **r["momentum"],
            }
            for r in results
            if r.get("momentum")
        ),
        key=lambda h: h["institutional_buy_ratio"],
        reverse=True,
    )
    if momentum_hits:
        logger.info(
            "超級動能與飆股捕捉：命中 %d 檔 → %s",
            len(momentum_hits),
            ", ".join(h["stock_id"] for h in momentum_hits),
        )
    else:
        logger.info("超級動能與飆股捕捉：今日無命中標的")

    # 「N字型量縮回測」：從已經算好的結果裡撈出有命中的股票，依「量縮比例」由小到大
    # 排序（比例越小代表賣壓洗得越乾淨，安全邊際越高），給報告一個獨立的高亮清單。
    pullback_hits = sorted(
        (
            {
                "stock_id": r["stock_id"],
                "name": r.get("name") or "",
                "sector": r.get("sector") or "",
                **r["pullback"],
            }
            for r in results
            if r.get("pullback")
        ),
        key=lambda h: h["volume_shrink_ratio"],
    )
    if pullback_hits:
        logger.info(
            "N字型量縮回測：命中 %d 檔 → %s",
            len(pullback_hits),
            ", ".join(h["stock_id"] for h in pullback_hits),
        )
    else:
        logger.info("N字型量縮回測：今日無命中標的")

    html = html_report.render_report(
        results,
        momentum_hits=momentum_hits,
        pullback_hits=pullback_hits,
        market_table=market_table,
        market_regime=market_regime,
    )
    path = html_report.save_report(html)
    logger.info("報告已產生：%s", path)

    site_path = html_report.save_site_index(html)
    logger.info("網站首頁已更新：%s（GitHub Actions 會把這個檔案 push 回 repo，網站就會顯示今天的報告）", site_path)

    if config.ENABLE_EMAIL:
        sent = email_report.send_report(html)
        if sent:
            logger.info("Email 已寄出")
        else:
            logger.info("Email 未寄出（未設定帳密或寄送失敗），報告仍保存在本機／網站")
    else:
        logger.info("Email 寄送已停用（改用網站瀏覽）。如需同時收信，於 .env 設定 ENABLE_EMAIL=true")

    return results


if __name__ == "__main__":
    run()
