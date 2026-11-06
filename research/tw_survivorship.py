"""【2026/11/05】實驗：台股回測「含下市公司」vs「不含下市公司」，量測倖存者偏差。結果寫到 surv_result.json。"""
import os  # noqa: F401
import sys  # noqa: F401
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _paths import CACHE, OUT, ROOT  # noqa: E402,F401
import json
import os
import sys

import pandas as pd  # noqa: E402

from screener import prices  # noqa: E402
from screener.backtest import perf_stats, run_backtest  # noqa: E402
from screener.common import apply_patch, load_etf_universe, load_strategy, log  # noqa: E402
from screener.etf import static_frame  # noqa: E402
from screener.loaders import load_infos, load_panels  # noqa: E402

cfg = load_strategy()
VARIANTS = {
    "目前版（價值）": {},
    "成長動能（單檔上限 10%）": {"portfolio": {"stock_style": "growth", "weighting": "cap_capped", "max_weight": 0.10},
                          "risk": {"stop_loss": {"basis": "vol", "proceeds": "redistribute"}}},
}
try:
    from screener import month_rev as mrev
    mrw = mrev.wide()
except Exception:  # noqa
    mrw = None
res = {}
for incl in (True, False):
    monthly = prices.load_monthly(include_delisted=incl)
    infos = load_infos(include_delisted=incl)
    panels, _ = load_panels(infos, monthly)
    fx_now = monthly[monthly["ticker"] == "TWD=X"].sort_values("month")["close"].iloc[-1]
    etf_static = static_frame(load_etf_universe(), fx_now)
    sc = {}
    for name, patch in VARIANTS.items():
        c = apply_patch(cfg, patch) if patch else cfg
        o = run_backtest(c, panels, infos, monthly, etf_static, screen_cache=sc, month_rev=mrw)
        m = o["monthly"]
        r = {k: perf_stats(m[k], 0.0) for k in ("sleeve_tw", "sleeve_us", "ret_net") if k in m}
        r = {k: {"cagr": v["cagr"], "mdd": v["max_drawdown"]} for k, v in r.items()}
        res[f"{name}｜{'含下市' if incl else '不含下市'}"] = r
        log.info("RESULT %s %s %s", name, incl, r)
        json.dump(res, open(OUT / "tw_survivorship.json", "w"), ensure_ascii=False, indent=1)
print(json.dumps(res, ensure_ascii=False, indent=1))
