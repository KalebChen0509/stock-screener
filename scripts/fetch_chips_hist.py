"""回補台股三大法人＋融資融券歷史（每月彙總）。在 GitHub Actions 跑（籌碼歷史回補 workflow）。

python scripts/fetch_chips_hist.py --max-minutes 300
最後一行印 left=<還沒補的月份數>，workflow 用來判斷要不要繼續。
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from screener import chips_hist  # noqa: E402
from screener.common import log  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-minutes", type=float, default=300)
    ap.add_argument("--sleep", type=float, default=1.2)
    args = ap.parse_args()
    res = chips_hist.update(max_minutes=args.max_minutes, sleep=args.sleep)
    log.info("chips hist: %s", res)
    print(f"left={res['left']}")


if __name__ == "__main__":
    main()
