"""測試 Mac 能不能發 LINE（讀鑰匙圈 stock-line：token／user_id）。只發一則測試訊息，不印金鑰。

    .venv/bin/python scripts/mac_line_test.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mac_events import keychain, load_secrets  # noqa: E402
from screener.notify import send_line  # noqa: E402


def main():
    if not keychain("stock-line", "token") or not keychain("stock-line", "user_id"):
        print("✘ 鑰匙圈裡少了 stock-line 的 token 或 user_id，請先照說明設定。")
        return 1
    load_secrets()
    ok = send_line("✅ 測試：Mac 已經可以發 LINE 了（選股系統）")
    print("✔ 已發出，請看手機 LINE" if ok else "✘ 發送失敗：上面的訊息會說明是 token 還是 user ID 有問題")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
