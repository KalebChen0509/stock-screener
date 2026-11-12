"""通知：LINE Messaging API（push）與 Email（SMTP）。未設定金鑰的管道會自動略過。"""
from __future__ import annotations

import os
import smtplib
import unicodedata
from email.mime.text import MIMEText

import requests

from .common import log


def send_line(text: str) -> bool:
    # 跟 Email 一樣清掉複製時夾帶的空白、換行、看不見的字元（2026/11/07 前沒清，貼上時多一個換行就會 401）
    token = _clean(os.environ.get("LINE_CHANNEL_ACCESS_TOKEN"), drop_spaces=True)
    to = _clean(os.environ.get("LINE_USER_ID"), drop_spaces=True)
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
        # 只印長度與格式，不印內容
        if r.status_code == 401:
            log.warning("LINE 權杖長度 %d 字元%s。要用 Messaging API 分頁最下面「Channel access token (long-lived)」（約 170 多字元）；"
                        "32 字元的是 Basic settings 的 Channel secret，貼錯了。", len(token), "（含非英數字元）" if _non_ascii(token) else "")
        if r.status_code == 400:
            log.warning("LINE 使用者 ID 長度 %d、%s U 開頭（應該是 Basic settings 的 Your user ID：U 開頭、33 字元）",
                        len(to), "是" if to.startswith("U") else "不是")
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


def line_wanted(scope: str) -> bool:
    """LINE 免費方案每月有則數上限，預設只發「市場重大事件」（events）。
    要讓日報（daily）、盤中提醒（alerts）、財報快報（earnings）也發 LINE：GitHub → Settings → Secrets and variables → Actions →
    Variables 新增 LINE_FOR，例如 events,daily。"""
    allowed = {x.strip() for x in (os.environ.get("LINE_FOR") or "events").split(",") if x.strip()}
    return "all" in allowed or scope in allowed


def notify(subject: str, text: str, scope: str = "daily", line_text: str | None = None) -> dict:
    """Email 一律寄；LINE 只在 line_wanted(scope) 時發。scope：daily（日報）、alerts、earnings、events、test。
    line_text：LINE 用的短版（沒給就跟 Email 一樣）。"""
    line = send_line(line_text if line_text is not None else f"{subject}\n\n{text}") if line_wanted(scope) else False
    return {"line": line, "email": send_email(subject, text)}
