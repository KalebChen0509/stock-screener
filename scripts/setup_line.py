"""把 LINE 的 channel access token 和 user ID 存進 Mac 鑰匙圈（stock-line），存完馬上發一則測試訊息。

為什麼要用這支：`security add-generic-password -w` 用互動輸入時最多只收 128 個字元，
LINE 的 token 約 170 字元會被截斷 → 401（11/13 實際發生）。這裡用 security 的互動模式寫入（沒有長度限制，值不會出現在指令列），已存在就覆蓋。

    .venv/bin/python scripts/setup_line.py
"""
import getpass
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def store(account, value):
    r = subprocess.run(["security", "-i"], input=f'add-generic-password -U -s stock-line -a {account} -w "{value}"\n',
                       text=True, capture_output=True)
    if r.returncode != 0:
        sys.exit(f"存入鑰匙圈失敗：{r.stderr.strip()[:200]}")


def main():
    print("貼上時畫面不會顯示，貼完按 Enter。")
    tok = re.sub(r"\s+", "", getpass.getpass("Channel access token (long-lived，約 170 字元)："))
    if len(tok) < 100 or '"' in tok:
        sys.exit(f"✘ 長度只有 {len(tok)} 字元，不像 long-lived token（32 字元的是 Channel secret）。沒有存。")
    uid = re.sub(r"\s+", "", getpass.getpass("Your user ID（U 開頭、33 字元）："))
    if not re.fullmatch(r"U[0-9a-f]{32}", uid):
        sys.exit(f"✘ user ID 格式不對（長度 {len(uid)}、{'是' if uid.startswith('U') else '不是'} U 開頭）。沒有存。")
    store("token", tok)
    store("user_id", uid)
    print(f"✔ 已存入鑰匙圈（token {len(tok)} 字元）。發送測試訊息…")
    from scripts_mac_line_test import main as test  # noqa
    return test()


if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import mac_line_test
    sys.modules["scripts_mac_line_test"] = mac_line_test
    sys.exit(main())
