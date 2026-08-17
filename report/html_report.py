"""
report/html_report.py
------------------------
把每檔股票的分析結果，組合成一份「今日 AI 台股晨報」HTML 報告。
使用 Jinja2 模板引擎，輸出成單一 HTML 檔案（CSS 內嵌、圖示為內嵌 SVG，不依賴任何外部
JS/CSS 套件）。唯一的外部資源是 Google Fonts 的網頁字型（見下方「字體」說明）；這份
報告現在是發布成網站而非寄信附件，所以可以放心掛外部字型連結，若載入失敗會自動退回
系統字體，不影響可讀性。

視覺系統說明（Premium Dark 版）：
  - 深色主題：頁面用近黑 #08080a，卡片用比頁面亮一階的 #141416（elevated surface），
    靠「一階亮度差 + hairline 邊框 + 柔和陰影」堆出層次感，而不是用粗黑框線分隔，
    這是目前主流高質感金融 App（Apple Stocks／Robinhood／Bloomberg 深色模式）共通的作法。
  - 字體：西文／數字用 Inter（Linear／Vercel／Stripe 等主流 SaaS／金融儀表板慣用字體，
    數字造型與 tabular-nums 對齊效果比系統預設字體更精緻），中文用 Noto Sans TC，兩者
    都掛 Google Fonts CDN。這樣做的理由是這份報告會被不同平台的使用者透過 GitHub Pages
    瀏覽——只依賴系統字體的話，Mac 上的 PingFang TC 好看，但 Windows 預設的 Microsoft
    JhengHei 觀感明顯較舊，掛網頁字型可以讓所有平台看到同一套、有經過設計的字體，而不是
    因平台不同而落差很大。字體堆疊仍保留原本的系統字體當 fallback（見 --font-sans），
    網路環境擋掉 Google Fonts 時不影響可讀性，只是退回原本的樣子。
  - 配色採台股慣例：紅漲、綠跌（跟美股習慣相反）。這是一個「polarity（漲跌極性）」
    編碼，全報告統一用同一組紅/綠 diverging pair：熱力圖、個股卡片的漲跌幅徽章、
    技術面/籌碼面/AI趨勢的偏多偏空標籤、三大法人買超賣超方塊，全部同一套規則，
    不會出現「這裡紅色是好、那裡紅色是壞」的不一致。
  - 兩種紅/綠用法分工明確：
      1) 熱力圖色塊 = 實色（依漲跌幅級距由淡到濃），越濃代表資金越集中/漲跌越劇烈。
      2) 其餘標籤/徽章 = 半透明「玻璃感」色塊（低透明度背景 + 高飽和文字 + 同色細邊框），
         這是深色 UI 常見的「輕量強調」作法，避免整頁被大面積飽和色淹沒。
  - 顏色永遠不是唯一線索：每個色塊都同時帶「▲/▼/－」符號與文字（偏多/偏空/買超/賣超），
    對色盲讀者或黑白列印一樣可讀。
  - 產業龍頭標籤、AI 評分、各區塊標題圖示徽章用藍/紫/琥珀色系（category／magnitude
    用途），跟紅綠漲跌極性分開，避免「這是分類還是漲跌」混淆（沿用 dataviz 準則：
    狀態色與分類色不共用同一色相）。
  - AI 綜合評分改用「環形量表（ring meter）」呈現：填充弧代表分數，底色是同色系
    較淡的一階，讓分數強弱一眼可讀，比純文字徽章更有儀表板的高級感。
  - 每個區塊標題、指標欄、三大法人卡片、AI 意見都補上對應的線性圖示（inline SVG，
    不吃外部字型/圖示庫），讓整份報告更有「圖示感」也更好掃視。
  - 這份報告現在是發布成「網站」（GitHub Pages，見 config.SITE_DIR），不再是寄信附件，
    所以可以放心用 <script> 做前端互動：
      1) 每張股票卡片補上近 20 日收盤價的走勢小圖（sparkline，內嵌 SVG，紅漲綠跌）。
      2) 頁面頂端有搜尋框（比對代號／名稱／產業）與排序下拉（漲跌幅／AI評分 高低），
         純前端 JS 即時篩選、重新排序卡片，不需要重新整理頁面或連後端。
"""
import json
import math
import os
from datetime import datetime

from jinja2 import Environment, BaseLoader

import config

# ---------------------------------------------------------------------------
# 熱力圖色塊（實色，依漲跌幅級距由淡到濃）。紅=漲(polarity正) 綠=跌(polarity負)。
# 一律回傳 (背景色, 文字色, 方向符號)，符號讓色盲讀者不用靠顏色也能判斷漲跌。
# ---------------------------------------------------------------------------
_HEAT_POS_LEVELS = [  # 漲：紅，越漲越濃，強級距帶柔光
    (3.0, "#c62b2b", "#ffffff", "0 0 18px rgba(198,43,43,0.45)"),
    (1.0, "#6b2626", "#ffb4ab", "none"),
    (0.0001, "#3b2020", "#ff8a80", "none"),
]
_HEAT_NEG_LEVELS = [  # 跌：綠，越跌越濃，強級距帶柔光
    (3.0, "#1f7a48", "#ffffff", "0 0 18px rgba(31,122,72,0.45)"),
    (1.0, "#1f4d34", "#8fe8ac", "none"),
    (0.0001, "#1c3327", "#7ee3a3", "none"),
]
_HEAT_NEUTRAL = ("#232326", "#9a9994", "none")


def _heat_style(change_pct):
    """依漲跌幅回傳 (背景色, 文字色, 符號, 柔光陰影)。用在熱力圖色塊與卡頭漲跌徽章。"""
    if change_pct is None or change_pct == 0:
        bg, text, shadow = _HEAT_NEUTRAL
        return bg, text, "－", shadow
    abs_pct = abs(change_pct)
    levels = _HEAT_POS_LEVELS if change_pct > 0 else _HEAT_NEG_LEVELS
    arrow = "▲" if change_pct > 0 else "▼"
    for threshold, bg, text, shadow in levels:
        if abs_pct >= threshold:
            return bg, text, arrow, shadow
    bg, text, shadow = _HEAT_NEUTRAL
    return bg, text, arrow, shadow


# ---------------------------------------------------------------------------
# 「玻璃感」半透明色塊：低透明度背景 + 高飽和文字 + 同色細邊框。
# 用在技術面/籌碼面/AI趨勢的狀態標籤、三大法人買賣超方塊、卡頭漲跌幅小徽章。
# ---------------------------------------------------------------------------
_GLASS_POS = ("rgba(255,69,58,0.16)", "#ff6b60", "rgba(255,69,58,0.38)")   # 紅：漲/多/買
_GLASS_NEG = ("rgba(48,209,88,0.15)", "#3ddc74", "rgba(48,209,88,0.36)")  # 綠：跌/空/賣
_GLASS_NEUTRAL = ("rgba(255,255,255,0.06)", "#9a9994", "rgba(255,255,255,0.14)")

_STATUS_STYLE = {
    "偏多": _GLASS_POS,
    "偏空": _GLASS_NEG,
    "中性": _GLASS_NEUTRAL,
    "買超": _GLASS_POS,
    "賣超": _GLASS_NEG,
    "買賣平衡": _GLASS_NEUTRAL,
}


def _status_style(label):
    """回傳 (背景色, 文字色, 邊框色)，並附帶對應的方向符號。"""
    bg, text, border = _STATUS_STYLE.get(label, _GLASS_NEUTRAL)
    if label in ("偏多", "買超"):
        arrow = "▲"
    elif label in ("偏空", "賣超"):
        arrow = "▼"
    else:
        arrow = "－"
    return bg, text, arrow, border


# ---------------------------------------------------------------------------
# AI 綜合評分環形量表：SVG 圓弧的 circumference / dash 長度由 Python 算好，
# 模板只需要照著畫，避免在 Jinja2 裡做三角函數運算。
# ---------------------------------------------------------------------------
_SCORE_RING_RADIUS = 30
_SCORE_RING_CIRCUMFERENCE = round(2 * math.pi * _SCORE_RING_RADIUS, 2)


def _score_ring(score: float):
    score = max(0, min(100, score or 0))
    dash = round(_SCORE_RING_CIRCUMFERENCE * (score / 100), 2)
    return dash, _SCORE_RING_CIRCUMFERENCE


