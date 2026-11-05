"""重新設定對帳信的寄件帳號（存進 Mac 的鑰匙圈），設定完馬上寄一封測試信。

信箱地址會顯示在畫面上方便你檢查；應用程式密碼輸入時不會顯示。
執行：~/.sinopac-venv/bin/python ~/PycharmProjects/stock-screener/trader/setup_mail.py
"""
import getpass
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def ask_email(prompt):
    while True:
        v = input(prompt).strip()
        if EMAIL.match(v):
            return v
        print("  這不像 email 地址（要有 @，例如 abc@gmail.com），請再輸入一次。")


def store(account, value):
    """用 security 的互動模式寫入（值不會出現在指令列，別的程式看不到）。"""
    cmd = f'add-generic-password -U -s stock-mail -a {account} -w "{value}"\n'
    r = subprocess.run(["security", "-i"], input=cmd, text=True, capture_output=True)
    if r.returncode != 0:
        sys.exit(f"存入鑰匙圈失敗：{r.stderr.strip()[:200]}")


def main():
    print("對帳信會從「寄件 Gmail」寄到「收件信箱」。\n")
    while True:
        user = ask_email("寄件 Gmail（例如你寄每日選股日報的那個副帳號）：").lower()
        if not user.endswith(("@gmail.com", "@googlemail.com")):
            print(f"  寄件信箱要是 @gmail.com 結尾（你輸入的是 @{user.split('@')[-1]}），請檢查有沒有打錯。")
            continue
        to = ask_email("收件信箱（你的主信箱）：")
        if input(f"\n請確認：從 {user} 寄到 {to}，對嗎？（y/n）").strip().lower() == "y":
            break
        print()
    while True:
        pw = getpass.getpass("寄件 Gmail 的應用程式密碼（16 個英文字母，可含空格；畫面不會顯示）：")
        pw = pw.replace(" ", "").strip()
        if re.fullmatch(r"[A-Za-z]{16}", pw):
            break
        print(f"  長度是 {len(pw)}，應用程式密碼應該是 16 個英文字母（不是 Gmail 登入密碼）。")
        print("  沒有的話：用寄件 Gmail 登入 → Google 帳戶 → 搜尋「應用程式密碼」→ 建立一組，複製那 16 個字母。")
    for a, v in (("smtp_user", user), ("email_to", to), ("smtp_pass", pw)):
        store(a, v)
    print("✔ 已存進鑰匙圈。寄測試信中…")
    from sync import send_mail
    send_mail("測試：Mac 寄信", "這是測試信，收到代表 Mac 可以寄對帳信了。")
    print(f"✔ 已寄出測試信到 {to}，去收信看看（沒看到的話看一下垃圾郵件）。")


if __name__ == "__main__":
    main()
