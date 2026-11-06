"""抓美股下市公司（抽樣）的日線，用來估計倖存者偏差。只在 GitHub Actions 跑（TIINGO_API_KEY 在 Secrets）。

python scripts/fetch_us_delisted.py --max-requests 45 --sleep 75
最後一行印 left=<還沒抓的檔數>，workflow 用來判斷要不要繼續。
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from screener import us_delisted  # noqa: E402
from screener.common import log  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-requests", type=int, default=45)
    ap.add_argument("--sleep", type=float, default=75.0)
    args = ap.parse_args()
    res = us_delisted.fetch(max_requests=args.max_requests, sleep=args.sleep)
    log.info("result %s", res)
    print(f"left={res['left']}")


if __name__ == "__main__":
    main()
