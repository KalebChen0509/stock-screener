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
    ap.add_argument("--month-rev-reserve", type=int, default=int(os.environ.get("MONTH_REV_RESERVE", 200)),
                    help="月營收回填完成前，每次保留給月營收的 FinMind 額度")
    args = ap.parse_args()
    cfg = load_strategy()
    fm = data_tw.FinMind(max_calls=args.max_calls)
    info = data_tw.fetch_stock_info(fm)
    uni = data_tw.common_stock_universe(info)
    uni.to_csv(CACHE_DIR / "tw_universe.csv", index=False)
    log.info("TW universe: %d common stocks", len(uni))
    # 月營收還沒回填完：先保留一部分額度給月營收，避免每天都被季報更新用光
    import json
    mp_path = CACHE_DIR / "tw_month_rev_progress.json"
    mp_old = json.loads(mp_path.read_text()) if mp_path.exists() else {"have": 0, "total": 1}
    mr_done = mp_old.get("have", 0) >= 0.98 * max(mp_old.get("total", 1), 1)
    reserve = 0 if mr_done else max(0, min(args.month_rev_reserve, args.max_calls - fm.calls))
    fm.max_calls = args.max_calls - reserve
    log.info("FinMind budget: %d calls (fundamentals %d, month revenue reserve %d)",
             args.max_calls, fm.max_calls - fm.calls, reserve)
    data_tw.update_fundamentals(uni, cfg["reporting_lag"]["tw"], fm)
    fm.max_calls = args.max_calls
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
