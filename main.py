"""
main.py
--------
系統主流程進入點。

流程：
  1. 讀取 config.STOCK_LIST 中的每檔股票
  2. data/stock_data.py 取得價量資料（yfinance）與籌碼資料（FinMind，選填）
  3. analysis/indicators.py 計算技術指標
  4. analysis/technical.py 產生技術面/籌碼面判讀
  5. ai/multi_model.py 呼叫已設定金鑰的 AI 模型（無金鑰則用規則式摘要）
  6. analysis/scoring.py 計算 AI 綜合評分 0~100
  7. report/html_report.py 產生 HTML 報告
  8. report/email_report.py 寄送 Email（無帳密則僅存檔）

可直接執行：python main.py
也是 GitHub Actions 每日排程呼叫的進入點。
"""
import logging
import sys

import config
from data import stock_data
from analysis import indicators, technical, scoring
from ai import multi_model
from report import html_report, email_report

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("main")


def analyze_stock(stock_id: str) -> dict:
    logger.info("開始分析 %s", stock_id)

    price_df = stock_data.fetch_price_data(stock_id)
    if price_df is None or price_df.empty:
        logger.warning("%s 無價量資料，略過此檔", stock_id)
        return None

    price_df = indicators.compute_all_indicators(price_df)
    tech_result = technical.judge_technical(price_df)

    chip_raw = stock_data.fetch_chip_data(stock_id)
    chip_result = technical.judge_chip(chip_raw)

    ai_context = {
        "close": tech_result.get("close"),
        "tech_trend": tech_result.get("trend"),
        "tech_signals": tech_result.get("signals", []),
        "chip_trend": chip_result.get("trend"),
        "chip_signals": chip_result.get("signals", []),
    }
    ai_result = multi_model.analyze_all(stock_id, ai_context)

    score = scoring.compute_score(tech_result, chip_result, ai_result["overall_sentiment"])

    return {
        "stock_id": stock_id,
        "score": score,
        "technical": tech_result,
        "chip": chip_result,
        "ai": ai_result,
    }


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

    html = html_report.render_report(results)
    path = html_report.save_report(html)
    logger.info("報告已產生：%s", path)

    sent = email_report.send_report(html)
    if sent:
        logger.info("Email 已寄出")
    else:
        logger.info("Email 未寄出（未設定帳密或寄送失敗），報告仍保存在本機")

    return results


if __name__ == "__main__":
    run()
