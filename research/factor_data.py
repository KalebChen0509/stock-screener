"""【2026/11/06】輸出 research/out/factor.pkl（約 5～10 分鐘）。因子掃描資料：每個月、每個市場，所有股票的指標＋下個月報酬。輸出 pickle。"""
import os  # noqa: F401
import sys  # noqa: F401
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _paths import CACHE, OUT, ROOT  # noqa: E402,F401
import pickle
import sys

import numpy as np
import pandas as pd

from screener import prices  # noqa: E402
from screener.backtest import wide  # noqa: E402
from screener.common import load_strategy  # noqa: E402
from screener.engine import px_frame_monthly, ret_n_months, screen_market  # noqa: E402
from screener.loaders import load_infos, load_panels  # noqa: E402
from screener import month_rev as mrev  # noqa: E402

cfg = load_strategy()
monthly = prices.load_monthly(include_delisted=True)
infos = load_infos(include_delisted=True)
panels, _ = load_panels(infos, monthly)
close, adj, vol = wide(monthly, "close"), wide(monthly, "adj_close"), wide(monthly, "volume")
mrw = mrev.wide()
months = [m for m in close.index if pd.Timestamp("2016-09-30") <= m <= pd.Timestamp("2026-09-30")]
KEEP = ["id", "market_cap", "avg_traded_value", "is_financial", "pass_all", "ret_12m", "ep", "earnings_yield", "book_to_price",
        "fcf_yield", "roe", "roe_5y", "gross_margin", "op_margin", "rev_growth_1y", "eps_growth_1y", "revenue_cagr_3y", "sue",
        "rev_yoy_3m", "rev_yoy_12m", "rev_accel", "shareholder_yield", "debt_to_equity", "share_growth", "industry"]
out = []
for i, t in enumerate(months[:-1]):
    t1 = months[i + 1]
    for mkt in ("tw", "us"):
        inf = infos[mkt]
        ym = dict(zip(inf.index, inf["yahoo"]))
        px = px_frame_monthly(close, vol, t, ym)
        r6 = ret_n_months(adj, t, 6, ym)
        r12 = ret_n_months(adj, t, 11, ym, skip=1)
        ctx = {"adj": adj, "close": close, "ymap": ym, "index": cfg["top_down"]["regime_indices"][mkt], "rf": None, "implied": False}
        res = screen_market(panels[mkt], inf, px, r6, t, cfg, mkt, ret12=r12, month_rev=mrw, ctx=ctx)
        a = res["all"]
        if a is None or a.empty:
            continue
        a = a[[c for c in KEEP if c in a.columns]].copy()
        rk = res["ranked"]
        a["value_rank"] = a["id"].map(dict(zip(rk["id"], rk["rank"]))) if len(rk) and "rank" in rk else np.nan
        tk = a["id"].map(ym)
        a["ret_6m"] = a["id"].map(r6)
        # 波動度：近 12 個月月報酬標準差
        win = adj.loc[:t].iloc[-13:]
        sd = win.pct_change().std()
        a["vol_12m"] = tk.map(sd)
        a0, a1 = tk.map(adj.loc[t]), tk.map(adj.loc[t1])
        r = a1 / a0 - 1
        # 下市：本月下市、停止交易很久才下市（多半經營出問題）→ −50%；其他缺值 → 0（跟正式回測一致）
        if "delist_date" in inf:
            dd = a["id"].map(inf["delist_date"])
            lt = a["id"].map(inf["last_trade"]) if "last_trade" in inf else pd.Series(pd.NaT, index=a.index)
            gone = dd.notna() & (pd.to_datetime(dd) <= t1) & r.isna()
            distressed = gone & ((pd.to_datetime(dd) - pd.to_datetime(lt)).dt.days > 20)
            r = r.mask(distressed, -0.5)
        r = r.where(r.abs() <= 3, 0.0).fillna(0.0)
        a["fwd"] = r.values
        a["has_px"] = a0.notna().values
        a["date"], a["market"] = t, mkt
        out.append(a)
    if i % 12 == 0:
        print(t.date(), flush=True)
bench = {}
for b in ("0050.TW", "SPY"):
    s = adj[b].reindex(months)
    bench[b] = (s.shift(-1) / s - 1).iloc[:-1]
pickle.dump({"df": pd.concat(out, ignore_index=True), "bench": bench}, open(OUT / "factor.pkl", "wb"))
print("done", sum(len(x) for x in out))
