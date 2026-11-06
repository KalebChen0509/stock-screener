"""衍生性商品資料（期交所期貨、選擇權、法人、P/C 比；美國 VIX）。只用在網站訊號與研究，不影響選股、不會下單。

每日更新：python scripts/fetch_derivs.py --max-requests 40
回補（衍生性商品回補 workflow）：python scripts/fetch_derivs.py --max-requests 400
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from screener import derivs  # noqa: E402
from screener.common import SITE_DATA, log, write_json  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-requests", type=int, default=40)
    ap.add_argument("--sleep", type=float, default=2.0)
    args = ap.parse_args()
    try:
        res = derivs.update(max_requests=args.max_requests, sleep=args.sleep)
        log.info("derivs update: %s", res)
    except Exception as e:  # noqa
        log.warning("derivs update failed: %s", e)
    try:
        write_json(SITE_DATA / "derivs.json", derivs.summary())
    except Exception as e:  # noqa
        log.warning("derivs summary failed: %s", e)
    print("complete=" + ("1" if derivs.progress().get("complete") else "0"))


if __name__ == "__main__":
    main()
