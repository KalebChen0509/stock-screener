"""通知：LINE Messaging API（push）與 Email（SMTP）。未設定金鑰的管道會自動略過。"""
from __future__ import annotations

import os
import smtplib
import unicodedata
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


def _clean(v: str | None, drop_spaces: bool = False) -> str:
    """GitHub Secrets 常見輸入問題：中文輸入法打出全形英數字（ｇｍａｉｌ）、前後空白、
    應用程式密碼中間的空格，以及複製時夾帶的看不見字元（零寬空白、方向標記）。"""
    v = unicodedata.normalize("NFKC", v or "")
    v = "".join(c for c in v if unicodedata.category(c) not in ("Cf", "Cc"))
    v = v.strip()
    return "".join(c for c in v if not c.isspace()) if drop_spaces else v


def _non_ascii(v: str) -> bool:
    return any(ord(c) > 127 for c in v)


def _why(v: str) -> str:
    """說明不是半形英數字的是哪一類字（不洩漏內容）。"""
    bad = [c for c in v if ord(c) > 127]
    if any(0x3100 <= ord(c) <= 0x312F or 0x31A0 <= ord(c) <= 0x31BF for c in bad):
        return "看起來是在注音輸入法下打的（出現注音符號）"
    if any(unicodedata.category(c) == "Lo" for c in bad):
        return "含有中文字"
    return f"含有 {len(bad)} 個特殊字元"


def configured() -> bool:
    """至少設定了一種通知管道（沒設定時不必保留未寄出的通知）。"""
    return bool((os.environ.get("LINE_CHANNEL_ACCESS_TOKEN") and os.environ.get("LINE_USER_ID"))
                or (os.environ.get("SMTP_USER") and os.environ.get("SMTP_PASS")))


def send_email(subject: str, text: str, html: str | None = None) -> bool:
    host = _clean(os.environ.get("SMTP_HOST")) or "smtp.gmail.com"
    port = int(_clean(os.environ.get("SMTP_PORT")) or 465)
    user = _clean(os.environ.get("SMTP_USER"))
    pw = _clean(os.environ.get("SMTP_PASS"), drop_spaces=True)
    to = _clean(os.environ.get("EMAIL_TO")) or user
    if not user or not pw:
        log.info("SMTP not configured; skip")
        return False
    for name, val in (("SMTP_USER", user), ("SMTP_PASS", pw), ("EMAIL_TO", to)):
        if _non_ascii(val):
            log.warning("email failed: GitHub Secret %s %s，不是半形英數字。請把輸入法切成英文（ABC）後重新填寫", name, _why(val))
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
