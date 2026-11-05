"""找出寄信失敗在哪一步（不會顯示帳號密碼）。會寄一封測試信到你的收信信箱。"""
import os
import smtplib
import socket
import sys
from email.mime.text import MIMEText

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sinopac import keychain  # noqa: E402
from sync import HELO, _ssl_ctx  # noqa: E402

user, pw, to = keychain("smtp_user", "stock-mail"), keychain("smtp_pass", "stock-mail"), keychain("email_to", "stock-mail")
pw = pw.replace(" ", "")
print(f"寄件帳號：{user[:2]}…@{user.split('@')[-1] if '@' in user else '（不是 email 格式！）'}；"
      f"應用程式密碼長度：{len(pw)}（應該是 16）；收件：…@{to.split('@')[-1] if '@' in to else '（不是 email 格式！）'}")
for port in (465, 587):
    step = "連線"
    try:
        if port == 465:
            s = smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30, context=_ssl_ctx(), local_hostname=HELO)
        else:
            s = smtplib.SMTP("smtp.gmail.com", 587, timeout=30, local_hostname=HELO)
        step = "打招呼（EHLO）"
        code, _ = s.ehlo()
        if port == 587:
            step = "加密（STARTTLS）"
            s.starttls(context=_ssl_ctx())
            s.ehlo()
        step = "登入"
        s.login(user, pw)
        step = "寄信"
        m = MIMEText("這是測試信，收到代表 Mac 可以寄對帳信了。", "plain", "utf-8")
        m["Subject"], m["From"], m["To"] = "測試：Mac 寄信", user, to
        s.sendmail(user, [to], m.as_string())
        s.quit()
        print(f"✔ {port}：寄出測試信了")
        break
    except Exception as e:  # noqa
        print(f"✘ {port}：在「{step}」這一步失敗：{e.__class__.__name__} {str(e)[:150]}")
