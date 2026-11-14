"""【2026/11/14】獨立重驗「成長版每日停損比月底檢查／不停損差」（11/05、11/06 的結論）。

第一步（這支）：用正式回測引擎重跑關鍵方案，把每月報酬、停損紀錄、持股存下來（research/out/stop_verify/runs.pkl）。
跟 11/06 的 stop_var.py 不同的地方：
  - 月底檢查同時跑兩種做法：不給日線（stop_var 的做法）與 check: monthly＋有日線（正式設定的做法），兩者應該接近
  - 多一組「日線先修錯價」（prices.repair_tw_jumps）：確認結論不是 Yahoo 錯價造成的假停損
  - 多一組「觸發當天收盤就賣」（exit_delay_days=0）：確認不是隔日賣出的假設造成的
第二步：stop_verify_analyze.py（顯著性、停損後的走勢、錯價檢查）。

    SCREENER_CACHE=<data 分支>/cache .venv/bin/python research/stop_verify.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _paths import OUT  # noqa: E402

import pickle  # noqa: E402

import pandas as pd  # noqa: E402

from screener import month_rev as mrev  # noqa: E402
from screener import prices  # noqa: E402
from screener.backtest import perf_stats, run_backtest  # noqa: E402
from screener.common import CACHE_DIR, apply_patch, load_etf_universe, load_strategy  # noqa: E402
from screener.etf import static_frame  # noqa: E402
from screener.loaders import load_infos, load_panels  # noqa: E402

D = OUT / "stop_verify"
D.mkdir(exist_ok=True)
base = load_strategy()
G = {"portfolio": {"stock_style": "growth", "weighting": "cap_capped", "max_weight": 0.10},
     "risk": {"stop_loss": {"basis": "vol", "proceeds": "redistribute"}}}


def sl(**k):
    return {"risk": {"stop_loss": k}}


# (名稱, 基礎, 額外 patch, 日線："raw"／"fixed"／None)
VARS = [
    ("成長｜每日停損（現行）", G, {}, "raw"),
    ("成長｜月底檢查（不給日線）", G, {}, None),
    ("成長｜月底檢查（check: monthly）", G, sl(check="monthly"), "raw"),
    ("成長｜不停損", G, sl(enabled=False), None),
    ("成長｜每日停損＋日線修錯價", G, {}, "fixed"),
    ("成長｜每日停損＋當天收盤賣", G, sl(exit_delay_days=0), "raw"),
    ("價值｜每日停損（現行）", {}, {}, "raw"),
    ("價值｜月底檢查（check: monthly）", {}, sl(check="monthly"), "raw"),
    ("價值｜不停損", {}, sl(enabled=False), None),
    ("價值｜每日停損＋日線修錯價", {}, {}, "fixed"),
]

monthly = prices.load_monthly(include_delisted=True)
infos = load_infos(include_delisted=True)
panels, _ = load_panels(infos, monthly)
fx_now = monthly[monthly["ticker"] == "TWD=X"].sort_values("month")["close"].iloc[-1]
es = static_frame(load_etf_universe(), fx_now)
mrw = mrev.wide()
c = pd.read_csv(CACHE_DIR / "daily_bt.csv.gz", parse_dates=["date"])
daily = c.pivot_table(index="date", columns="ticker", values="adj_close", aggfunc="last").sort_index()
dl = prices.load_delisted_daily()
if len(dl):
    daily = pd.concat([daily, dl[[x for x in dl.columns if x not in daily.columns]]], axis=1).sort_index()
fixed, fixlog = prices.repair_tw_jumps(daily)
print(f"日線：{daily.shape[1]} 檔、{daily.index[0].date()}～{daily.index[-1].date()}；修錯價 {len(fixlog)} 檔、{sum(len(v) for v in fixlog.values())} 天", flush=True)
pickle.dump({"daily": daily, "fixed": fixed, "fixlog": fixlog}, open(D / "daily.pkl", "wb"))

runs, sc = {}, {}
for name, b, p, dk in VARS:
    cfg = apply_patch(base, b) if b else base
    cfg = apply_patch(cfg, p) if p else cfg
    o = run_backtest(cfg, panels, infos, monthly, es, screen_cache=sc, month_rev=mrw,
                     daily={"raw": daily, "fixed": fixed, None: None}[dk])
    m = o["monthly"]
    runs[name] = {"monthly": m, "stops": o["stops"], "spells": o["spells"], "holdings": o["holdings"]}
    f = perf_stats
    print(name, {"net": round(f(m["ret_net"])["cagr"], 4), "mdd": round(f(m["ret_net"])["max_drawdown"], 4),
                 "tw": round(f(m["sleeve_tw"])["cagr"], 4), "tw_mdd": round(f(m["sleeve_tw"])["max_drawdown"], 4),
                 "us": round(f(m["sleeve_us"])["cagr"], 4), "stops": len(o["stops"]),
                 "期間": f"{m.index[0]:%Y-%m}～{m.index[-1]:%Y-%m}"}, flush=True)
    pickle.dump(runs, open(D / "runs.pkl", "wb"))
