"""並行模擬的檢查（不需網路）。執行：python -m tests.test_paper"""
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd

from screener import paper
from screener.backtest import run_backtest
from screener.common import apply_patch, load_etf_universe, load_strategy
from screener.etf import static_frame
from screener.metrics import fundamental_panel
from tests.test_synthetic import synth_fund


def main():
    rng = np.random.default_rng(11)
    cfg = apply_patch(load_strategy(), {"paper": {"start": "2026-08-01"}})
    quarters = pd.date_range("2012-03-31", "2026-06-30", freq="QE")
    tw_ids = [f"{4000 + i}" for i in range(40)]
    us_ids = [f"P{i}" for i in range(40)]
    fund = pd.concat([synth_fund("tw", tw_ids, quarters), synth_fund("us", us_ids, quarters)])
    infos = {"tw": pd.DataFrame({"name": [f"台{i}" for i in tw_ids], "industry": ["電子零組件業"] * 20 + ["食品工業"] * 20,
                                 "is_financial": False, "yahoo": [s + ".TW" for s in tw_ids]}, index=tw_ids),
             "us": pd.DataFrame({"name": us_ids, "industry": ["Retail"] * 40, "is_financial": False,
                                 "yahoo": us_ids, "sic": "5311"}, index=us_ids)}
    for k in infos:
        infos[k].index.name = "id"
    ymap = {**dict(zip(tw_ids, infos["tw"]["yahoo"])), **dict(zip(us_ids, us_ids))}
    panel = fundamental_panel(fund, None, ymap)
    panels = {"tw": panel[panel.market == "tw"], "us": panel[panel.market == "us"]}
    months = pd.date_range("2011-01-31", "2026-09-30", freq="ME")
    etfs = load_etf_universe()
    rows = []
    for t in list(ymap.values()) + ["^TWII", "^GSPC"] + [e["ticker"] for e in etfs] + ["TWD=X"]:
        p_ = 30 + np.cumsum(rng.normal(0, 0.2, len(months))) if t == "TWD=X" else \
            50 * np.exp(np.cumsum(rng.normal(0.008, 0.05, len(months))))
        rows.append(pd.DataFrame({"ticker": t, "month": months, "close": p_, "adj_close": p_, "volume": 5e6, "split": 0.0}))
    monthly = pd.concat(rows)
    adj = monthly.pivot_table(index="month", columns="ticker", values="adj_close")
    etf_static = static_frame(etfs, 30.0)

    # ---------- 回測：最後一個月底也做決策；「只買 ETF」全部放大盤 ----------
    o = run_backtest(apply_patch(cfg, {"portfolio": {"stock_style": "index"}}), panels, infos, monthly, etf_static,
                     start="2026-08-01", end="2026-08-31", decide_last=True)
    assert len(o["monthly"]) == 1 and o["holdings"][-1].get("final")
    w = {x["ticker"]: round(x["w"], 6) for x in o["holdings"][-1]["weights"]}
    assert w == {"0050.TW": 0.25, "SPY": 0.25, "VT": 0.5}, w
    o0 = run_backtest(cfg, panels, infos, monthly, etf_static, start="2026-08-01", end="2026-07-31", decide_last=True)
    assert o0["monthly"].empty and len(o0["holdings"]) == 1, "only the first decision so far"
    print("decide_last / index style ok")

    # ---------- 日線（測試用：月線內插成日線）----------
    days = pd.bdate_range("2026-05-01", "2026-09-15")

    def loader(tks, start):
        sub = adj[[t for t in tks if t in adj]].loc["2026-04-30":]
        return sub.reindex(sub.index.union(days)).interpolate().reindex(days)

    with tempfile.TemporaryDirectory() as d:
        old = paper.PAPER_LOG
        paper.PAPER_LOG = Path(d) / "paper_log.json"
        try:
            r0 = paper.update(cfg, panels, infos, monthly, etf_static, today="2026-07-20", daily_loader=loader)
            assert r0["status"] == "waiting" and not paper.PAPER_LOG.exists()
            r = paper.update(cfg, panels, infos, monthly, etf_static, today="2026-09-15", daily_loader=loader)
            assert r["status"] == "running" and len(r["versions"]) == 4, r
            for v in r["versions"]:
                assert v["decision_month"] == "2026-08" and v["months"] == ["2026-08"], v
                assert v["cum_closed"] is not None and v["mtd"] is not None and v["mtd_to"] == "2026-09-15"
                assert abs((1 + v["cum_closed"]) * (1 + v["mtd"]) - 1 - v["cum"]) < 1e-12
                assert sum(x[2] for k in v["holdings"] for x in v["holdings"][k]) > 0.99
            etf = next(v for v in r["versions"] if v["name"].startswith("只買 ETF"))
            assert {x[0] for x in etf["holdings"]["etf"]} == {"0050.TW", "SPY", "VT"} and not etf["holdings"]["tw"]
            # 只買 ETF 的當月估計 = 三檔 ETF 加權（美股乘匯率）
            dl = loader(["0050.TW", "SPY", "VT", "TWD=X"], None)
            t0, t1 = pd.Timestamp("2026-08-31"), dl.index[-1]
            fx = dl.at[t1, "TWD=X"] / dl.at[t0, "TWD=X"]
            exp = 0.25 * (dl.at[t1, "0050.TW"] / dl.at[t0, "0050.TW"] - 1) + \
                0.25 * (dl.at[t1, "SPY"] / dl.at[t0, "SPY"] * fx - 1) + 0.5 * (dl.at[t1, "VT"] / dl.at[t0, "VT"] * fx - 1)
            assert abs(etf["mtd"] - exp) < 1e-9, (etf["mtd"], exp)
            assert etf.get("vs_etf") == 0 and all("vs_etf" in v for v in r["versions"])
            import json
            log1 = json.loads(paper.PAPER_LOG.read_text())
            g = "成長動能（單檔上限 10%）"
            assert set(log1[g]) == {"2026-07", "2026-08"}
            locked = {x["ticker"] for x in log1[g]["2026-08"]["weights"]}
            # 規則或資料改變：鎖定的決策不變，並標示「決策有變動」
            cfg2 = apply_patch(cfg, {"growth": {"filters": [], "max_holdings": 5}})
            r2 = paper.update(cfg2, panels, infos, monthly, etf_static, today="2026-09-15", daily_loader=loader)
            v2 = next(v for v in r2["versions"] if v["name"] == g)
            shown = {ymap[x[0]] for k in ("tw", "us") for x in v2["holdings"][k]} | {x[0] for x in v2["holdings"]["etf"]}
            assert shown == locked, ("site shows the locked picks", shown ^ locked)
            log2 = json.loads(paper.PAPER_LOG.read_text())
            assert log2[g]["2026-08"] == log1[g]["2026-08"], "locked decisions never change"
            assert v2["changed_since_locked"], "recomputed picks differ → flagged"
        finally:
            paper.PAPER_LOG = old
    print("paper trading ok:", {v["name"]: round(v["cum"], 4) for v in r["versions"]})


if __name__ == "__main__":
    main()
