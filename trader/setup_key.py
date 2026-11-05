"""把永豐 API Key 存進 Mac 的鑰匙圈（輸入時畫面不會顯示）。

用法：
  ~/.sinopac-venv/bin/python ~/PycharmProjects/stock-screener/trader/setup_key.py sinopac-sim    # 模擬下單用的 Key
  ~/.sinopac-venv/bin/python ~/PycharmProjects/stock-screener/trader/setup_key.py sinopac-sync   # 每天同步用的唯讀 Key
存好後會用這把 Key 登入一次確認可以用（模擬 Key 登入模擬環境、同步 Key 登入正式環境）。
"""
import getpass
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

SERVICES = {"sinopac-sim": "模擬下單（權限：行情／資料、交易；不勾正式環境）",
            "sinopac-sync": "每天同步（權限：行情／資料、帳務、正式環境；不勾交易）"}


def ask(label):
    while True:
        v = getpass.getpass(f"貼上 {label}（畫面不會顯示，貼完按 Enter）：").strip()
        if re.fullmatch(r"[A-Za-z0-9+/=_\-]{20,}", v):
            return v
        print(f"  長度 {len(v)}、或含有空格／中文等不該出現的字元，請重新複製貼上（只貼 Key 本身）。")


def store(service, account, value):
    r = subprocess.run(["security", "-i"], input=f'add-generic-password -U -s {service} -a {account} -w "{value}"\n',
                       text=True, capture_output=True)
    if r.returncode != 0:
        sys.exit(f"存入鑰匙圈失敗：{r.stderr.strip()[:200]}")


def main():
    service = sys.argv[1] if len(sys.argv) > 1 else ""
    if service not in SERVICES:
        sys.exit("請指定要存哪一把：sinopac-sim（模擬下單）或 sinopac-sync（每天同步）")
    print(f"存入「{service}」：{SERVICES[service]}\n")
    key = ask("API Key")
    secret = ask("Secret Key")
    if key == secret:
        sys.exit("API Key 和 Secret Key 一樣，應該是貼錯了，請重新執行。")
    store(service, "api_key", key)
    store(service, "secret_key", secret)
    del key, secret
    print("✔ 已存進鑰匙圈，用這把 Key 登入測試中…")
    from sinopac import login, logout, say, short
    api, accounts = login(simulation=(service == "sinopac-sim"), service=service)
    say(f"✔ 登入成功（{'模擬' if service == 'sinopac-sim' else '正式'}環境）：" + "、".join(short(a) for a in accounts))
    logout(api)


if __name__ == "__main__":
    main()
