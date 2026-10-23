"""更新台股股票清單與財報快取（每次執行有 API 請求上限，可重複執行慢慢回填）。"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from screener.common import load_strategy, log, write_json, CACHE_DIR  # noqa: E402
from screener import data_tw  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-calls", type=int, default=int(os.environ.get("FINMIND_MAX_CALLS", 550)))
    args = ap.parse_args()
    cfg = load_strategy()
    fm = data_tw.FinMind(max_calls=args.max_calls)
    info = data_tw.fetch_stock_info(fm)
    uni = data_tw.common_stock_universe(info)
    uni.to_csv(CACHE_DIR / "tw_universe.csv", index=False)
    log.info("TW universe: %d common stocks", len(uni))
    data_tw.update_fundamentals(uni, cfg["reporting_lag"]["tw"], fm)
    # 月營收：OpenAPI 補最新月份，剩餘 FinMind 額度回填歷史
    try:
        from screener import month_rev
        mp = month_rev.update(uni, fm, data_tw._liquidity_rank(uni))
        write_json(CACHE_DIR / "tw_month_rev_progress.json", mp)
    except Exception as e:  # noqa
        log.warning("month revenue update failed: %s", e)
    prog = data_tw.backfill_progress(uni)
    log.info("TW backfill progress: %s", prog)
    write_json(CACHE_DIR / "tw_progress.json", prog)


if __name__ == "__main__":
    main()
