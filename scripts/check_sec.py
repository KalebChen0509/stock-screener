"""檢查美股 8-K 公告能不能抓（鑰匙圈 stock-sec／email）。不會印出 email、不會寄信、不會改任何紀錄。

    .venv/bin/python scripts/check_sec.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import requests  # noqa: E402

from mac_events import PAGES, REPO, keychain  # noqa: E402
from screener import stock_news as N  # noqa: E402


def main():
    email = keychain("stock-sec", "email")
    if not email:
        print("✘ 鑰匙圈裡找不到 stock-sec／email。請先執行：security add-generic-password -s stock-sec -a email -w")
        return 1
    if "@" not in email:
        print("✘ 鑰匙圈裡存的不像 email（沒有 @）。刪掉重設：security delete-generic-password -s stock-sec -a email")
        return 1
    ua = f"stock-screener personal-research {email}"
    r = requests.get(N.SEC_CURRENT, timeout=60, headers={"User-Agent": ua})
    if r.status_code != 200:
        print(f"✘ SEC 拒絕連線（HTTP {r.status_code}）。email 格式可能有誤，或稍後再試。")
        return 1
    st = {}
    W = N.build_watchlist(PAGES, REPO)
    ciks = N.sec_cik_map(ua, st)
    have = sum(1 for t in W["us"] if t in ciks)
    print(f"✔ SEC 連線成功：最新 8-K 清單正常；你的美股清單 {len(W['us'])} 檔，其中 {have} 檔找得到 SEC 編號（會追蹤它們的 8-K）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
