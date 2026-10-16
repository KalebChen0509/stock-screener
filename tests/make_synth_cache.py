"""產生合成快取，用來在沒有網路時測試 update_daily / run_backtest / 網站。"""
import os, sys
import numpy as np, pandas as pd
from tests.test_synthetic import synth_fund, rng
from screener.common import CACHE_DIR, load_etf_universe, ensure_dir

def main():
    ensure_dir(CACHE_DIR / "tw_fin")
    quarters = pd.date_range("2012-03-31", "2026-06-30", freq="QE")
    tw_ids = [f"{1101 + i}" for i in range(80)]
    us_ids = [f"TK{chr(65+i%26)}{i}" for i in range(80)]
    inds_tw = ["半導體業", "電子零組件業", "食品工業", "電腦及週邊設備業", "金融保險業"]
    pd.DataFrame({"stock_id": tw_ids, "stock_name": [f"台灣公司{i}" for i in range(80)],
                  "industry_category": [inds_tw[i % 5] for i in range(80)], "type": "twse",
                  "is_financial": [i % 5 == 4 for i in range(80)],
                  "yahoo": [s + ".TW" for s in tw_ids]}).to_csv(CACHE_DIR / "tw_universe.csv", index=False)
    f = synth_fund("tw", tw_ids, quarters)
    for sid, g in f.groupby("id"):
        g.to_csv(CACHE_DIR / "tw_fin" / f"{sid}.csv.gz", index=False, compression="gzip")
    inds_us = ["Software & Business Services", "Electronics & Semis", "Retail", "Machinery & Computers", "Banks"]
    pd.DataFrame({"cik": range(1000, 1080), "ticker": us_ids, "name": [f"US Corp {i}" for i in range(80)],
                  "exchange": "NYSE", "sic": "3570", "sic_desc": "", "industry": [inds_us[i % 5] for i in range(80)],
                  "is_financial": [i % 5 == 4 for i in range(80)], "yahoo": us_ids}).to_csv(CACHE_DIR / "us_universe.csv", index=False)
    fu = synth_fund("us", us_ids, quarters)
    fu["shares_date"] = fu["period_end"]
    fu.to_csv(CACHE_DIR / "us_fundamentals.csv.gz", index=False, compression="gzip")
    months = pd.date_range("2011-01-31", "2026-10-31", freq="ME")
    etfs = load_etf_universe()
    tickers = [s + ".TW" for s in tw_ids] + us_ids + [e["ticker"] for e in etfs] + ["TWD=X", "^TWII", "^GSPC", "^RLV", "^RUJ"]
    rows = []
    for t in tickers:
        p = 50 * np.exp(np.cumsum(rng.normal(0.007, 0.06, len(months))))
        if t == "TWD=X":
            p = 31 + np.cumsum(rng.normal(0, 0.3, len(months)))
        rows.append(pd.DataFrame({"ticker": t, "month": months, "close": p,
                                  "adj_close": p * np.exp(np.arange(len(months)) * 0.003),
                                  "volume": 3e6, "split": 0.0}))
    pd.concat(rows).to_csv(CACHE_DIR / "prices_monthly.csv.gz", index=False, compression="gzip")
    print("synthetic cache at", CACHE_DIR)

if __name__ == "__main__":
    main()
