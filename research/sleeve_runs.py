"""【2026/11/06】輸出 research/out/sleeves.pkl（約 20 分鐘）。跑各版本（不同停損）的回測，存下台股、美股選股部位與總報酬的月報酬，給避險比較用。"""
import os  # noqa: F401
import sys  # noqa: F401
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _paths import CACHE, OUT, ROOT  # noqa: E402,F401
import pickle
import sys

import pandas as pd

from screener import prices  # noqa: E402
from screener.backtest import run_backtest  # noqa: E402
from screener.common import CACHE_DIR, apply_patch, load_etf_universe, load_strategy  # noqa: E402
from screener.etf import static_frame  # noqa: E402
from screener.loaders import load_infos, load_panels  # noqa: E402
from screener import month_rev as mrev  # noqa: E402

cfg = load_strategy()
G = {"portfolio": {"stock_style": "growth", "weighting": "cap_capped", "max_weight": 0.10},
     "risk": {"stop_loss": {"basis": "vol", "proceeds": "redistribute"}}}
M = {"portfolio": {"stock_style": "momsue"}, "risk": {"stop_loss": {"basis": "vol", "proceeds": "redistribute"}}}
OFF = {"risk": {"stop_loss": {"enabled": False}}}
MON = {"risk": {"stop_loss": {"check": "monthly"}}}
RUNS = {  # 名稱: (patch 列表, 用日線)
    "價值｜每日停損（現行）": ([], True),
    "價值｜不停損": ([OFF], False),
    "成長｜每日停損": ([G], True),
    "成長｜月底停損": ([G, MON], False),
    "成長｜不停損": ([G, OFF], False),
    "動能＋驚喜｜月底停損": ([M, MON], False),
    "動能＋驚喜｜不停損": ([M, OFF], False),
}
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
out, sc = {}, {}
for name, (patches, ud) in RUNS.items():
    cc = cfg
    for p in patches:
        cc = apply_patch(cc, p)
    o = run_backtest(cc, panels, infos, monthly, es, screen_cache=sc, month_rev=mrw, daily=daily if ud else None)
    m = o["monthly"]
    out[name] = m[["sleeve_tw", "sleeve_us", "ret_net", "bench_0050.TW", "bench_SPY"]].copy()
    print(name, "done", flush=True)
pickle.dump(out, open(OUT / "sleeves.pkl", "wb"))
