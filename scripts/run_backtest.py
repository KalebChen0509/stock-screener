"""執行 10 年回測，輸出 site/data/backtest.json。"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np  # noqa: E402
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
    events = {}
    try:
        from screener.events import load_tw_events, load_us_events
        from screener.data_us import load_universe
        events = {"us": load_us_events(load_universe()), "tw": load_tw_events()}
    except Exception as e:  # noqa
        log.warning("events not loaded: %s", e)
    out = run_backtest(cfg, panels, infos, monthly, etf_static, start=args.start, events=events)
    m = out["monthly"]
    cap = cfg["portfolio"]["initial_capital"]
    rf = cfg["backtest"].get("risk_free", 0.0)

    series_cols = ["ret_gross", "ret_net"] + [c for c in m.columns if c.startswith("bench_")]
    labels = {"ret_gross": "策略（未扣費）", "ret_net": "策略（扣費後）",
              "sleeve_tw": "台股個股部位", "sleeve_us": "美股個股部位", "sleeve_etf": "ETF 部位"}
    sleeve_cols = [c for c in ("sleeve_tw", "sleeve_us", "sleeve_etf") if c in m]
    for c in series_cols:
        if c.startswith("bench_"):
            labels[c] = c[6:]

    def stats_for(sub):
        return {labels[c]: {**perf_stats(sub[c], rf),
                            "final_amount": float(cap * (1 + sub[c]).prod())} for c in series_cols}

    last5 = m[m.index > m.index[-1] - pd.DateOffset(years=5)]

    def sleeves_for(sub):
        res = {}
        pairs = {"sleeve_tw": "bench_0050.TW", "sleeve_us": "bench_SPY", "sleeve_etf": "bench_VT"}
        for c in sleeve_cols:
            st = perf_stats(sub[c], rf)
            b = pairs.get(c)
            if b in sub:
                st["bench"] = b[6:]
                st["bench_cagr"] = perf_stats(sub[b], rf).get("cagr")
            res[labels[c]] = st
        return res

    def regimes_for(sub):
        """各種市況下的表現：以混合基準為大盤。"""
        bcol = "bench_混合基準(20/20/60)"
        if bcol not in sub:
            return {}
        b, sN, sG = sub[bcol], sub["ret_net"], sub["ret_gross"]
        up, dn = b > 0, b < 0
        out = {
            "up_capture": float(sN[up].mean() / b[up].mean()) if up.any() else None,
            "down_capture": float(sN[dn].mean() / b[dn].mean()) if dn.any() else None,
            "win_rate_vs_bench": float((sN > b).mean()),
            "up_months": int(up.sum()), "down_months": int(dn.sum()),
            "avg_excess_up": float((sN - b)[up].mean()) if up.any() else None,
            "avg_excess_down": float((sN - b)[dn].mean()) if dn.any() else None,
        }
        # 滾動 12 個月：大盤空頭年（12 個月報酬 < 0）vs 多頭年
        rb = (1 + b).rolling(12).apply(np.prod, raw=True) - 1
        rs = (1 + sN).rolling(12).apply(np.prod, raw=True) - 1
        ok = rb.notna()
        out["rolling12_beat_rate"] = float((rs[ok] >= rb[ok]).mean()) if ok.any() else None
        out["rolling12_beat_rate_bear"] = float((rs[ok & (rb < 0)] >= rb[ok & (rb < 0)]).mean()) if (ok & (rb < 0)).any() else None
        out["rolling12_beat_rate_bull"] = float((rs[ok & (rb >= 0)] >= rb[ok & (rb >= 0)]).mean()) if (ok & (rb >= 0)).any() else None
        crises = {"2018Q4 升息與貿易戰": ("2018-10", "2018-12"), "2020 新冠崩盤": ("2020-02", "2020-03"),
                  "2020-21 復甦": ("2020-04", "2021-12"), "2022 升息熊市": ("2022-01", "2022-10"),
                  "2023-24 AI 多頭": ("2023-01", "2024-12")}
        cr = {}
        for k, (a, z) in crises.items():
            w = sub.loc[(sub.index >= pd.Timestamp(a)) & (sub.index <= pd.Timestamp(z) + pd.offsets.MonthEnd(0))]
            if len(w):
                cr[k] = {"strategy_net": float((1 + w["ret_net"]).prod() - 1),
                         "strategy_gross": float((1 + w["ret_gross"]).prod() - 1),
                         "bench": float((1 + w[bcol]).prod() - 1)}
        out["periods"] = cr
        return out
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
        "sleeves_full": sleeves_for(m), "sleeves_5y": sleeves_for(last5),
        "regimes_full": regimes_for(m), "regimes_5y": regimes_for(last5),
        "sleeve_nav": {labels[c]: list((cap * (1 + m[c].fillna(0)).cumprod()).round(0)) for c in sleeve_cols},
        "data_coverage": {mk: int(panels[mk]["id"].nunique()) for mk in panels},
    }
    write_json(Path(args.out), res)
    for k, v in res["stats_full"].items():
        log.info("%-22s CAGR %6.2f%%  MDD %6.2f%%  final %12.0f", k, 100 * v["cagr"],
                 100 * v["max_drawdown"], v["final_amount"])


if __name__ == "__main__":
    main()
