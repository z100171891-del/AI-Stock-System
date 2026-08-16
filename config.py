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
# 台股代號（不含 .TW，資料層會自動補上）
STOCK_LIST = _get_list("STOCK_LIST", "2330,2317,0050")

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

# ---------------- Email 通知 ----------------
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
