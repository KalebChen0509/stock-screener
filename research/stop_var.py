"""停損規則比較（10 組，2026/11/06）：輸出 research/out/stop_var.json（約 30 分鐘）。"""
import os  # noqa: F401
import sys  # noqa: F401
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _paths import CACHE, OUT, ROOT  # noqa: E402,F401
import sys, json, pandas as pd
from screener import prices
from screener.backtest import run_backtest, perf_stats
from screener.common import apply_patch, load_etf_universe, load_strategy, CACHE_DIR
from screener.etf import static_frame
from screener.loaders import load_infos, load_panels
from screener import month_rev as mrev
base = load_strategy()
G = {"portfolio": {"stock_style": "growth", "weighting": "cap_capped", "max_weight": 0.10},
     "risk": {"stop_loss": {"basis": "vol", "proceeds": "redistribute"}}}
def sl(**k): return {"risk": {"stop_loss": k}}
VARS = [  # (名稱, 基礎, 額外 patch, 用日線)
 ("成長｜現行（每日、冷卻3月）", G, {}, True),
 ("成長｜只在月底檢查", G, {}, False),
 ("成長｜冷卻改 1 個月", G, sl(cooldown_months=1), True),
 ("成長｜停損放寬（波動×3.5，上限50%）", G, sl(vol_mult=3.5, vol_max=0.5), True),
 ("成長｜放寬＋冷卻1個月", G, sl(vol_mult=3.5, vol_max=0.5, cooldown_months=1), True),
 ("成長｜站上10月均線才買回", G, sl(reentry="ma"), True),
 ("成長｜不停損", G, sl(enabled=False), False),
 ("價值｜現行（買進價15%、冷卻3月）", {}, {}, True),
 ("價值｜冷卻改 1 個月", {}, sl(cooldown_months=1), True),
 ("價值｜只在月底檢查", {}, {}, False),
]
monthly = prices.load_monthly(include_delisted=True); infos = load_infos(include_delisted=True)
panels, _ = load_panels(infos, monthly)
fx_now = monthly[monthly["ticker"] == "TWD=X"].sort_values("month")["close"].iloc[-1]
es = static_frame(load_etf_universe(), fx_now); mrw = mrev.wide()
c = pd.read_csv(CACHE_DIR / "daily_bt.csv.gz", parse_dates=["date"])
daily = c.pivot_table(index="date", columns="ticker", values="adj_close", aggfunc="last").sort_index()
dl = prices.load_delisted_daily()
if len(dl):
    daily = pd.concat([daily, dl[[x for x in dl.columns if x not in daily.columns]]], axis=1).sort_index()
res, sc = {}, {}
for name, b, p, ud in VARS:
    cfg = apply_patch(apply_patch(base, b) if b else base, p) if p else (apply_patch(base, b) if b else base)
    o = run_backtest(cfg, panels, infos, monthly, es, screen_cache=sc, month_rev=mrw, daily=daily if ud else None)
    m = o["monthly"]; h = len(m) // 2
    f = lambda s: perf_stats(s)
    res[name] = {"net": f(m["ret_net"])["cagr"], "mdd": f(m["ret_net"])["max_drawdown"], "sharpe": f(m["ret_net"])["sharpe"],
                 "tw": f(m["sleeve_tw"])["cagr"], "tw_mdd": f(m["sleeve_tw"])["max_drawdown"], "us": f(m["sleeve_us"])["cagr"],
                 "tw_h1": f(m["sleeve_tw"].iloc[:h])["cagr"], "tw_h2": f(m["sleeve_tw"].iloc[h:])["cagr"],
                 "net_h1": f(m["ret_net"].iloc[:h])["cagr"], "net_h2": f(m["ret_net"].iloc[h:])["cagr"],
                 "y2018": float((1 + m["ret_net"][m.index.year == 2018]).prod() - 1),
                 "y2022": float((1 + m["ret_net"][m.index.year == 2022]).prod() - 1), "stops": len(o["stops"])}
    print(name, {k: round(v, 3) for k, v in res[name].items()}, flush=True)
    json.dump(res, open(OUT / "stop_var.json", "w"), ensure_ascii=False, indent=1)
