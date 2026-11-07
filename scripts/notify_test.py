"""通知測試：寄一封測試 Email、發一則測試 LINE（GitHub Actions「通知測試」手動執行）。只顯示成功／失敗，不印出金鑰。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from screener.notify import notify  # noqa: E402


def main():
    r = notify("【測試】選股系統通知測試", "如果你收到這則訊息，代表通知設定成功。\n市場重大事件會同時寄 Email 和 LINE。", scope="test")
    print(f"Email：{'✔ 成功' if r['email'] else '✘ 失敗或沒設定'}")
    print(f"LINE ：{'✔ 成功' if r['line'] else '✘ 失敗或沒設定（看上面的 LINE push failed 訊息）'}")
    if not (r["email"] or r["line"]):
        sys.exit(1)


if __name__ == "__main__":
    main()
