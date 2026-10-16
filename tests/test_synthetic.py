"""用合成資料檢查整條流程（不需網路）。執行：python -m tests.test_synthetic"""
import numpy as np
import pandas as pd

from screener.backtest import perf_stats, run_backtest
from screener.common import load_etf_universe, load_strategy
from screener.etf import static_frame
from screener.metrics import fundamental_panel
from screener.data_us import duration_to_quarters
from screener.data_tw import _decumulate, _is_cumulative

rng = np.random.default_rng(0)


def synth_fund(market, ids, quarters):
    rows = []
    for sid in ids:
        good = rng.random() < 0.4
        ta = rng.uniform(1e9, 5e10)
        for q in quarters:
            rev = ta * rng.uniform(0.15, 0.3)
            gm = rng.uniform(0.3, 0.6) if good else rng.uniform(0.05, 0.3)
            gp = rev * gm
            oi = gp * (rng.uniform(0.7, 0.9) if good else rng.uniform(0.2, 0.6))
            nonop = oi * rng.uniform(-0.02, 0.03)
            pre = oi + nonop
            ni = pre * 0.8
            cfo = ni * rng.uniform(1.0, 1.4)
            capex = cfo * rng.uniform(0.1, 0.4)
            avail = q + pd.Timedelta(days=45 if q.quarter != 4 else 85)
            rows.append(dict(market=market, id=sid, period_end=q, available_date=avail,
                             revenue=rev, gross_profit=gp, op_income=oi, nonop=nonop, pretax=pre,
                             net_income=ni, rd=rev * (0.06 if rng.random() < 0.3 else 0.01), cfo=cfo, capex=capex, cfi=-capex * 1.1, cff=-cfo * (0.3 if good else -0.1),
                             cash=ta * (0.25 if good else 0.08), st_inv=0, receivables=ta * .1,
                             inventory=ta * .1, current_assets=ta * .5, total_assets=ta,
                             current_liab=ta * (.15 if good else .4), total_liab=ta * (.3 if good else .7),
                             equity=ta * (.7 if good else .3), goodwill=0, intangibles=ta * .02,
                             lt_inv=ta * .05, debt=ta * .05, shares=ta / 50))
    return pd.DataFrame(rows)


def main():
    cfg = load_strategy()
    # --- 小工具測試 ---
    facts = [
        {"start": "2023-01-01", "end": "2023-03-31", "val": 10, "filed": "2023-05-01", "form": "10-Q"},
        {"start": "2023-01-01", "end": "2023-06-30", "val": 25, "filed": "2023-08-01", "form": "10-Q"},
        {"start": "2023-01-01", "end": "2023-09-30", "val": 45, "filed": "2023-11-01", "form": "10-Q"},
        {"start": "2023-01-01", "end": "2023-12-31", "val": 70, "filed": "2024-02-20", "form": "10-K"},
    ]
    q = duration_to_quarters(facts)
    assert list(q["val"]) == [10, 15, 20, 25], q
    idx = pd.to_datetime(["2022-03-31", "2022-06-30", "2022-09-30", "2022-12-31",
                          "2023-03-31", "2023-06-30", "2023-09-30", "2023-12-31"])
    cum = pd.DataFrame({"x": [1, 3, 6, 10, 2, 5, 9, 14]}, index=idx, dtype=float)
    assert _is_cumulative(cum["x"])
    dq = _decumulate(cum, ["x"])
    assert list(dq["x"]) == [1, 2, 3, 4, 2, 3, 4, 5], dq
    print("unit checks ok")

    quarters = pd.date_range("2012-03-31", "2026-06-30", freq="QE")
    tw_ids = [f"{1000 + i}" for i in range(60)]
    us_ids = [f"T{i}" for i in range(60)]
    fund = pd.concat([synth_fund("tw", tw_ids, quarters), synth_fund("us", us_ids, quarters)])
    infos = {
        "tw": pd.DataFrame({"name": tw_ids, "industry": [f"ind{i % 5}" for i in range(60)],
                            "is_financial": False, "yahoo": [s + ".TW" for s in tw_ids]}, index=tw_ids),
        "us": pd.DataFrame({"name": us_ids, "industry": [f"ind{i % 5}" for i in range(60)],
                            "is_financial": False, "yahoo": us_ids}, index=us_ids),
    }
    yahoo_map = {**dict(zip(tw_ids, infos["tw"]["yahoo"])), **dict(zip(us_ids, us_ids))}
    panel = fundamental_panel(fund, None, yahoo_map)
    panels = {"tw": panel[panel.market == "tw"], "us": panel[panel.market == "us"]}

    months = pd.date_range("2011-01-31", "2026-09-30", freq="ME")
    etfs = load_etf_universe()
    tickers = list(infos["tw"]["yahoo"]) + us_ids + [e["ticker"] for e in etfs] + ["TWD=X", "^TWII", "^GSPC"]
    rows = []
    for t in tickers:
        p = 50 * np.exp(np.cumsum(rng.normal(0.008, 0.06, len(months))))
        if t == "TWD=X":
            p = 30 + np.cumsum(rng.normal(0, 0.3, len(months)))
        adjp = p * np.exp(np.arange(len(months)) * 0.003)
        rows.append(pd.DataFrame({"ticker": t, "month": months, "close": p, "adj_close": adjp,
                                  "volume": 5e6, "split": 0.0}))
    prices = pd.concat(rows)
    etf_static = static_frame(etfs, 30.0)
    out = run_backtest(cfg, panels, infos, prices, etf_static)
    m = out["monthly"]
    print(m.tail(3).T)
    for c in ["ret_gross", "ret_net"] + [c for c in m if c.startswith("bench_")]:
        st = perf_stats(m[c])
        print(f"{c:30s} CAGR {st['cagr']:.2%} MDD {st['max_drawdown']:.2%}")
    assert m["nav_net"].iloc[-1] < m["nav_gross"].iloc[-1]
    assert m["n_tw"].mean() > 0 and m["n_us"].mean() > 0 and m["n_etf"].mean() > 0
    print("synthetic backtest ok; months", len(m), "total cost", m.attrs["total_cost"])


if __name__ == "__main__":
    main()
