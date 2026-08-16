"""
report/email_report.py
------------------------
用 Gmail SMTP 寄送每日 HTML 報告。

若 .env 尚未設定 EMAIL_SENDER / EMAIL_APP_PASSWORD / EMAIL_RECEIVER，
send_report() 會直接記錄警告並回傳 False，不會拋出例外中斷流程
（方便在「先做好功能，之後自己填 Gmail 帳密」的階段先跑通其餘功能）。

Gmail 應用程式密碼申請方式：
  Google 帳戶 -> 安全性 -> 兩步驟驗證（需先開啟）-> 應用程式密碼 -> 產生 16 碼密碼
"""
import logging
import smtplib
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import config

logger = logging.getLogger(__name__)


def send_report(html: str, subject: str = None) -> bool:
    if not (config.EMAIL_SENDER and config.EMAIL_APP_PASSWORD and config.EMAIL_RECEIVER):
        logger.warning(
            "email_report: 尚未設定 EMAIL_SENDER / EMAIL_APP_PASSWORD / EMAIL_RECEIVER，"
            "略過寄送，報告已另存於 output/ 目錄"
        )
        return False

    subject = subject or f"📈 今日 AI 台股晨報 {datetime.now().strftime('%Y-%m-%d')}"

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = config.EMAIL_SENDER
    msg["To"] = ", ".join(config.EMAIL_RECEIVER)
    msg.attach(MIMEText(html, "html", "utf-8"))

    try:
        with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=30) as server:
            server.starttls()
            server.login(config.EMAIL_SENDER, config.EMAIL_APP_PASSWORD)
            server.sendmail(config.EMAIL_SENDER, config.EMAIL_RECEIVER, msg.as_string())
        logger.info("email_report: 已寄送報告至 %s", config.EMAIL_RECEIVER)
        return True
    except Exception as exc:  # noqa: BLE001
        logger.error("email_report: 寄送失敗: %s", exc)
        return False
