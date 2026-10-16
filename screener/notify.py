"""通知：LINE Messaging API（push）與 Email（SMTP）。未設定金鑰的管道會自動略過。"""
from __future__ import annotations

import os
import smtplib
from email.mime.text import MIMEText

import requests

from .common import log


def send_line(text: str) -> bool:
    token = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN")
    to = os.environ.get("LINE_USER_ID")
    if not token or not to:
        log.info("LINE not configured; skip")
        return False
    chunks = [text[i:i + 4800] for i in range(0, len(text), 4800)][:5]
    r = requests.post("https://api.line.me/v2/bot/message/push",
                      headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                      json={"to": to, "messages": [{"type": "text", "text": c} for c in chunks]},
                      timeout=30)
    if r.status_code != 200:
        log.warning("LINE push failed %s %s", r.status_code, r.text[:300])
        return False
    return True


def send_email(subject: str, text: str, html: str | None = None) -> bool:
    host = os.environ.get("SMTP_HOST", "smtp.gmail.com")
    port = int(os.environ.get("SMTP_PORT", "465"))
    user = os.environ.get("SMTP_USER")
    pw = os.environ.get("SMTP_PASS")
    to = os.environ.get("EMAIL_TO") or user
    if not user or not pw:
        log.info("SMTP not configured; skip")
        return False
    msg = MIMEText(html or text, "html" if html else "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = user
    msg["To"] = to
    try:
        if port == 465:
            with smtplib.SMTP_SSL(host, port, timeout=30) as s:
                s.login(user, pw)
                s.sendmail(user, [x.strip() for x in to.split(",")], msg.as_string())
        else:
            with smtplib.SMTP(host, port, timeout=30) as s:
                s.starttls()
                s.login(user, pw)
                s.sendmail(user, [x.strip() for x in to.split(",")], msg.as_string())
        return True
    except Exception as e:  # noqa
        log.warning("email failed: %s", e)
        return False


def notify(subject: str, text: str) -> dict:
    return {"line": send_line(f"{subject}\n\n{text}"), "email": send_email(subject, text)}