# ---------------------------------------------------------------------------
# 走勢小圖（sparkline）：近 N 日收盤價序列 → 內嵌 SVG 折線圖。
# 面積填色用同一色相 ~13% 透明度的「wash」，終點帶一顆有 surface 色圈的端點，
# 顏色沿用漲跌 polarity（紅漲綠跌），跟報告其他地方的規則一致。
# ---------------------------------------------------------------------------
def _sparkline_svg(values, change_pct):
    if not values or len(values) < 2:
        return ""
    width, height, pad_y, pad_x = 128, 34, 4, 2
    n = len(values)
    lo, hi = min(values), max(values)
    span = hi - lo

    def _y(v):
        if span == 0:
            return height / 2
        return pad_y + (1 - (v - lo) / span) * (height - 2 * pad_y)

    xs = [round(pad_x + i * (width - 2 * pad_x) / (n - 1), 2) for i in range(n)]
    ys = [round(_y(v), 2) for v in values]

    pos = (change_pct if change_pct is not None else (values[-1] - values[0])) >= 0
    stroke = "#ff6b60" if pos else "#3ddc74"
    fill = "rgba(255,69,58,0.14)" if pos else "rgba(48,209,88,0.13)"

    points = " ".join(f"{x},{y}" for x, y in zip(xs, ys))
    area = f"M{xs[0]},{height} L" + " L".join(f"{x},{y}" for x, y in zip(xs, ys)) + f" L{xs[-1]},{height} Z"

    return (
        f'<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}">'
        f'<path d="{area}" fill="{fill}" stroke="none"/>'
        f'<polyline points="{points}" fill="none" stroke="{stroke}" stroke-width="2" '
        f'stroke-linecap="round" stroke-linejoin="round"/>'
        f'<circle cx="{xs[-1]}" cy="{ys[-1]}" r="4.5" fill="{stroke}" stroke="#1b1b1e" stroke-width="2"/>'
        f'</svg>'
    )


def _prepare_stocks(stocks: list) -> list:
    """幫每檔股票補上顏色/符號/圖示欄位，不修改原始傳入的 list/dict。"""
    prepared = []
    for s in stocks:
        s = dict(s)
        change_pct = s.get("technical", {}).get("change_pct")
        bg, text, arrow, shadow = _heat_style(change_pct)
        s["heat_bg"], s["heat_text"], s["heat_arrow"], s["heat_shadow"] = bg, text, arrow, shadow
        s["change_pct"] = change_pct

        tech = dict(s.get("technical") or {})
        tech["tag_bg"], tech["tag_text"], tech["tag_arrow"], tech["tag_border"] = _status_style(tech.get("trend"))
        tech["sparkline_svg"] = _sparkline_svg(tech.get("price_history") or [], change_pct)
        s["technical"] = tech

        chip = dict(s.get("chip") or {})
        chip["tag_bg"], chip["tag_text"], chip["tag_arrow"], chip["tag_border"] = _status_style(chip.get("trend"))
        institutions = []
        for inst in chip.get("institutions", []):
            inst = dict(inst)
            inst["tag_bg"], inst["tag_text"], inst["tag_arrow"], inst["tag_border"] = _status_style(inst.get("action"))
            institutions.append(inst)
        chip["institutions"] = institutions
        s["chip"] = chip

        ai = dict(s.get("ai") or {})
        ai["tag_bg"], ai["tag_text"], ai["tag_arrow"], ai["tag_border"] = _status_style(ai.get("overall_sentiment"))
        s["ai"] = ai

        # 評分（0~100）用單一藍色序列（sequential，magnitude 編碼），跟漲跌極性的紅綠分開，
        # 避免「評分」跟「漲跌」共用同一套顏色語彙造成混淆；改用環形量表視覺化。
        score = s.get("score", 0) or 0
        if score >= 70:
            s["score_text"] = "#5b9df0"
        elif score >= 40:
            s["score_text"] = "#9dc4f2"
        else:
            s["score_text"] = "#5b5b5e"
        s["score_ring_dash"], s["score_ring_circumference"] = _score_ring(score)
        s["score_ring_radius"] = _SCORE_RING_RADIUS

        # 「超級動能與飆股捕捉」／「N字型量縮回測」命中標記：main.py 的
        # analyze_stock() 已經把 analysis/momentum_screener.py 的結果分別放進
        # s["momentum"]／s["pullback"]（dict 或 None），這裡只是轉成模板好用的
        # 布林值，方便在卡頭加徽章（兩者可以同時成立，各自獨立顯示）。
        s["is_momentum_hit"] = bool(s.get("momentum"))
        s["is_pullback_hit"] = bool(s.get("pullback"))

        prepared.append(s)
    return prepared


# ---------------------------------------------------------------------------
# 內嵌 SVG 圖示（線性、currentColor 描邊，viewBox 0 0 24 24）。
# 不用外部圖示字型／圖片，確保單一 HTML 檔案在瀏覽器與郵件用戶端都能正確顯示。
# ---------------------------------------------------------------------------
ICONS = {
    # 資金族群熱力圖：2x2 方格，代表熱力圖色塊
    "heat": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="7.5" height="7.5" rx="1.6"/><rect x="13.5" y="3" width="7.5" height="7.5" rx="1.6"/><rect x="3" y="13.5" width="7.5" height="7.5" rx="1.6"/><rect x="13.5" y="13.5" width="7.5" height="7.5" rx="1.6"/></svg>',
    # 技術面：趨勢折線 + 箭頭
    "chart": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M3 17l5.5-5.5 4 4L21 7"/><path d="M15 7h6v6"/></svg>',
    # 籌碼面／三大法人：銀行/機構列柱
    "bank": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M3 10.5 12 4l9 6.5"/><path d="M5 10.5V20M10 10.5V20M14 10.5V20M19 10.5V20"/><path d="M3 20h18"/></svg>',
    # AI：兩顆星芒，象徵 AI／智慧分析
    "ai": '<svg viewBox="0 0 24 24" fill="currentColor" stroke="none"><path d="M11.5 2.5c.5 2.7 1 4.4 2.2 5.6 1.2 1.2 2.9 1.7 5.6 2.2-2.7.5-4.4 1-5.6 2.2-1.2 1.2-1.7 2.9-2.2 5.6-.5-2.7-1-4.4-2.2-5.6C8.1 11.3 6.4 10.8 3.7 10.3c2.7-.5 4.4-1 5.6-2.2 1.2-1.2 1.7-2.9 2.2-5.6Z"/><path d="M18.5 15c.3 1.4.6 2.2 1.3 2.9.7.7 1.5 1 2.9 1.3-1.4.3-2.2.6-2.9 1.3-.7.7-1 1.5-1.3 2.9-.3-1.4-.6-2.2-1.3-2.9-.7-.7-1.5-1-2.9-1.3 1.4-.3 2.2-.6 2.9-1.3.7-.7 1-1.5 1.3-2.9Z"/></svg>',
    # 收盤價：價格標籤
    "tag": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M3 11.6V5.5A2 2 0 0 1 5 3.5h6.1a2 2 0 0 1 1.4.6l8 8a2 2 0 0 1 0 2.8l-6.6 6.6a2 2 0 0 1-2.8 0l-8-8A2 2 0 0 1 3 11.6Z"/><circle cx="8" cy="8.5" r="1.3" fill="currentColor" stroke="none"/></svg>',
    # 成交量：長條圖
    "volume": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="3.5" y="12" width="3.4" height="8" rx="1"/><rect x="10.3" y="7" width="3.4" height="13" rx="1"/><rect x="17.1" y="3.5" width="3.4" height="16.5" rx="1"/></svg>',
    # 關鍵支撐：箭頭向下指向底線
    "support": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3.5v12.5"/><path d="M7 12l5 5 5-5"/><path d="M4 20.5h16"/></svg>',
    # 關鍵壓力：箭頭向上指向頂線
    "resistance": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M4 3.5h16"/><path d="M7 12l5-5 5 5"/><path d="M12 8v12.5"/></svg>',
    # 外資：地球，象徵國際資金
    "foreign": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="8.5"/><path d="M3.5 12h17"/><path d="M12 3.5c2.3 2.3 3.6 5.3 3.6 8.5s-1.3 6.2-3.6 8.5c-2.3-2.3-3.6-5.3-3.6-8.5S9.7 5.8 12 3.5Z"/></svg>',
    # 投信：分層堆疊，象徵基金集資
    "trust": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3.5 20.5 8 12 12.5 3.5 8 12 3.5Z"/><path d="M3.5 12 12 16.5 20.5 12"/><path d="M3.5 16 12 20.5 20.5 16"/></svg>',
    # 自營商：公事包，象徵自營交易
    "dealer": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="8" width="18" height="12" rx="2"/><path d="M8.5 8V6.2a1.8 1.8 0 0 1 1.8-1.8h3.4a1.8 1.8 0 0 1 1.8 1.8V8"/><path d="M3 13.5h18"/></svg>',
    # 搜尋：放大鏡
    "search": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="10.5" cy="10.5" r="6.5"/><path d="M20 20l-4.3-4.3"/></svg>',
    # 超級動能與飆股捕捉：火箭，象徵帶量噴出突破
    "rocket": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M12 2.5c3 2.2 5 6.3 5 10.3 0 2.1-.9 4-2 5.2l-3 2.7-3-2.7c-1.1-1.2-2-3.1-2-5.2 0-4 2-8.1 5-10.3Z"/><circle cx="12" cy="10" r="1.7" fill="currentColor" stroke="none"/><path d="M9.3 16.8l-2.1 3.7M14.7 16.8l2.1 3.7"/></svg>',
    # N字型量縮回測：上升折線中間帶一個小回檔凹點（回測不破前低），下方一條防線
    "pullback": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M3 15.5 8 9 11.5 12.6 15 6.5 21 3.5"/><circle cx="11.5" cy="12.6" r="1.6" fill="currentColor" stroke="none"/><path d="M7.2 18h8.6"/></svg>',
}

