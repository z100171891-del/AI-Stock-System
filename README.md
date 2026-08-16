# AI-Stock-System｜AI 股票分析系統

從 0 到每天自動分析、AI 幫你工作、每日 Email 通知的台股分析系統。
不用 Colab，直接在本機（或 GitHub Actions）執行。

## 專案結構

```
AI-Stock-System/
├── main.py                    # 主流程進入點
├── config.py                  # 讀取 .env 的全域設定
├── data/
│   └── stock_data.py          # yfinance 抓價量 / FinMind 抓籌碼（選填）
├── analysis/
│   ├── indicators.py          # MA5/MA20/MA60、RSI、MACD、成交量比
│   ├── technical.py           # 技術面/籌碼面 偏多偏空判讀
│   └── scoring.py             # AI 綜合評分 0~100
├── ai/
│   ├── base.py                # 共用 prompt 樣板與情緒判斷
│   ├── gemini_analysis.py     # Google Gemini
│   ├── openai_analysis.py     # OpenAI GPT
│   ├── deepseek_analysis.py   # DeepSeek
│   ├── groq_analysis.py       # Groq
│   └── multi_model.py         # 多模型彙整入口（無金鑰時自動用規則式摘要）
├── report/
│   ├── html_report.py         # 產生 HTML 晨報
│   └── email_report.py        # 用 Gmail 寄送（無帳密時僅存檔）
├── tests/
│   └── test_analysis.py       # 單元測試（pytest）
├── .github/workflows/
│   └── daily_report.yml       # 每天 08:00 (Asia/Taipei) 自動執行
├── requirements.txt
├── .env.example
└── .gitignore
```

## 目前狀態

系統架構已完整可運作，採「可插拔」設計，即使還沒申請任何金鑰也能跑完整條流程
（技術面/籌碼面分析 → 規則式摘要取代 AI 分析 → 產生 HTML 報告 → 存檔）：

- ✅ 股票資料（yfinance，免金鑰）
- ⬜ 籌碼資料（FinMind，需 Token，未設定時會標示「資料未提供」但不影響其他功能）
- ⬜ AI 多模型分析（Gemini/OpenAI/DeepSeek/Groq，未設定金鑰時自動改用規則式摘要）
- ⬜ Email 通知（需 Gmail 帳號 + 應用程式密碼，未設定時報告只會存在 `output/` 目錄）
- ⬜ GitHub Actions 每日自動排程（workflow 檔案已寫好，需你自己 push 到 GitHub 並設定 Secrets）

## 本機安裝與執行

```bash
cd AI-Stock-System
python -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env
# 用文字編輯器打開 .env，至少確認 STOCK_LIST（預設 2330,2317,0050）

python main.py
```

執行後會在 `output/` 目錄產生 `report_YYYYMMDD.html`，可直接用瀏覽器打開查看。

```bash
# 執行單元測試
pytest tests/ -v
```

## 逐步啟用各項功能

### 1. 籌碼面分析（FinMind，選填）

1. 到 https://finmindtrade.com/ 免費註冊帳號
2. 登入後在會員中心取得 API Token
3. 貼到 `.env` 的 `FINMIND_TOKEN=`

### 2. AI 多模型分析（至少擇一即可運作）

你目前已選擇之後會申請 **Gemini** 金鑰，步驟如下：

1. 到 https://aistudio.google.com/app/apikey
2. 登入 Google 帳號 → 建立 API 金鑰
3. 貼到 `.env` 的 `GEMINI_API_KEY=`

其他模型（可依需求擴充，架構已支援）：

| 模型 | 申請網址 | .env 欄位 |
|---|---|---|
| OpenAI | https://platform.openai.com/api-keys | `OPENAI_API_KEY` |
| DeepSeek | https://platform.deepseek.com/api_keys | `DEEPSEEK_API_KEY` |
| Groq | https://console.groq.com/keys | `GROQ_API_KEY` |

同時填多個金鑰時，`ai/multi_model.py` 會呼叫所有已設定的模型，並在報告中並列各家觀點、
以多數決得出「AI 綜合情緒」，供評分模組使用。

### 3. Email 每日通知（Gmail）

1. 前往 Google 帳戶 → 安全性 → 開啟「兩步驟驗證」
2. 開啟後前往「應用程式密碼」頁面（https://myaccount.google.com/apppasswords）
3. 建立一組應用程式密碼（16 碼，無空格），**不是**你平常登入用的密碼
4. 填入 `.env`：
   ```
   EMAIL_SENDER=你的Gmail帳號@gmail.com
   EMAIL_APP_PASSWORD=產生的16碼應用程式密碼
   EMAIL_RECEIVER=收件信箱@gmail.com
   ```
5. 重新執行 `python main.py`，即可收到「今日 AI 台股晨報」

### 4. 推送到 GitHub

```bash
git init
git add .
git commit -m "Init AI Stock System"
git branch -M main
git remote add origin <你的repo網址>
git push -u origin main
```

`.gitignore` 已排除 `.env`，你的金鑰與密碼不會被上傳。

### 5. 啟用 GitHub Actions 每日自動排程

1. 到你的 GitHub Repo → Settings → Secrets and variables → Actions
2. 新增以下 Repository secrets（哪些有填就新增哪些，未填的模型/功能會自動略過）：
   - `STOCK_LIST`（例如 `2330,2317,0050`）
   - `FINMIND_TOKEN`
   - `GEMINI_API_KEY` / `OPENAI_API_KEY` / `DEEPSEEK_API_KEY` / `GROQ_API_KEY`
   - `EMAIL_SENDER` / `EMAIL_APP_PASSWORD` / `EMAIL_RECEIVER`
3. 到 Actions 頁籤，確認 `Daily AI Stock Report` workflow 已出現
4. 可先點選 `Run workflow` 手動觸發一次測試
5. 之後每天 UTC 00:00（= 台北時間 08:00）會自動執行，執行結果與錯誤都可在 Actions log 查看
6. 若執行失敗，`output/` 目錄會被上傳成 artifact 方便除錯

## 評分邏輯說明

AI 綜合評分（0~100）由三部分加權組成：

- 技術面 40%（MA5/MA20/MA60 排列、RSI、MACD、成交量）
- 籌碼面 30%（三大法人買賣超；無 FinMind Token 時視為中性 50 分）
- AI 情緒面 30%（多模型分析多數決；無金鑰時用規則式摘要，視為中性起點再依技術/籌碼推導）

可在 `analysis/scoring.py` 的 `WEIGHTS` 調整權重。

## 免責聲明

本系統產生之所有分析、評分與 AI 觀點僅供技術學習與參考，不構成任何投資建議，
使用者應自行判斷並承擔投資風險。
