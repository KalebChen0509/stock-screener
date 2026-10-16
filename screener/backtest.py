"""月調倉回測：每月底依當時可得資料選股，次月持有；等權重；台幣計價。

兩種情境同時模擬：
  gross：不扣任何費用與稅
  net  ：扣手續費、證交稅、換匯價差、美股股利預扣 30%
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .common import log
from .engine import px_frame_monthly, ret_n_months, screen_market, select_with_buffer
from .etf import point_in_time_metrics, screen_and_rank
from .screen import market_regime


def wide(prices: pd.DataFrame, col: str) -> pd.DataFrame:
    return prices.pivot_table(index="month", columns="ticker", values=col, aggfunc="last").sort_index()


def _asset_class(ticker: str, kind: str) -> str:
    if kind == "etf":
        return "tw_etf" if ticker.endswith((".TW", ".TWO")) else "us"
    return "tw_stock" if ticker.endswith((".TW", ".TWO")) else "us"


def run_backtest(cfg: dict, panels: dict, infos: dict, prices: pd.DataFrame,
                 etf_static: pd.DataFrame, start: str | None = None, end: str | None = None,
                 progress=None, events: dict | None = None) -> dict:
    close, adj, vol = wide(prices, "close"), wide(prices, "adj_close"), wide(prices, "volume")
    fx = close["TWD=X"].dropna() if "TWD=X" in close else pd.Series(dtype=float)
    months = close.index
    bt = cfg["backtest"]
    start = pd.Timestamp(start or bt["start"]) + pd.offsets.MonthEnd(0)
    # 最後一個「完整」月份：yfinance 當月資料不完整，排除
    last_full = (pd.Timestamp.today().normalize() - pd.offsets.MonthEnd(1))
    end_ts = last_full if (end in (None, "latest") and bt.get("end", "latest") == "latest") \
        else pd.Timestamp(end or bt["end"]) + pd.offsets.MonthEnd(0)
    rebal = [m for m in months if start - pd.offsets.MonthEnd(1) <= m < end_ts]
    pw = cfg["portfolio"]["weights"]
    max_n = cfg["portfolio"]["max_holdings_per_stock_sleeve"]
    k_etf = cfg["portfolio"]["etf_holdings"]
    costs = cfg["costs"]
    td = cfg["top_down"]

    y_maps = {m: dict(zip(infos[m].index, infos[m]["yahoo"])) for m in infos}
    etf_tk = set(etf_static["ticker"]) | set(etf_static.get("tracking_index", pd.Series(dtype=str)).dropna())
    etf_prices = prices[prices["ticker"].isin(etf_tk)]
    nav = {"gross": 1.0, "net": 1.0}
    w_prev = {"gross": {}, "net": {}}
    records, holdings_log = [], []
    total_cost = 0.0
    total_wht = 0.0
    buf = cfg.get("buffer", {})
    mult = buf.get("rank_multiple", 2)
    held = {"tw": [], "us": [], "etf": []}

    for i, t in enumerate(rebal[:-1]):
        t1 = rebal[i + 1]
        # ---------- 選股 ----------
        targets = {}
        picks = {}
        for mkt, sleeve in (("tw", "tw_stock"), ("us", "us_stock")):
            if mkt not in panels or panels[mkt].empty:
                continue
            ym = y_maps[mkt]
            px = px_frame_monthly(close, vol, t, ym)
            r6 = ret_n_months(adj, t, td.get("sector_momentum_months", 6), ym)
            r12 = ret_n_months(adj, t, 11, ym, skip=1)
            res = screen_market(panels[mkt], infos[mkt], px, r6, t, cfg, mkt, ret12=r12,
                                events=(events or {}).get(mkt))
            ids = select_with_buffer(res["ranked"], held[mkt], max_n, mult)
            held[mkt] = ids
            weight = pw[sleeve]
            if td.get("regime_defensive"):
                idx_t = td["regime_indices"][mkt]
                if idx_t in close:
                    rg = market_regime(close[idx_t], t, td.get("regime_ma_months", 10))
                    if rg["bull"] is False:
                        weight *= 0.5
            picks[mkt] = ids
            for sid in ids:
                targets[("stock", ym[sid])] = weight / len(ids)
        em = point_in_time_metrics(etf_static, etf_prices, fx, t)
        er = screen_and_rank(em, cfg["etf"])
        er = er[er["pass_all"]].copy()
        er["rank"] = np.arange(1, len(er) + 1)
        er["id"] = er["ticker"]
        etf_ids = select_with_buffer(er, held["etf"], k_etf, mult)
        held["etf"] = etf_ids
        picks["etf"] = etf_ids
        for tk in etf_ids:
            targets[("etf", tk)] = pw["etf"] / len(etf_ids)

        # ---------- 持有一個月的報酬 ----------
        fx0, fx1 = fx.get(t, np.nan), fx.get(t1, np.nan)
        fx_r = fx1 / fx0 if fx0 and fx1 and np.isfinite(fx0) and np.isfinite(fx1) else 1.0
        rec = {"month": t1}
        for scen in ("gross", "net"):
            prev = w_prev[scen]
            # 交易成本：依權重變化
            cost = 0.0
            if scen == "net":
                keys = set(prev) | set(targets)
                for k in keys:
                    d = targets.get(k, 0.0) - prev.get(k, 0.0)
                    cls = _asset_class(k[1], k[0])
                    c = costs[cls]
                    rate = c["buy"] if d > 0 else c["sell"]
                    if cls == "us":
                        rate += costs.get("fx_spread", 0.0)
                    cost += abs(d) * rate
            port_r = 0.0
            sleeve_r = {"tw": [0.0, 0.0], "us": [0.0, 0.0], "etf": [0.0, 0.0]}
            new_w = {}
            wht = 0.0
            for k, w in targets.items():
                tk = k[1]
                a0, a1 = adj.at[t, tk] if tk in adj else np.nan, adj.at[t1, tk] if tk in adj else np.nan
                c0, c1 = close.at[t, tk] if tk in close else np.nan, close.at[t1, tk] if tk in close else np.nan
                if not (np.isfinite(a0) and np.isfinite(a1)) or a0 <= 0:
                    r_loc, div = 0.0, 0.0
                else:
                    r_loc = a1 / a0 - 1
                    pr = c1 / c0 - 1 if (np.isfinite(c0) and np.isfinite(c1) and c0 > 0) else r_loc
                    div = max(r_loc - pr, 0.0)
                    if abs(r_loc) > 3:  # 明顯錯誤的報價
                        r_loc, div = 0.0, 0.0
                us = not tk.endswith((".TW", ".TWO"))
                if scen == "net" and us:
                    r_loc -= costs.get("us_dividend_withholding", 0.0) * div
                    wht += w * costs.get("us_dividend_withholding", 0.0) * div
                r = (1 + r_loc) * (fx_r if us else 1.0) - 1
                port_r += w * r
                sl = "etf" if k[0] == "etf" else ("us" if us else "tw")
                sleeve_r[sl][0] += w * r
                sleeve_r[sl][1] += w
                new_w[k] = w * (1 + r)
            cash_w = max(0.0, 1.0 - sum(targets.values()))
            gross_val = port_r + 1.0  # 現金報酬 0
            nav_before = nav[scen] * (1 - cost)
            nav[scen] = nav_before * gross_val
            tot = sum(new_w.values()) + cash_w
            w_prev[scen] = {k: v / tot for k, v in new_w.items()} if tot > 0 else {}
            rec[f"ret_{scen}"] = (1 - cost) * gross_val - 1
            rec[f"nav_{scen}"] = nav[scen]
            if scen == "gross":
                for sl, (a, b) in sleeve_r.items():
                    rec[f"sleeve_{sl}"] = a / b if b > 0 else 0.0
            if scen == "net":
                rec["cost"] = cost
                rec["withholding"] = wht
                total_cost += cost
                total_wht += wht
        rec["n_tw"] = len(picks.get("tw", []))
        rec["n_us"] = len(picks.get("us", []))
        rec["n_etf"] = len(picks.get("etf", []))
        rec["cash_weight"] = max(0.0, 1.0 - sum(targets.values()))
        records.append(rec)
        holdings_log.append({"month": t, "tw": picks.get("tw", []), "us": picks.get("us", []),
                             "etf": picks.get("etf", [])})
        if progress:
            progress(i + 1, len(rebal) - 1)
        if (i + 1) % 12 == 0:
            log.info("backtest %s nav gross %.3f net %.3f (tw %d us %d etf %d)", t1.date(),
                     nav["gross"], nav["net"], rec["n_tw"], rec["n_us"], rec["n_etf"])

    res = pd.DataFrame(records).set_index("month")
    # ---------- 基準 ----------
    bench = {}
    for b in bt.get("benchmarks", []):
        if b not in adj:
            continue
        s = adj[b].reindex([rebal[0]] + list(res.index))
        r = s.pct_change().iloc[1:]
        if not b.endswith((".TW", ".TWO")):
            f = fx.reindex([rebal[0]] + list(res.index))
            r = (1 + r) * (f / f.shift(1)).iloc[1:] - 1
        bench[b] = r.fillna(0.0)
    if {"0050.TW", "SPY", "VT"} <= set(bench):
        bench["混合基準(20/20/60)"] = 0.2 * bench["0050.TW"] + 0.2 * bench["SPY"] + 0.6 * bench["VT"]
    for b, r in bench.items():
        res[f"bench_{b}"] = r
    res.attrs["total_cost"] = total_cost
    res.attrs["total_withholding"] = total_wht
    return {"monthly": res, "holdings": holdings_log}


def perf_stats(r: pd.Series, rf: float = 0.0) -> dict:
    r = r.dropna()
    if r.empty:
        return {}
    nav = (1 + r).cumprod()
    years = len(r) / 12
    cagr = nav.iloc[-1] ** (1 / years) - 1 if years > 0 else np.nan
    vol = r.std() * np.sqrt(12)
    sharpe = (cagr - rf) / vol if vol > 0 else np.nan
    dd = nav / nav.cummax() - 1
    yearly = (1 + r).groupby(r.index.year).prod() - 1
    return {"total_return": float(nav.iloc[-1] - 1), "cagr": float(cagr), "vol": float(vol),
            "sharpe": float(sharpe), "max_drawdown": float(dd.min()),
            "best_month": float(r.max()), "worst_month": float(r.min()),
            "yearly": {int(k): float(v) for k, v in yearly.items()}, "months": int(len(r))}
