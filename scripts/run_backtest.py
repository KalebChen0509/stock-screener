"""執行 10 年回測，輸出 site/data/backtest.json。"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd  # noqa: E402
from pathlib import Path  # noqa: E402

from screener import prices  # noqa: E402
from screener.backtest import perf_stats, run_backtest  # noqa: E402
from screener.common import SITE_DATA, load_etf_universe, load_strategy, log, write_json  # noqa: E402
from screener.etf import static_frame  # noqa: E402
from screener.loaders import load_infos, load_panels  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default=None)
    ap.add_argument("--out", default=str(SITE_DATA / "backtest.json"))
    args = ap.parse_args()
    cfg = load_strategy()
    monthly = prices.load_monthly()
    infos = load_infos()
    panels, _ = load_panels(infos, monthly)
    fx_now = monthly[monthly["ticker"] == "TWD=X"].sort_values("month")["close"].iloc[-1]
    etf_static = static_frame(load_etf_universe(), fx_now)
    out = run_backtest(cfg, panels, infos, monthly, etf_static, start=args.start)
    m = out["monthly"]
    cap = cfg["portfolio"]["initial_capital"]
    rf = cfg["backtest"].get("risk_free", 0.0)

    series_cols = ["ret_gross", "ret_net"] + [c for c in m.columns if c.startswith("bench_")]
    labels = {"ret_gross": "策略（未扣費）", "ret_net": "策略（扣費後）"}
    for c in series_cols:
        if c.startswith("bench_"):
            labels[c] = c[6:]

    def stats_for(sub):
        return {labels[c]: {**perf_stats(sub[c], rf),
                            "final_amount": float(cap * (1 + sub[c]).prod())} for c in series_cols}

    last5 = m[m.index > m.index[-1] - pd.DateOffset(years=5)]
    names = {mk: infos[mk]["name"].to_dict() for mk in infos}
    etf_names = {e["ticker"]: e["name"] for e in load_etf_universe()}
    hold = []
    for h in out["holdings"]:
        hold.append({
            "month": h["month"].strftime("%Y-%m"),
            "tw": [[s, names.get("tw", {}).get(s, "")] for s in h["tw"]],
            "us": [[s, names.get("us", {}).get(s, "")] for s in h["us"]],
            "etf": [[s, etf_names.get(s, "")] for s in h["etf"]],
        })
    res = {
        "generated": pd.Timestamp.now(tz="Asia/Taipei").strftime("%Y-%m-%d %H:%M"),
        "start": m.index[0].strftime("%Y-%m"), "end": m.index[-1].strftime("%Y-%m"),
        "initial_capital": cap,
        "weights": cfg["portfolio"]["weights"],
        "costs": cfg["costs"],
        "months": [d.strftime("%Y-%m") for d in m.index],
        "nav": {labels[c]: list((cap * (1 + m[c].fillna(0)).cumprod()).round(0)) for c in series_cols},
        "stats_full": stats_for(m),
        "stats_5y": stats_for(last5),
        "counts": {"tw": list(m["n_tw"]), "us": list(m["n_us"]), "etf": list(m["n_etf"]),
                   "cash": list(m["cash_weight"].round(4))},
        "cost_total_pct": float(m.attrs.get("total_cost", 0)),
        "withholding_total_pct": float(m.attrs.get("total_withholding", 0)),
        "avg_monthly_cost": float(m["cost"].mean()),
        "holdings": hold,
        "data_coverage": {mk: int(panels[mk]["id"].nunique()) for mk in panels},
    }
    write_json(Path(args.out), res)
    for k, v in res["stats_full"].items():
        log.info("%-22s CAGR %6.2f%%  MDD %6.2f%%  final %12.0f", k, 100 * v["cagr"],
                 100 * v["max_drawdown"], v["final_amount"])


if __name__ == "__main__":
    main()
