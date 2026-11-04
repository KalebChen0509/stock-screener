"""檢查正式環境：帳戶有沒有通過 API 測試、能不能讀到持股（只讀，不下單；不顯示金額）。

執行：~/.sinopac-venv/bin/python ~/PycharmProjects/stock-screener/trader/check_account.py
畫面只顯示帳號末四碼和檔數，可以放心截圖給 Claude。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sinopac import login, logout, say, short  # noqa: E402


def main():
    api, accounts = login(simulation=False)
    say("✔ 正式環境登入成功（這把 Key 沒有交易權限，不能下單）")
    for a in accounts:
        signed = getattr(a, "signed", None)
        mark = "✔ 已通過 API 測試" if signed else "✘ 還沒通過 API 測試（或還沒簽署）"
        say(f"  {short(a)}：{mark}")
    if api.stock_account is None:
        say("✘ 沒有看到證券帳戶：確認這把 Key 的「選擇帳戶」有勾證券帳戶。")
    else:
        try:
            pos = api.list_positions(api.stock_account)
            say(f"✔ 讀得到台股庫存：共 {len(pos)} 檔（這裡不顯示股數和金額）")
        except Exception as e:  # noqa
            say(f"✘ 讀不到台股庫存：{e.__class__.__name__}（確認這把 Key 有勾「帳務」）")
    logout(api)


if __name__ == "__main__":
    main()
