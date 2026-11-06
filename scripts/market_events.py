"""市場重大事件偵測（「市場事件偵測」workflow 每 30 分鐘、全天候執行）。

python scripts/market_events.py --store evbr
evbr＝events 分支的資料夾（state.json、events.csv、market_events.json）。
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from screener import market_events  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--store", default="evbr")
    args = ap.parse_args()
    res = market_events.run(args.store, pages_url=os.environ.get("PAGES_URL", ""))
    print(res)


if __name__ == "__main__":
    main()
