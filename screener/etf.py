"""ETF 指標與篩選。

回測時只用「當時可得」的資料：成立年數、流動性、近 12 個月殖利率、追蹤波動；
費用率、規模、前十大持股、估值使用目前值（ETF 這些特性變動慢，影響有限）。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .common import log
from .screen import apply_hard_filters


def enrich_live(etfs: list[dict], fx_usdtwd: float) -> pd.DataFrame:
    """用 yfinance 補上即時的費用率、規模、前十大持股、估值。"""
    import yfinance as yf
    rows = []
    for e in etfs:
        r = dict(e)
        r["aum_usd"] = (e.get("aum_twd_approx") or np.nan) / fx_usdtwd if e.get("aum_twd_approx") else np.nan
        r["top10_weight"] = np.nan
        r["pe"] = np.nan
        r["pb"] = np.nan
        try:
            t = yf.Ticker(e["ticker"])
            info = t.info or {}
            ta = info.get("totalAssets")
            if ta:
                r["aum_usd"] = ta / (fx_usdtwd if e["market"] == "tw" else 1.0)
            er = info.get("netExpenseRatio") or info.get("annualReportExpenseRatio")
            if er and e["market"] == "us":
                r["expense_ratio"] = er / 100 if er > 0.05 else er
            try:
                fd = t.funds_data
                th = fd.top_holdings
                if th is not None and len(th):
                    col = "Holding Percent" if "Holding Percent" in th.columns else th.columns[-1]
                    r["top10_weight"] = float(th[col].head(10).sum())
                eh = fd.equity_holdings
                if eh is not None and len(eh):
                    col = eh.columns[0]
                    if "Price/Earnings" in eh.index:
                        r["pe"] = float(eh.loc["Price/Earnings", col])
                    if "Price/Book" in eh.index:
                        r["pb"] = float(eh.loc["Price/Book", col])
            except Exception:  # noqa
                pass
        except Exception as ex:  # noqa
            log.warning("ETF info %s: %s", e["ticker"], ex)
        rows.append(r)
    return pd.DataFrame(rows)


def static_frame(etfs: list[dict], fx_usdtwd: float) -> pd.DataFrame:
    df = pd.DataFrame(etfs)
    df["aum_usd"] = df.get("aum_twd_approx", pd.Series(np.nan, index=df.index)) / fx_usdtwd
    df["top10_weight"] = np.nan
    df["pe"] = np.nan
    df["pb"] = np.nan
    return df


def point_in_time_metrics(etf_df: pd.DataFrame, prices: pd.DataFrame, fx: pd.Series,
                          date: pd.Timestamp) -> pd.DataFrame:
    """prices：月資料長格式（ticker, month, close, adj_close, volume）。"""
    df = etf_df.copy()
    p = prices[prices["month"] <= date]
    fx_now = fx[fx.index <= date].iloc[-1] if (fx.index <= date).any() else np.nan
    ages, liq, dy, tv = [], [], [], []
    for _, r in df.iterrows():
        s = p[p["ticker"] == r["ticker"]].sort_values("month")
        if s.empty:
            ages.append(0); liq.append(np.nan); dy.append(np.nan); tv.append(np.nan)
            continue
        ages.append((date - s["month"].iloc[0]).days / 365.25)
        last3 = s.tail(3)
        val = (last3["volume"] * last3["close"] / 21).mean()
        if r["market"] == "tw":
            val = val / fx_now
        liq.append(val)
        last13 = s.tail(13)
        if len(last13) >= 13:
            tr = last13["adj_close"].iloc[-1] / last13["adj_close"].iloc[0]
            pr = last13["close"].iloc[-1] / last13["close"].iloc[0]
            dy.append(max(tr - pr, 0) / pr if pr > 0 else np.nan)
        else:
            dy.append(np.nan)
        idx = r.get("tracking_index")
        if isinstance(idx, str) and idx:
            si = p[p["ticker"] == idx].set_index("month")["close"].pct_change()
            se = s.set_index("month")["close"].pct_change()
            diff = (se - si).dropna().tail(36)
            tv.append(diff.std() * np.sqrt(12) if len(diff) >= 12 else np.nan)
        else:
            tv.append(np.nan)
    df["age_years"] = ages
    df["avg_traded_value_usd"] = liq
    df["div_yield_ttm"] = dy
    df["tracking_vol"] = tv
    ey = 1 / df["pe"].where(df["pe"] > 0)
    bp = 1 / df["pb"].where(df["pb"] > 0)
    parts = [df["div_yield_ttm"].rank(pct=True)]
    if ey.notna().sum() >= 3:
        parts.append(ey.rank(pct=True))
    if bp.notna().sum() >= 3:
        parts.append(bp.rank(pct=True))
    df["value_tilt"] = pd.concat(parts, axis=1).mean(axis=1)
    return df


def screen_and_rank(df: pd.DataFrame, cfg_etf: dict) -> pd.DataFrame:
    filters = []
    for f in cfg_etf["hard_filters"]:
        f = dict(f)
        filters.append(f)
    out = apply_hard_filters(df, filters, market="etf", industry_col="market")
    # 沒有資料（NaN）的前十大持股、規模，視為通過（台股 ETF 查不到時）
    for fid, m in (("top10", "top10_weight"), ("aum", "aum_usd")):
        col = f"f_{fid}"
        if col in out:
            out[col] = out[col] | out[m].isna()
    fcols = [c for c in out.columns if c.startswith("f_")]
    out["pass_all"] = out[fcols].all(axis=1)
    out["fail_count"] = (~out[fcols]).sum(axis=1)
    names = np.array([c[2:] for c in fcols])
    out["failed"] = [list(names[~row]) for row in out[fcols].values]
    score = pd.Series(0.0, index=out.index)
    tot = 0.0
    passed = out["pass_all"]
    for m, w in cfg_etf["ranking"].items():
        if m not in out:
            continue
        s = out[m].where(passed)
        pct = s.rank(pct=True, ascending=w > 0).fillna(0.5)
        out[f"pct_{m}"] = pct * 100
        score += abs(w) * pct * 100
        tot += abs(w)
    out["score"] = (score / tot).where(passed)
    return out.sort_values(["pass_all", "score"], ascending=[False, False])