_INST_ICON_KEY = {"外資": "foreign", "投信": "trust", "自營商": "dealer"}


def _inst_icon(name):
    return ICONS.get(_INST_ICON_KEY.get(name, "dealer"), "")


TEMPLATE = """
<!DOCTYPE html>
<html lang="zh-Hant">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>今日 AI 台股晨報 {{ report_date }}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800;900&family=Noto+Sans+TC:wght@400;500;700;900&display=swap" rel="stylesheet">
<style>
  :root {
    color-scheme: dark;
    --page:          #08080a;
    --surface:       #141416;
    --surface-2:     #1b1b1e;
    --ink-1:         #f5f5f4;
    --ink-2:         #b8b7b2;
    --ink-muted:     #8b8a85;
    --grid:          rgba(255,255,255,0.08);
    --border:        rgba(255,255,255,0.08);
    --border-strong: rgba(255,255,255,0.14);
    --accent:        #3987e5;
    --accent-tint:   rgba(57,135,229,0.14);
    --accent-border: rgba(57,135,229,0.30);
    --violet:        #9085e9;
    --violet-tint:   rgba(144,133,233,0.14);
    --violet-border: rgba(144,133,233,0.32);
    --warn:          #fab219;
    --warn-tint:     rgba(250,178,25,0.14);
    --warn-border:   rgba(250,178,25,0.32);
    --teal:          #2dd4bf;
    --teal-tint:     rgba(45,212,191,0.14);
    --teal-border:   rgba(45,212,191,0.34);
    --shadow-card:   0 24px 48px -28px rgba(0,0,0,0.75), 0 1px 0 rgba(255,255,255,0.03) inset;
    /* Inter 扛西文／數字（歐美主流金融 App 慣用的字重與數字造型，tabular-nums 對齊效果
       比系統字體更漂亮），Noto Sans TC 扛中文（確保 Windows／Android 使用者看到的中文
       字重跟 Mac 上的 PingFang TC 一樣精緻，不會因為平台不同而落差很大）。兩者都掛在
       Google Fonts CDN，若被網路環境擋掉會自動退回原本的系統字體，不影響可讀性。 */
    --font-sans: 'Inter', 'Noto Sans TC', system-ui, -apple-system, "PingFang TC", "Microsoft JhengHei", sans-serif;
  }
  * { box-sizing: border-box; }
  html { background: var(--page); }
  body {
    font-family: var(--font-sans);
    background:
      radial-gradient(ellipse 1400px 500px at 50% -10%, rgba(57,135,229,0.10), transparent 60%),
      var(--page);
    margin: 0;
    padding: 36px 32px 56px;
    color: var(--ink-1);
    line-height: 1.5;
    -webkit-font-smoothing: antialiased;
    text-rendering: optimizeLegibility;
  }
  /* 整版寬螢幕網站：外層容器撐到 1600px 用滿桌面寬度，不再只集中在畫面正中間一小條。
     標題／搜尋排序列／熱力圖這類「一段式」內容用 .narrow 收窄成易讀寬度置中；
     股票卡片清單（#stock-list）則是寬版多欄網格，畫面越寬就自動排出越多欄，
     內容才會真的把整個版面撐滿，而不是兩側留一大片空白。
     行動裝置（<640px）在下面的 media query 裡全部收回成單欄，維持手機體驗。 */
  .wrap { max-width: 1600px; margin: 0 auto; }
  .narrow { max-width: 760px; margin: 0 auto; }
  svg { display: block; }

  /* ---------- Icon badges (section / block title marks) ---------- */
  .icon-badge {
    display: inline-flex; align-items: center; justify-content: center;
    width: 26px; height: 26px; border-radius: 8px; flex-shrink: 0;
    border: 1px solid transparent;
  }
  .icon-badge svg { width: 14px; height: 14px; }
  .badge-accent { background: var(--accent-tint); color: var(--accent); border-color: var(--accent-border); }
  .badge-violet { background: var(--violet-tint); color: var(--violet); border-color: var(--violet-border); }
  .badge-amber  { background: var(--warn-tint); color: var(--warn); border-color: var(--warn-border); }
  .icon-inline { width: 13px; height: 13px; flex-shrink: 0; }

  /* ---------- Market regime alert（大盤多空環境濾網，跌破月線時強制顯示） ---------- */
  .market-alert {
    display: flex; align-items: flex-start; gap: 14px;
    background: linear-gradient(135deg, #c62b2b, #7a1f1f);
    border: 1px solid rgba(255,255,255,0.16);
    border-radius: 16px;
    padding: 18px 20px;
    margin-bottom: 22px;
    box-shadow: 0 0 0 1px rgba(198,43,43,0.4), 0 20px 44px -22px rgba(198,43,43,0.65);
  }
  .market-alert-icon { font-size: 26px; line-height: 1.1; flex-shrink: 0; }
  .market-alert-body { display: flex; flex-direction: column; gap: 4px; }
  .market-alert-title { font-size: 15.5px; font-weight: 800; color: #ffffff; letter-spacing: -0.01em; }
  .market-alert-text { font-size: 13px; color: rgba(255,255,255,0.94); line-height: 1.6; }
  .market-alert-meta { font-size: 11.5px; color: rgba(255,255,255,0.74); margin-top: 2px; font-variant-numeric: tabular-nums; }

  /* ---------- Header ---------- */
  .header { text-align: center; padding-bottom: 34px; margin-bottom: 34px; border-bottom: 1px solid var(--grid); }
  .brand-mark {
    width: 46px; height: 46px; border-radius: 13px; margin: 0 auto 16px;
    display: flex; align-items: center; justify-content: center;
    background: linear-gradient(135deg, rgba(144,133,233,0.28), rgba(57,135,229,0.22));
    border: 1px solid rgba(144,133,233,0.35);
    color: #b3a8f5;
  }
  .brand-mark svg { width: 22px; height: 22px; }
  .header .eyebrow {
    font-size: 11.5px; letter-spacing: 0.16em; text-transform: uppercase;
    color: var(--accent); font-weight: 700; margin-bottom: 12px;
  }
  .header h1 {
    margin: 0; font-size: 31px; font-weight: 800; letter-spacing: -0.025em; line-height: 1.25;
    background: linear-gradient(180deg, #ffffff 0%, #cfd3da 100%);
    -webkit-background-clip: text; background-clip: text; color: transparent;
  }
  .header p { color: var(--ink-muted); margin: 10px 0 0; font-size: 13px; font-variant-numeric: tabular-nums; }

  /* ---------- Toolbar (search / sort) ---------- */
  .toolbar { padding: 16px 18px; }
  .toolbar-row { display: flex; gap: 10px; flex-wrap: wrap; }
  .search-box {
    flex: 1; min-width: 180px; display: flex; align-items: center; gap: 9px;
    background: var(--surface-2); border: 1px solid var(--border); border-radius: 12px; padding: 0 14px;
  }
  .search-box svg { width: 15px; height: 15px; color: var(--ink-muted); flex-shrink: 0; }
  .search-box input {
    flex: 1; background: transparent; border: none; outline: none; color: var(--ink-1);
    font-size: 13px; padding: 11px 0; font-family: inherit;
  }
  .search-box input::placeholder { color: var(--ink-muted); }
  .sort-select {
    background: var(--surface-2); border: 1px solid var(--border); border-radius: 12px;
    color: var(--ink-1); font-size: 13px; padding: 11px 14px; font-family: inherit; outline: none;
  }
  .toolbar-count { margin-top: 10px; font-size: 11.5px; color: var(--ink-muted); }
  .empty-state {
    display: none; text-align: center; padding: 36px 16px; color: var(--ink-muted); font-size: 13px;
    background: var(--surface); border: 1px solid var(--border); border-radius: 18px; margin-bottom: 18px;
  }

  /* ---------- Momentum breakout highlight (超級動能與飆股捕捉) ---------- */
  .badge-momentum { background: rgba(250,178,25,0.18); color: var(--warn); border-color: rgba(250,178,25,0.42); }
  .section.momentum {
    border-color: rgba(250,178,25,0.30);
    background: linear-gradient(165deg, rgba(250,178,25,0.09), var(--surface) 55%);
    box-shadow: var(--shadow-card), 0 0 0 1px rgba(250,178,25,0.10) inset;
  }
  .momentum-list { display: flex; flex-direction: column; gap: 8px; }
  .momentum-row {
    display: flex; align-items: center; gap: 8px 16px; flex-wrap: wrap;
    background: var(--surface-2); border: 1px solid var(--border); border-radius: 12px;
    padding: 12px 14px; text-decoration: none; color: inherit;
    transition: border-color .15s ease;
  }
  .momentum-row:hover { border-color: rgba(250,178,25,0.5); }
  .momentum-id { display: flex; flex-direction: column; min-width: 64px; }
  .momentum-code { font-size: 14.5px; font-weight: 800; color: var(--ink-1); }
  .momentum-name { font-size: 11px; color: var(--ink-muted); }
  .momentum-metrics { display: flex; gap: 16px 20px; flex-wrap: wrap; margin-left: auto; }
  .momentum-metric { text-align: right; }
  .momentum-metric-label { display: block; font-size: 9.5px; color: var(--ink-muted); font-weight: 600; margin-bottom: 2px; }
  .momentum-metric-value { font-size: 13px; font-weight: 800; font-variant-numeric: tabular-nums; color: var(--ink-1); }
  .momentum-metric-value.highlight { color: var(--warn); }
  .momentum-card-badge {
    display: inline-flex; align-items: center; gap: 4px;
    font-size: 11.5px; font-weight: 700; padding: 3px 10px; border-radius: 999px;
    background: rgba(250,178,25,0.16); color: var(--warn); border: 1px solid rgba(250,178,25,0.4);
  }
  .momentum-card-badge svg { width: 12px; height: 12px; }

  /* ---------- Pullback entry highlight (N字型量縮回測) ---------- */
  .badge-pullback { background: var(--teal-tint); color: var(--teal); border-color: var(--teal-border); }
  .section.pullback {
    border-color: rgba(45,212,191,0.30);
    background: linear-gradient(165deg, rgba(45,212,191,0.09), var(--surface) 55%);
    box-shadow: var(--shadow-card), 0 0 0 1px rgba(45,212,191,0.10) inset;
  }
  .pullback-list { display: flex; flex-direction: column; gap: 8px; }
  .pullback-row {
    display: flex; align-items: center; gap: 8px 16px; flex-wrap: wrap;
    background: var(--surface-2); border: 1px solid var(--border); border-radius: 12px;
    padding: 12px 14px; text-decoration: none; color: inherit;
    transition: border-color .15s ease;
  }
  .pullback-row:hover { border-color: rgba(45,212,191,0.5); }
  .pullback-id { display: flex; flex-direction: column; min-width: 64px; }
  .pullback-code { font-size: 14.5px; font-weight: 800; color: var(--ink-1); }
  .pullback-name { font-size: 11px; color: var(--ink-muted); }
  .pullback-metrics { display: flex; gap: 16px 20px; flex-wrap: wrap; margin-left: auto; }
  .pullback-metric { text-align: right; }
  .pullback-metric-label { display: block; font-size: 9.5px; color: var(--ink-muted); font-weight: 600; margin-bottom: 2px; }
  .pullback-metric-value { font-size: 13px; font-weight: 800; font-variant-numeric: tabular-nums; color: var(--ink-1); }
  .pullback-metric-value.highlight { color: var(--teal); }
  /* 潛在停損幅度沿用全報告「綠=跌」的漲跌極性配色，不用 --teal（那是這個區塊的分類色，
     跟漲跌極性是兩套不同語意，混用會造成「這裡綠色是分類、那裡綠色是跌」的不一致）。 */
  .pullback-metric-value.risk { color: #3ddc74; }
  .pullback-card-badge {
    display: inline-flex; align-items: center; gap: 4px;
    font-size: 11.5px; font-weight: 700; padding: 3px 10px; border-radius: 999px;
    background: var(--teal-tint); color: var(--teal); border: 1px solid var(--teal-border);
  }
  .pullback-card-badge svg { width: 12px; height: 12px; }

  /* ---------- Sparkline (price trend mini chart) ---------- */
  .sparkline-wrap { margin-top: 12px; }
  .sparkline-label { display: block; font-size: 10px; color: var(--ink-muted); margin-bottom: 4px; }

  /* ---------- 全市場報價總覽（動能掃描沒命中的個股，純前端渲染的大表格） ---------- */
  .market-table-toolbar { display: flex; align-items: center; gap: 14px; flex-wrap: wrap; margin: 14px 0 12px; }
  .market-table-toolbar .search-box { max-width: 320px; }
  .market-table-count { font-size: 11.5px; color: var(--ink-muted); margin-left: auto; }
  .market-table-scroll { overflow-x: auto; border: 1px solid var(--border); border-radius: 14px; max-height: 640px; overflow-y: auto; }
  table.market-table-el { width: 100%; border-collapse: collapse; font-size: 12.5px; font-variant-numeric: tabular-nums; }
  table.market-table-el th, table.market-table-el td { padding: 9px 14px; text-align: left; white-space: nowrap; }
  table.market-table-el td.num, table.market-table-el th.num { text-align: right; }
  table.market-table-el thead th {
    position: sticky; top: 0; background: var(--surface-2); color: var(--ink-muted);
    font-weight: 700; font-size: 11px; letter-spacing: 0.02em; cursor: pointer; user-select: none;
    border-bottom: 1px solid var(--border);
  }
  table.market-table-el thead th:hover { color: var(--ink-1); }
  table.market-table-el thead th.sorted-asc::after { content: " \25B2"; color: var(--accent); }
  table.market-table-el thead th.sorted-desc::after { content: " \25BC"; color: var(--accent); }
  table.market-table-el tbody tr { border-bottom: 1px solid var(--grid); }
  table.market-table-el tbody tr:last-child { border-bottom: none; }
  table.market-table-el tbody tr:hover { background: rgba(255,255,255,0.03); }
  table.market-table-el td.mkt-code { font-weight: 700; color: var(--ink-1); }

  /* ---------- Section shell (heatmap + each card use this) ---------- */
  .section {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 18px;
    box-shadow: var(--shadow-card);
    padding: 26px 26px 28px;
    margin-bottom: 20px;
    position: relative;
    overflow: hidden;
  }
  .section::before {
    content: ""; position: absolute; top: 0; left: 0; right: 0; height: 1px;
    background: linear-gradient(90deg, transparent, rgba(255,255,255,0.28), transparent);
  }

  /* ---------- Stock card grid（寬版多欄，畫面越寬欄數越多） ---------- */
  #stock-list {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(480px, 1fr));
    gap: 20px;
    align-items: start;
    max-width: 1600px;
    margin: 0 auto;
  }
  #stock-list .stock-card { margin-bottom: 0; }   /* 間距改由 grid gap 控制，避免跟 .section 的 margin-bottom 疊加 */

  @media (max-width: 640px) {
    #stock-list { grid-template-columns: 1fr; }   /* 手機收回成單欄，維持原本手機閱讀體驗 */
    body { padding-left: 16px; padding-right: 16px; }   /* 手機邊距縮回，內容不被兩側留白吃掉太多寬度 */
    .heatmap-grid { grid-template-columns: repeat(2, 1fr); }   /* 熱力圖在窄螢幕改 2 欄，格子不會過窄 */
  }

  .section-title-row { display: flex; align-items: center; gap: 10px; margin-bottom: 4px; }
  .section-title { font-size: 13.5px; font-weight: 700; color: var(--ink-1); letter-spacing: -0.01em; }
  .section-caption { font-size: 12px; color: var(--ink-muted); margin-bottom: 16px; margin-left: 36px; }

  /* ---------- Heatmap ---------- */
  .heatmap-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px; }
  .heatmap-tile {
    border-radius: 12px; padding: 12px 8px; text-align: center;
    display: flex; flex-direction: column; gap: 3px;
    border: 1px solid rgba(255,255,255,0.06);
  }
  .heatmap-sector { font-size: 10.5px; opacity: 0.85; line-height: 1.3; min-height: 2.4em; }
  .heatmap-code { font-size: 13.5px; font-weight: 800; font-variant-numeric: tabular-nums; }
  .heatmap-pct { font-size: 13px; font-weight: 700; font-variant-numeric: tabular-nums; }

  /* ---------- Stock card ---------- */
  .card-head {
    display: flex; align-items: center; justify-content: space-between; gap: 12px;
    padding-bottom: 18px; margin-bottom: 18px; border-bottom: 1px solid var(--grid);
  }
  .card-id-row { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
  .card-code { font-size: 22px; font-weight: 800; letter-spacing: -0.015em; color: var(--ink-1); }
  .card-name { font-size: 13px; color: var(--ink-2); }
  .chip-tag {
    display: inline-flex; align-items: center; gap: 4px;
    font-size: 11.5px; font-weight: 700; padding: 3px 10px; border-radius: 999px;
    border: 1px solid transparent;
  }
  .sector-chip { background: var(--violet-tint); color: var(--violet); border-color: var(--violet-border); }
  .pct-chip { font-variant-numeric: tabular-nums; border: none; }

  /* ---------- Score ring meter ---------- */
  .score-tile { display: flex; flex-direction: column; align-items: center; flex-shrink: 0; }
  .score-tile .stat-label { font-size: 10.5px; color: var(--ink-muted); margin-bottom: 4px; text-align: center; }
  .score-ring-num { font-size: 19px; font-weight: 800; font-variant-numeric: tabular-nums; }
  .score-ring-den { font-size: 8.5px; font-weight: 600; fill: var(--ink-muted); }

  .status-row { display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 20px; }
  .status-tag {
    display: inline-flex; align-items: center; gap: 5px;
    font-size: 12.5px; font-weight: 700; padding: 6px 12px; border-radius: 999px;
    border: 1px solid transparent;
  }
  .status-tag .label { color: var(--ink-muted); font-weight: 500; }

  .metrics-row {
    display: grid; grid-template-columns: repeat(4, 1fr); gap: 0;
    background: var(--surface-2);
    border: 1px solid var(--border); border-radius: 12px; overflow: hidden; margin-bottom: 18px;
  }
  .metric-cell { padding: 14px 10px; text-align: center; border-left: 1px solid var(--border); }
  .metric-cell:first-child { border-left: none; }
  .metric-icon { display: flex; justify-content: center; margin-bottom: 6px; color: var(--ink-muted); }
  .metric-icon svg { width: 14px; height: 14px; }
  .metric-label { font-size: 10.5px; color: var(--ink-muted); margin-bottom: 5px; letter-spacing: 0.01em; }
  .metric-value { font-size: 14.5px; font-weight: 700; font-variant-numeric: tabular-nums; color: var(--ink-1); }

  .block-title {
    display: flex; align-items: center; gap: 9px;
    font-size: 12px; font-weight: 700; color: var(--ink-2); letter-spacing: 0.03em;
    margin: 22px 0 12px; padding-top: 18px; border-top: 1px dashed var(--grid);
  }
  .block-title:first-of-type { border-top: none; padding-top: 0; }

  .signal-list { margin: 0 0 0 36px; padding: 0; list-style: none; }
  .signal-list li {
    font-size: 12.5px; color: var(--ink-2); line-height: 1.7;
    padding-left: 14px; position: relative; margin-bottom: 3px;
  }
  .signal-list li::before { content: "·"; position: absolute; left: 2px; color: var(--ink-muted); font-weight: 700; }

  .chip-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px; margin-left: 36px; }
  .chip-item {
    border-radius: 12px; padding: 12px 6px; text-align: center;
    background: var(--surface-2); border: 1px solid var(--border);
  }
  .chip-item-name {
    display: flex; align-items: center; justify-content: center; gap: 5px;
    font-size: 11.5px; color: var(--ink-2); font-weight: 700; margin-bottom: 6px;
  }
  .chip-item-name svg { width: 13px; height: 13px; color: var(--ink-muted); }
  .chip-item-value {
    display: inline-flex; align-items: center; gap: 3px; justify-content: center;
    font-size: 12.5px; font-weight: 700; padding: 3px 9px; border-radius: 999px;
    border: 1px solid transparent;
  }
  .chip-streak {
    display: block; margin-top: 6px; font-size: 10.5px; font-weight: 700;
    color: var(--warn); background: var(--warn-tint); border-radius: 999px; padding: 3px 8px;
  }

  /* ---------- 波段停損試算（量縮回測進場點專屬，卡片內） ---------- */
  .stoploss-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px; margin-left: 36px; }
  .stoploss-item {
    border-radius: 12px; padding: 12px 8px; text-align: center;
    background: var(--surface-2); border: 1px solid var(--border);
  }
  .stoploss-label { font-size: 10.5px; color: var(--ink-muted); margin-bottom: 6px; }
  .stoploss-value { font-size: 14px; font-weight: 800; font-variant-numeric: tabular-nums; color: var(--ink-1); }
  /* 潛在停損幅度恆為負值（跌破防守價的假設性跌幅），沿用全報告「綠=跌」的漲跌極性配色，
     不另外用紅色標示風險——避免同一份報告出現「這裡紅色是漲、那裡紅色是警示」的不一致。 */
  .stoploss-value.risk { color: #3ddc74; }

  .ai-opinion {
    background: var(--surface-2); border: 1px solid var(--border); border-radius: 12px;
    padding: 13px 15px; margin: 0 0 8px 36px; font-size: 12.5px; line-height: 1.7; color: var(--ink-1);
  }
  .ai-opinion:last-child { margin-bottom: 0; }
  .ai-provider {
    display: flex; align-items: center; gap: 6px;
    font-weight: 700; color: var(--accent); font-size: 11.5px; margin-bottom: 5px; letter-spacing: 0.01em;
  }
  .ai-provider svg { width: 12px; height: 12px; color: var(--violet); }

  .footer { text-align: center; color: var(--ink-muted); font-size: 11.5px; margin-top: 28px; padding-top: 18px; border-top: 1px solid var(--grid); }
</style>
</head>
<body>
<div class="wrap">

  {% if market_regime and market_regime.status == "bearish" %}
  <div class="market-alert narrow">
    <span class="market-alert-icon">🚨</span>
    <div class="market-alert-body">
      <div class="market-alert-title">大盤跌破月線，系統進入防禦模式</div>
      <div class="market-alert-text">短線做多勝率大幅降低，建議空手或極小部位試單</div>
      {% if market_regime.close is not none and market_regime.ma20 is not none %}
      <div class="market-alert-meta">
        加權指數收盤 {{ "%.2f"|format(market_regime.close) }}，20日均線（月線）
        {{ "%.2f"|format(market_regime.ma20) }}
      </div>
      {% endif %}
    </div>
  </div>
  {% endif %}

  <div class="header narrow">
    <div class="brand-mark">""" + ICONS["ai"] + """</div>
    <div class="eyebrow">Daily AI Equity Brief</div>
    <h1>今日 AI 台股晨報</h1>
    <p>{{ report_date }}</p>
  </div>

  <div class="section toolbar narrow">
    <div class="toolbar-row">
      <select class="sort-select" id="stock-sort">
        <option value="default">預設排序（大盤優先＋漲跌幅）</option>
        <option value="change_desc">漲跌幅：高 → 低</option>
        <option value="change_asc">漲跌幅：低 → 高</option>
        <option value="score_desc">AI 評分：高 → 低</option>
        <option value="score_asc">AI 評分：低 → 高</option>
      </select>
    </div>
    <div class="toolbar-count" id="stock-count"></div>
  </div>

  {% if momentum_hits %}
  <div class="section momentum narrow">
    <div class="section-title-row">
      <span class="icon-badge badge-momentum">""" + ICONS["rocket"] + """</span>
      <span class="section-title">超級動能與飆股捕捉</span>
    </div>
    <div class="section-caption">均線多頭排列・波動收斂洗盤完畢・帶量創高突破・法人買超同步跟隨（VCP 動能點火篩選）</div>
    <div class="momentum-list">
      {% for h in momentum_hits %}
      <a class="momentum-row" href="#card-{{ h.stock_id }}">
        <div class="momentum-id">
          <span class="momentum-code">{{ h.stock_id }}</span>
          {% if h.name %}<span class="momentum-name">{{ h.name }}</span>{% endif %}
        </div>
        <div class="momentum-metrics">
          <div class="momentum-metric">
            <span class="momentum-metric-label">突破價位</span>
            <span class="momentum-metric-value">{{ "%.2f"|format(h.breakout_price) }}</span>
          </div>
          <div class="momentum-metric">
            <span class="momentum-metric-label">量能倍數</span>
            <span class="momentum-metric-value">{{ "%.1f"|format(h.volume_multiple) }}x</span>
          </div>
          <div class="momentum-metric">
            <span class="momentum-metric-label">法人買超佔比</span>
            <span class="momentum-metric-value highlight">{{ "%.1f"|format(h.institutional_buy_ratio) }}%</span>
          </div>
        </div>
      </a>
      {% endfor %}
    </div>
  </div>
  {% endif %}

  {% if pullback_hits %}
  <div class="section pullback narrow">
    <div class="section-title-row">
      <span class="icon-badge badge-pullback">""" + ICONS["pullback"] + """</span>
      <span class="section-title">N字型量縮回測進場點</span>
    </div>
    <div class="section-caption">近5日內曾帶量表態・今日量縮洗盤・收盤守住表態低點與10日均線（波段安全進場，適合持有1-2週）</div>
    <div class="pullback-list">
      {% for h in pullback_hits %}
      <a class="pullback-row" href="#card-{{ h.stock_id }}">
        <div class="pullback-id">
          <span class="pullback-code">{{ h.stock_id }}</span>
          {% if h.name %}<span class="pullback-name">{{ h.name }}</span>{% endif %}
        </div>
        <div class="pullback-metrics">
          <div class="pullback-metric">
            <span class="pullback-metric-label">進場參考價</span>
            <span class="pullback-metric-value">{{ "%.2f"|format(h.entry_price) }}</span>
          </div>
          <div class="pullback-metric">
            <span class="pullback-metric-label">表態日距今</span>
            <span class="pullback-metric-value">{{ h.trigger_date_offset }}日</span>
          </div>
          <div class="pullback-metric">
            <span class="pullback-metric-label">防守低點</span>
            <span class="pullback-metric-value">{{ "%.2f"|format(h.trigger_low) }}</span>
          </div>
          <div class="pullback-metric">
            <span class="pullback-metric-label">今日量縮比例</span>
            <span class="pullback-metric-value highlight">{{ "%.0f"|format(h.volume_shrink_ratio * 100) }}%</span>
          </div>
          <div class="pullback-metric">
            <span class="pullback-metric-label">潛在停損幅度</span>
            <span class="pullback-metric-value risk">{{ "%.1f"|format(h.stop_loss_pct) }}%</span>
          </div>
        </div>
      </a>
      {% endfor %}
    </div>
  </div>
  {% endif %}

  {% set leaders = stocks|rejectattr("is_index")|rejectattr("is_market_scan_hit")|list %}
  {% if leaders %}
  <div class="section narrow">
    <div class="section-title-row">
      <span class="icon-badge badge-violet">""" + ICONS["heat"] + """</span>
      <span class="section-title">資金族群熱力圖</span>
    </div>
    <div class="section-caption">依當日漲跌幅排序・紅漲綠跌・僅涵蓋本報告追蹤的各產業龍頭股（全市場動能掃描命中的個股不計入，避免熱力圖被沒有「產業龍頭」意義的個股稀釋）</div>
    <div class="heatmap-grid">
      {% for s in leaders %}
      <div class="heatmap-tile" style="background:{{ s.heat_bg }}; color:{{ s.heat_text }}; box-shadow:{{ s.heat_shadow }};">
        <span class="heatmap-sector">{{ s.sector or "—" }}</span>
        <span class="heatmap-code">{{ s.stock_id }}</span>
        <span class="heatmap-pct">{{ s.heat_arrow }} {{ "%+.2f"|format(s.change_pct) if s.change_pct is not none else "N/A" }}%</span>
      </div>
      {% endfor %}
    </div>
  </div>
  {% endif %}

  <div id="stock-list">
  {% for s in stocks %}
  <div class="section stock-card" id="card-{{ s.stock_id }}"
       data-code="{{ s.stock_id }}" data-name="{{ s.name or '' }}" data-sector="{{ s.sector or '' }}"
       data-change="{{ s.change_pct if s.change_pct is not none else '' }}" data-score="{{ s.score }}"
       data-order="{{ loop.index }}">

    <div class="card-head">
      <div>
        <div class="card-id-row">
          <span class="card-code">{{ s.stock_id }}</span>
          {% if s.name %}<span class="card-name">{{ s.name }}</span>{% endif %}
        </div>
        <div class="card-id-row" style="margin-top:9px;">
          {% if s.sector %}<span class="chip-tag sector-chip">{{ s.sector }}</span>{% endif %}
          {% if s.change_pct is not none %}
          <span class="chip-tag pct-chip" style="background:{{ s.heat_bg }}; color:{{ s.heat_text }};">
            {{ s.heat_arrow }} {{ "%+.2f"|format(s.change_pct) }}%
          </span>
          {% endif %}
          {% if s.is_momentum_hit %}
          <span class="momentum-card-badge">""" + ICONS["rocket"] + """ 動能突破</span>
          {% endif %}
          {% if s.is_pullback_hit %}
          <span class="pullback-card-badge">""" + ICONS["pullback"] + """ 量縮回測進場點</span>
          {% endif %}
        </div>
        {% if s.technical.sparkline_svg %}
        <div class="sparkline-wrap">
          <span class="sparkline-label">近{{ s.technical.price_history|length }}日走勢</span>
          {{ s.technical.sparkline_svg|safe }}
        </div>
        {% endif %}
      </div>
      <div class="score-tile">
        <div class="stat-label">AI 綜合評分</div>
        <svg width="76" height="76" viewBox="0 0 76 76">
          <circle cx="38" cy="38" r="{{ s.score_ring_radius }}" fill="none" stroke="rgba(255,255,255,0.08)" stroke-width="7"/>
          <circle cx="38" cy="38" r="{{ s.score_ring_radius }}" fill="none" stroke="{{ s.score_text }}" stroke-width="7"
            stroke-linecap="round"
            stroke-dasharray="{{ s.score_ring_dash }} {{ s.score_ring_circumference }}"
            transform="rotate(-90 38 38)"/>
          <text x="38" y="35" text-anchor="middle" font-family="'Inter','Noto Sans TC',system-ui,-apple-system,sans-serif"
            font-size="19" font-weight="800" fill="#f5f5f4">{{ s.score }}</text>
          <text x="38" y="48" text-anchor="middle" font-family="'Inter','Noto Sans TC',system-ui,-apple-system,sans-serif"
            font-size="9" font-weight="600" fill="#8b8a85">/100</text>
        </svg>
      </div>
    </div>

    <div class="status-row">
      <span class="status-tag" style="background:{{ s.technical.tag_bg }}; color:{{ s.technical.tag_text }}; border-color:{{ s.technical.tag_border }};">
        {{ s.technical.tag_arrow }} <span class="label" style="color:inherit; opacity:0.75;">技術面</span> {{ s.technical.trend }}
      </span>
      <span class="status-tag" style="background:{{ s.chip.tag_bg }}; color:{{ s.chip.tag_text }}; border-color:{{ s.chip.tag_border }};">
        {{ s.chip.tag_arrow }} <span class="label" style="color:inherit; opacity:0.75;">籌碼面</span> {{ s.chip.trend }}
      </span>
      <span class="status-tag" style="background:{{ s.ai.tag_bg }}; color:{{ s.ai.tag_text }}; border-color:{{ s.ai.tag_border }};">
        {{ s.ai.tag_arrow }} <span class="label" style="color:inherit; opacity:0.75;">AI趨勢</span> {{ s.ai.overall_sentiment }}
      </span>
    </div>

    <div class="metrics-row">
      <div class="metric-cell">
        <div class="metric-icon">""" + ICONS["tag"] + """</div>
        <div class="metric-label">收盤價</div>
        <div class="metric-value">{{ "%.2f"|format(s.technical.close) if s.technical.close else "N/A" }}</div>
      </div>
      <div class="metric-cell">
        <div class="metric-icon">""" + ICONS["volume"] + """</div>
        <div class="metric-label">成交量</div>
        <div class="metric-value">{{ "{:,.0f}".format(s.technical.volume) if s.technical.volume else "N/A" }}</div>
      </div>
      <div class="metric-cell">
        <div class="metric-icon">""" + ICONS["support"] + """</div>
        <div class="metric-label">關鍵支撐</div>
        <div class="metric-value">{{ "%.2f"|format(s.technical.support) if s.technical.support else "N/A" }}</div>
      </div>
      <div class="metric-cell">
        <div class="metric-icon">""" + ICONS["resistance"] + """</div>
        <div class="metric-label">關鍵壓力</div>
        <div class="metric-value">{{ "%.2f"|format(s.technical.resistance) if s.technical.resistance else "N/A" }}</div>
      </div>
    </div>

    <div class="block-title">
      <span class="icon-badge badge-accent">""" + ICONS["chart"] + """</span>技術面觀察
    </div>
    <ul class="signal-list">
      {% for sig in s.technical.signals %}<li>{{ sig }}</li>{% endfor %}
    </ul>

    {% if s.pullback %}
    <div class="block-title">
      <span class="icon-badge badge-pullback">""" + ICONS["pullback"] + """</span>波段停損試算（量縮回測進場點）
    </div>
    <div class="stoploss-grid">
      <div class="stoploss-item">
        <div class="stoploss-label">進場參考價</div>
        <div class="stoploss-value">{{ "%.2f"|format(s.pullback.entry_price) }}</div>
      </div>
      <div class="stoploss-item">
        <div class="stoploss-label">嚴格防守價</div>
        <div class="stoploss-value">{{ "%.2f"|format(s.pullback.trigger_low) }}</div>
      </div>
      <div class="stoploss-item">
        <div class="stoploss-label">潛在停損幅度</div>
        <div class="stoploss-value risk">{{ "%.1f"|format(s.pullback.stop_loss_pct) }}%</div>
      </div>
    </div>
    {% endif %}

    {% if s.chip.institutions %}
    <div class="block-title">
      <span class="icon-badge badge-amber">""" + ICONS["bank"] + """</span>籌碼面明細（三大法人）
    </div>
    <div class="chip-grid">
      {% for inst in s.chip.institutions %}
      <div class="chip-item">
        <span class="chip-item-name">{{ inst.name|inst_icon|safe }}{{ inst.name }}</span>
        <span class="chip-item-value" style="background:{{ inst.tag_bg }}; color:{{ inst.tag_text }}; border-color:{{ inst.tag_border }};">
          {{ inst.tag_arrow }} {{ inst.action }} {{ "{:,}".format(inst.net|abs) }}
        </span>
        {% if inst.streak_days > 1 %}
        <span class="chip-streak">{{ '連買' if inst.streak_type == 'buy' else '連賣' }} {{ inst.streak_days }} 天</span>
        {% endif %}
      </div>
      {% endfor %}
    </div>
    {% if s.chip.margin_available %}
    <ul class="signal-list" style="margin-top:10px;"><li>已取得融資融券資料（詳見原始數據）</li></ul>
    {% endif %}
    {% else %}
    <div class="block-title">
      <span class="icon-badge badge-amber">""" + ICONS["bank"] + """</span>籌碼面觀察
    </div>
    <ul class="signal-list">
      {% for sig in s.chip.signals %}<li>{{ sig }}</li>{% endfor %}
    </ul>
    {% endif %}

    <div class="block-title">
      <span class="icon-badge badge-violet">""" + ICONS["ai"] + """</span>AI 多模型分析
    </div>
    {% for op in s.ai.opinions %}
    <div class="ai-opinion">
      <span class="ai-provider">""" + ICONS["ai"] + """{{ op.provider }}</span>{{ op.text }}
    </div>
    {% endfor %}

  </div>
  {% endfor %}
  </div>

  {% if market_table %}
  <div class="section">
    <div class="section-title-row">
      <span class="icon-badge badge-accent">""" + ICONS["search"] + """</span>
      <span class="section-title">全市場報價總覽</span>
    </div>
    <div class="section-caption">
      上市股票共 {{ market_table|length }} 檔・未符合「超級動能與飆股捕捉」五條件、也未在上方深度分析中的個股僅顯示基礎行情（不含 AI 分析）・點欄位標題可排序
    </div>
    <div class="market-table-toolbar">
      <div class="search-box">
        """ + ICONS["search"] + """
        <input id="market-search" type="text" placeholder="搜尋代號／名稱／產業…" autocomplete="off">
      </div>
      <div class="market-table-count" id="market-count"></div>
    </div>
    <div class="market-table-scroll">
      <table class="market-table-el" id="market-table">
        <thead>
          <tr>
            <th data-key="stock_id">代號</th>
            <th data-key="name">名稱</th>
            <th data-key="sector">產業別</th>
            <th data-key="close" class="num">收盤價</th>
            <th data-key="change_pct" class="num">漲跌幅</th>
            <th data-key="volume" class="num">成交量(股)</th>
          </tr>
        </thead>
        <tbody id="market-table-body"></tbody>
      </table>
    </div>
    <div class="empty-state" id="market-empty-state" style="display:none;">沒有符合條件的股票，換個關鍵字試試。</div>
  </div>
  <script id="market-table-data" type="application/json">{{ market_table_json }}</script>
  {% endif %}

  <div class="footer narrow">
    由 AI 股票分析系統自動產生・僅供參考，不構成投資建議
  </div>

</div>

<script>
(function () {
  // 股票卡片工具列：只保留排序，搜尋框已移除（使用者反應用不到，全市場報價表
  // 那邊還有一份獨立的搜尋框，這裡拿掉的只是龍頭股卡片上方這一份）。
  var cards = Array.prototype.slice.call(document.querySelectorAll('.stock-card'));
  var container = document.getElementById('stock-list');
  var sortSelect = document.getElementById('stock-sort');
  var countEl = document.getElementById('stock-count');
  if (!cards.length || !container || !sortSelect) return;
  var total = cards.length;

  function apply() {
    var sortVal = sortSelect.value;
    var sorted = cards.slice().sort(function (a, b) {
      if (sortVal === 'change_desc') return (parseFloat(b.dataset.change) || -9999) - (parseFloat(a.dataset.change) || -9999);
      if (sortVal === 'change_asc') return (parseFloat(a.dataset.change) || 9999) - (parseFloat(b.dataset.change) || 9999);
      if (sortVal === 'score_desc') return (parseFloat(b.dataset.score) || 0) - (parseFloat(a.dataset.score) || 0);
      if (sortVal === 'score_asc') return (parseFloat(a.dataset.score) || 0) - (parseFloat(b.dataset.score) || 0);
      return (parseInt(a.dataset.order, 10) || 0) - (parseInt(b.dataset.order, 10) || 0);
    });
    sorted.forEach(function (c) { container.appendChild(c); });
    if (countEl) countEl.textContent = '共 ' + total + ' 檔';
  }

  sortSelect.addEventListener('change', apply);
  apply();
})();

(function () {
  // 全市場報價總覽：資料用 JSON 直接注入頁面（見上方 <script type="application/json">），
  // 純前端渲染成表格，並支援搜尋 + 點欄位標題排序，不需要任何外部套件（如 Grid.js／DataTables）。
  var dataEl = document.getElementById('market-table-data');
  var body = document.getElementById('market-table-body');
  var table = document.getElementById('market-table');
  var searchInput = document.getElementById('market-search');
  var countEl = document.getElementById('market-count');
  var emptyState = document.getElementById('market-empty-state');
  if (!dataEl || !body || !table) return;

  var rows = [];
  try { rows = JSON.parse(dataEl.textContent || '[]'); } catch (e) { rows = []; }
  var total = rows.length;
  var sortKey = 'change_pct';
  var sortDir = 'desc';

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }
  function fmtPct(v) {
    if (v === null || v === undefined) return 'N/A';
    return (v > 0 ? '+' : '') + v.toFixed(2) + '%';
  }
  function pctColor(v) {
    if (v === null || v === undefined || v === 0) return '#9a9994';
    return v > 0 ? '#ff6b60' : '#3ddc74';  // 紅漲綠跌（台股慣例），跟卡片/熱力圖同一套規則
  }
  function fmtVolume(v) {
    if (v === null || v === undefined) return 'N/A';
    return Number(v).toLocaleString('zh-Hant');
  }

  function render() {
    var q = (searchInput && searchInput.value || '').trim().toLowerCase();
    var filtered = q ? rows.filter(function (r) {
      var hay = ((r.stock_id || '') + ' ' + (r.name || '') + ' ' + (r.sector || '')).toLowerCase();
      return hay.indexOf(q) !== -1;
    }) : rows.slice();

    filtered.sort(function (a, b) {
      var av = a[sortKey], bv = b[sortKey];
      var isStr = (sortKey === 'stock_id' || sortKey === 'name' || sortKey === 'sector');
      if (isStr) {
        av = (av || '').toLowerCase(); bv = (bv || '').toLowerCase();
      } else {
        av = (av === null || av === undefined) ? -Infinity : av;
        bv = (bv === null || bv === undefined) ? -Infinity : bv;
      }
      if (av < bv) return sortDir === 'asc' ? -1 : 1;
      if (av > bv) return sortDir === 'asc' ? 1 : -1;
      return 0;
    });

    var html = filtered.map(function (r) {
      var pct = r.change_pct;
      return '<tr>' +
        '<td class="mkt-code">' + esc(r.stock_id) + '</td>' +
        '<td>' + esc(r.name) + '</td>' +
        '<td>' + esc(r.sector || '—') + '</td>' +
        '<td class="num">' + (r.close != null ? Number(r.close).toFixed(2) : 'N/A') + '</td>' +
        '<td class="num" style="color:' + pctColor(pct) + ';font-weight:700;">' + fmtPct(pct) + '</td>' +
        '<td class="num">' + fmtVolume(r.volume) + '</td>' +
        '</tr>';
    }).join('');
    body.innerHTML = html;

    if (countEl) {
      countEl.textContent = q
        ? ('符合「' + (searchInput.value || '').trim() + '」：' + filtered.length + ' / ' + total + ' 檔')
        : ('共 ' + total + ' 檔');
    }
    if (emptyState) emptyState.style.display = filtered.length ? 'none' : 'block';
  }

  Array.prototype.slice.call(table.querySelectorAll('th[data-key]')).forEach(function (th) {
    th.addEventListener('click', function () {
      var key = th.dataset.key;
      if (sortKey === key) {
        sortDir = sortDir === 'asc' ? 'desc' : 'asc';
      } else {
        sortKey = key;
        sortDir = (key === 'stock_id' || key === 'name' || key === 'sector') ? 'asc' : 'desc';
      }
      Array.prototype.slice.call(table.querySelectorAll('th[data-key]')).forEach(function (h) {
        h.classList.remove('sorted-asc', 'sorted-desc');
      });
      th.classList.add(sortDir === 'asc' ? 'sorted-asc' : 'sorted-desc');
      render();
    });
  });

  if (searchInput) searchInput.addEventListener('input', render);
  render();
})();
</script>
</body>
</html>
"""


