"""
report/html_report.py
------------------------
把每檔股票的分析結果，組合成一份「今日 AI 台股晨報」HTML 報告。
使用 Jinja2 模板引擎，輸出成單一自包含的 HTML 檔案（CSS 內嵌）。
"""
import os
from datetime import datetime

from jinja2 import Environment, BaseLoader

import config

TEMPLATE = """
<!DOCTYPE html>
<html lang="zh-Hant">
<head>
<meta charset="UTF-8">
<title>今日 AI 台股晨報 {{ report_date }}</title>
<style>
  body { font-family: "Microsoft JhengHei", "PingFang TC", Arial, sans-serif;
         background:#f4f6f8; margin:0; padding:24px; color:#1a1a2e; }
  .header { text-align:center; margin-bottom:24px; }
  .header h1 { margin:0; font-size:24px; }
  .header p { color:#666; margin:4px 0 0; }
  .card { background:#fff; border-radius:12px; box-shadow:0 2px 8px rgba(0,0,0,0.08);
          padding:20px; margin-bottom:20px; max-width:640px; margin-left:auto; margin-right:auto; }
  .card-top { display:flex; justify-content:space-between; align-items:center; }
  .stock-name { font-size:18px; font-weight:bold; background:#e63946; color:#fff;
                padding:4px 10px; border-radius:6px; }
  .score { font-size:16px; font-weight:bold; color:#1d3557; }
  .row { display:flex; justify-content:space-between; margin-top:14px; font-size:14px; }
  .tag { display:inline-block; padding:2px 8px; border-radius:4px; font-size:13px; margin-right:4px; }
  .bull { background:#e8f6ee; color:#1e7e34; }
  .bear { background:#fdecec; color:#c0392b; }
  .neutral { background:#eef1f4; color:#555; }
  .metrics { display:grid; grid-template-columns:1fr 1fr; gap:6px 16px; margin-top:12px; font-size:13px; color:#333; }
  .chip-detail { margin-top:14px; }
  .chip-detail-title { font-size:13px; font-weight:bold; color:#333; margin-bottom:6px; }
  .chip-grid { display:grid; grid-template-columns:repeat(3, 1fr); gap:8px; }
  .chip-item { border-radius:8px; padding:8px 6px; text-align:center; }
  .chip-item.buy { background:#e8f6ee; }
  .chip-item.sell { background:#fdecec; }
  .chip-item.flat { background:#eef1f4; }
  .chip-name { display:block; font-size:12px; color:#555; font-weight:bold; }
  .chip-value { display:block; font-size:13px; margin-top:2px; }
  .chip-item.buy .chip-value { color:#1e7e34; }
  .chip-item.sell .chip-value { color:#c0392b; }
  .chip-streak { display:inline-block; margin-top:4px; font-size:11px; padding:1px 6px;
                 border-radius:10px; background:#fff3cd; color:#856404; }
  .ai-block { margin-top:14px; border-top:1px dashed #ddd; padding-top:10px; }
  .ai-opinion { font-size:13px; margin-bottom:6px; }
  .ai-provider { font-weight:bold; color:#457b9d; }
  .signals { font-size:12px; color:#666; margin-top:8px; }
  .footer { text-align:center; color:#999; font-size:12px; margin-top:24px; }
</style>
</head>
<body>
  <div class="header">
    <h1>📈 今日 AI 台股晨報</h1>
    <p>{{ report_date }}</p>
  </div>

  {% for s in stocks %}
  <div class="card">
    <div class="card-top">
      <span class="stock-name">{{ s.stock_id }} {{ s.name or "" }}</span>
      <span class="score">AI 綜合評分：{{ s.score }} / 100</span>
    </div>
    <div class="row">
      <span class="tag {{ 'bull' if s.technical.trend == '偏多' else ('bear' if s.technical.trend == '偏空' else 'neutral') }}">
        技術面：{{ s.technical.trend }}
      </span>
      <span class="tag {{ 'bull' if s.chip.trend == '偏多' else ('bear' if s.chip.trend == '偏空' else 'neutral') }}">
        籌碼面：{{ s.chip.trend }}
      </span>
      <span class="tag {{ 'bull' if s.ai.overall_sentiment == '偏多' else ('bear' if s.ai.overall_sentiment == '偏空' else 'neutral') }}">
        AI趨勢：{{ s.ai.overall_sentiment }}
      </span>
    </div>
    <div class="metrics">
      <div>收盤價：{{ "%.2f"|format(s.technical.close) if s.technical.close else "N/A" }} 元</div>
      <div>成交量：{{ "{:,.0f}".format(s.technical.volume) if s.technical.volume else "N/A" }} 股</div>
      <div>關鍵支撐：{{ "%.2f"|format(s.technical.support) if s.technical.support else "N/A" }}</div>
      <div>關鍵壓力：{{ "%.2f"|format(s.technical.resistance) if s.technical.resistance else "N/A" }}</div>
    </div>
    <div class="signals">
      {% for sig in s.technical.signals %}・{{ sig }}<br>{% endfor %}
    </div>

    {% if s.chip.institutions %}
    <div class="chip-detail">
      <div class="chip-detail-title">籌碼面明細（三大法人）</div>
      <div class="chip-grid">
        {% for inst in s.chip.institutions %}
        <div class="chip-item {{ 'buy' if inst.action == '買超' else ('sell' if inst.action == '賣超' else 'flat') }}">
          <span class="chip-name">{{ inst.name }}</span>
          <span class="chip-value">{{ inst.action }} {{ "{:,}".format(inst.net|abs) }} 股</span>
          {% if inst.streak_days > 1 %}
          <span class="chip-streak">{{ '連買' if inst.streak_type == 'buy' else '連賣' }} {{ inst.streak_days }} 天</span>
          {% endif %}
        </div>
        {% endfor %}
      </div>
      {% if s.chip.margin_available %}
      <div class="signals" style="margin-top:6px;">・已取得融資融券資料（詳見原始數據）</div>
      {% endif %}
    </div>
    {% else %}
    <div class="signals">
      {% for sig in s.chip.signals %}・{{ sig }}<br>{% endfor %}
    </div>
    {% endif %}
    <div class="ai-block">
      {% for op in s.ai.opinions %}
      <div class="ai-opinion"><span class="ai-provider">{{ op.provider }}：</span>{{ op.text }}</div>
      {% endfor %}
    </div>
  </div>
  {% endfor %}

  <div class="footer">
    由 AI 股票分析系統自動產生・僅供參考，不構成投資建議
  </div>
</body>
</html>
"""


def render_report(stocks: list) -> str:
    """
    Args:
        stocks: list of dict，每個 dict 至少含
                stock_id, score, technical(dict), chip(dict), ai(dict)
    Returns:
        完整 HTML 字串
    """
    env = Environment(loader=BaseLoader())
    template = env.from_string(TEMPLATE)
    report_date = datetime.now().strftime("%Y-%m-%d (%A)")
    return template.render(stocks=stocks, report_date=report_date)


def save_report(html: str, filename: str = None) -> str:
    """把 HTML 存到 output/ 目錄，回傳檔案路徑"""
    filename = filename or f"report_{datetime.now().strftime('%Y%m%d')}.html"
    path = os.path.join(config.OUTPUT_DIR, filename)
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    return path
