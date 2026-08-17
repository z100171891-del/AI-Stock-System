"""
config.py
----------
系統全域設定。統一從 .env 讀取環境變數，
讓 API 金鑰、Email 帳密等機密資訊不會寫死在程式碼中。
"""
import os
from dotenv import load_dotenv

# 讀取專案根目錄下的 .env（若不存在則所有變數皆為 None，系統仍可運作於降級模式）
load_dotenv()


def _get_list(key: str, default: str = ""):
    raw = os.getenv(key, default)
    return [item.strip() for item in raw.split(",") if item.strip()]


# ---------------- 股票清單 ----------------
# 台股代號（不含 .TW，資料層會自動補上）；TAIEX 代表大盤加權指數，一律排在報告最前面
STOCK_LIST = _get_list(
    "STOCK_LIST",
    "TAIEX,2330,2454,2327,2408,2317,2308,3008,2382,2412,2881,2603,1216,2002,1301",
)

# 每檔個股對應的「產業龍頭」標籤，會顯示在報告卡片與資金族群熱力圖上。
# 沒有列在這裡的股票代號（例如使用者自行新增的）會顯示為空白，不影響其餘功能。
STOCK_SECTOR_MAP = {
    "2330": "半導體代工龍頭",
    "2454": "IC設計龍頭",
    "2327": "被動元件龍頭",
    "2408": "記憶體(DRAM)龍頭",
    "2317": "電子代工(EMS)龍頭",
    "2308": "電源管理龍頭",
    "3008": "光學鏡頭龍頭",
    "2382": "伺服器/筆電代工龍頭",
    "2412": "電信龍頭",
    "2881": "金融控股龍頭",
    "2603": "航運龍頭",
    "1216": "食品零售龍頭",
    "2002": "鋼鐵龍頭",
    "1301": "石化龍頭",
    "0050": "台灣50 ETF",
}

# ---------------- 資料來源 ----------------
FINMIND_TOKEN = os.getenv("FINMIND_TOKEN", "").strip()
YFINANCE_SUFFIX = ".TW"          # 台股上市；上櫃股票可改 .TWO
PRICE_HISTORY_DAYS = 120         # 抓取近 N 日資料，供 MA60 等指標計算

# ---------------- AI 多模型分析 ----------------
AI_PROVIDERS = {
    "gemini": os.getenv("GEMINI_API_KEY", "").strip(),
    "openai": os.getenv("OPENAI_API_KEY", "").strip(),
    "deepseek": os.getenv("DEEPSEEK_API_KEY", "").strip(),
    "groq": os.getenv("GROQ_API_KEY", "").strip(),
}
# 只保留有填金鑰的模型
ACTIVE_AI_PROVIDERS = [name for name, key in AI_PROVIDERS.items() if key]

# Gemini「核心發動題材」段落需要模型能查到近期真實消息（不能只靠訓練資料，否則
# 容易編造不存在的新聞），因此預設會在 Gemini 請求裡加上 Google Search grounding
# 工具，讓模型在回答前先搜尋。免費方案有搜尋額度限制，超過額度該次呼叫會失敗
# （會自動退回規則式摘要，不影響其他模型／其他股票），若想先關掉這個功能改用
# 純語言模型回答（不會搜尋，但也不會有額外的搜尋額度顧慮），在 .env 設定
# GEMINI_ENABLE_GROUNDING=false 即可，不用改程式碼。
GEMINI_ENABLE_GROUNDING = os.getenv("GEMINI_ENABLE_GROUNDING", "true").strip().lower() == "true"

# ---------------- Email 通知 ----------------
# 預設關閉：改用「網站」方式看報告（見下方 SITE_DIR），不用寄信也能每天看最新報告。
# 若還是想同時收信，在 .env 加一行 ENABLE_EMAIL=true 即可重新開啟。
ENABLE_EMAIL = os.getenv("ENABLE_EMAIL", "false").strip().lower() == "true"

# ---------------- 全市場動能掃描 ----------------
# 預設開啟：對全部上市股票（約1000檔）跑「超級動能與飆股捕捉」Phase A 篩選。
# 若正式跑起來發現太耗時（例如 GitHub Actions 逾時），可在 .env 設
# ENABLE_FULL_MARKET_SCAN=false 先關掉，只跑 STOCK_LIST 既有的龍頭股清單，
# 不用改程式碼。
ENABLE_FULL_MARKET_SCAN = os.getenv("ENABLE_FULL_MARKET_SCAN", "true").strip().lower() == "true"
EMAIL_SENDER = os.getenv("EMAIL_SENDER", "").strip()
EMAIL_APP_PASSWORD = os.getenv("EMAIL_APP_PASSWORD", "").strip()
EMAIL_RECEIVER = _get_list("EMAIL_RECEIVER", "")
SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587

# ---------------- 其他 ----------------
TIMEZONE = os.getenv("TIMEZONE", "Asia/Taipei")

# ---------------- 輸出路徑 ----------------
OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output")
os.makedirs(OUTPUT_DIR, exist_ok=True)

# docs/ 是「網站」的發布目錄：GitHub Pages 設定成從 main 分支的 /docs 目錄發布，
# 每次產生報告時都會覆蓋 docs/index.html，變成固定網址、永遠是最新一天的報告。
SITE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "docs")
os.makedirs(SITE_DIR, exist_ok=True)