def render_report(
    stocks: list,
    momentum_hits: list = None,
    pullback_hits: list = None,
    market_table: list = None,
    market_regime: dict = None,
) -> str:
    """
    Args:
        stocks: list of dict，每個 dict 至少含
                stock_id, score, technical(dict), chip(dict), ai(dict)
                建議也帶入 sector（產業龍頭標籤）與 is_index（是否為大盤等指數）
                順序即為報告呈現順序，請在呼叫前排序好（例如大盤在前、其餘依漲跌幅排序）
                若某檔股票有 momentum(dict)，卡頭會多一個「動能突破」徽章；
                若有 pullback(dict)，卡頭會多一個「量縮回測進場點」徽章，卡片內文也會
                多一個「波段停損試算」區塊（進場參考價／嚴格防守價／潛在停損幅度，
                取 pullback.entry_price／trigger_low／stop_loss_pct）。兩個徽章互相
                獨立，可以同時出現。
                若是全市場掃描命中補進來的個股，記得帶 is_market_scan_hit=True，
                報告會把它排除在「資金族群熱力圖」之外（那個區塊只給既有龍頭股清單用）。
        momentum_hits: analysis/momentum_screener.py「超級動能與飆股捕捉」篩選命中的
                清單（見 main.py run()），每筆需含 stock_id、breakout_price、
                volume_multiple、institutional_buy_ratio。有值時會在報告最上方
                （熱力圖之前）多一個「超級動能與飆股捕捉」高亮區塊，每一列可以點擊
                跳到下面對應的個股卡片（用 #card-{stock_id} 錨點）。
        pullback_hits: analysis/momentum_screener.py「N字型量縮回測」篩選命中的清單，
                每筆需含 stock_id、entry_price、trigger_date_offset、trigger_low、
                volume_shrink_ratio、stop_loss_pct。有值時會在動能突破區塊之後多一個
                「N字型量縮回測進場點」高亮區塊，同樣可以點擊跳到對應的個股卡片。
        market_table: 全市場動能掃描沒有命中、也還沒有深度分析的個股基礎行情清單
                （見 main.py 的 _run_full_market_momentum_scan()），每筆
                {"stock_id","name","sector","close","change","change_pct","volume"}。
                有值時會在股票卡片清單下方多一個「全市場報價總覽」表格區塊，資料以
                JSON 直接注入頁面、純前端渲染＋搜尋／排序，不需要任何外部套件。
        market_regime: main.py 的 _check_market_regime() 回傳的大盤環境判斷，
                {"status": "bullish"/"bearish"/"unknown", "close": float, "ma20": float}。
                status 為 "bearish"（加權指數跌破20日均線）時，報告最頂端（比標題還前面）
                會強制顯示醒目紅色警告；"bullish"／"unknown" 都不顯示，不影響選股或
                報告其餘內容。
    Returns:
        完整 HTML 字串
    """
    env = Environment(loader=BaseLoader())
    env.filters["inst_icon"] = _inst_icon
    template = env.from_string(TEMPLATE)
    report_date = datetime.now().strftime("%Y-%m-%d (%A)")
    prepared_stocks = _prepare_stocks(stocks)
    market_table = market_table or []
    # 用 </ -> <\/ 避免表格資料裡萬一出現 "</script>" 字樣時提早關閉 script 標籤；
    # ensure_ascii=False 讓中文名稱/產業別直接以可讀的 UTF-8 輸出，不會膨脹成 \uXXXX。
    market_table_json = json.dumps(market_table, ensure_ascii=False).replace("</", "<\\/")
    return template.render(
        stocks=prepared_stocks,
        report_date=report_date,
        momentum_hits=momentum_hits or [],
        pullback_hits=pullback_hits or [],
        market_regime=market_regime,
        market_table=market_table,
        market_table_json=market_table_json,
    )


def save_report(html: str, filename: str = None) -> str:
    """把 HTML 存到 output/ 目錄（帶日期檔名，當作歷史存檔），回傳檔案路徑"""
    filename = filename or f"report_{datetime.now().strftime('%Y%m%d')}.html"
    path = os.path.join(config.OUTPUT_DIR, filename)
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    return path


def save_site_index(html: str) -> str:
    """
    把同一份 HTML 另存成固定檔名 docs/index.html。
    這個檔案是「網站」的首頁本體：GitHub Pages 設定成從 main 分支的 /docs 目錄發布後，
    每次 GitHub Actions 跑完每日排程、把這個檔案 commit + push 回 repo，
    https://<你的帳號>.github.io/<repo名稱>/ 這個網址顯示的內容就會自動更新成最新一天的報告，
    不需要再寄信。
    """
    path = os.path.join(config.SITE_DIR, "index.html")
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    return path
