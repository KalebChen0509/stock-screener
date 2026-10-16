"""Hard filters、由上而下產業評分、綜合排名。"""
from __future__ import annotations

import numpy as np
import pandas as pd

OPS = {
    ">=": lambda s, v: s >= v,
    "<=": lambda s, v: s <= v,
    ">": lambda s, v: s > v,
    "<": lambda s, v: s < v,
    "==": lambda s, v: s == v,
}


def _threshold(v, market):
    if isinstance(v, dict):
        return v.get(market)
    return v


def apply_hard_filters(df: pd.DataFrame, filters: list[dict], market: str,
                       industry_col: str = "industry") -> pd.DataFrame:
    """在 df 加上 f_<id>（是否通過）、pass_all、fail_count、failed（未通過清單）。"""
    df = df.copy()
    fcols = []
    for f in filters:
        if not f.get("enabled", True):
            continue
        col = f"f_{f['id']}"
        m = f["metric"]
        s = df[m] if m in df else pd.Series(np.nan, index=df.index)
        if f["op"] == "<=peer":
            k = f.get("peer_k_std", 1.0)
            min_g = f.get("peer_min_group", 5)
            grp = df.groupby(industry_col)[m]
            med, std, cnt = grp.transform("median"), grp.transform("std"), grp.transform("count")
            lim = med + k * std.fillna(0)
            all_lim = s.median() + k * s.std()
            lim = lim.where(cnt >= min_g, all_lim)
            df[f"peer_limit_{m}"] = lim
            ok = s <= lim
        else:
            v = _threshold(f["value"], market)
            if v is None:
                continue
            if isinstance(v, bool):
                ok = s.map(lambda x: (bool(x) == v) if pd.notna(x) else False)
            else:
                ok = OPS[f["op"]](pd.to_numeric(s, errors="coerce"), v)
        df[col] = ok.fillna(False).astype(bool)
        fcols.append(col)
    if fcols:
        passed = df[fcols]
        df["pass_all"] = passed.all(axis=1)
        df["fail_count"] = (~passed).sum(axis=1)
        names = np.array([c[2:] for c in fcols])
        df["failed"] = [list(names[~row]) for row in passed.values]
    else:
        df["pass_all"] = True
        df["fail_count"] = 0
        df["failed"] = [[] for _ in range(len(df))]
    return df


# ----------------------------------------------------------------------------
def sector_scores(universe_now: pd.DataFrame, ret_6m: pd.Series, cfg_td: dict,
                  industry_col: str = "industry") -> pd.DataFrame:
    """產業評分：相對強弱（產業中位數 6 個月報酬）＋估值（產業中位數盈餘殖利率）。"""
    d = universe_now[[industry_col, "id", "earnings_yield"]].copy()
    d["ret6"] = d["id"].map(ret_6m)
    g = d.groupby(industry_col)
    s = pd.DataFrame({"n": g.size(), "mom": g["ret6"].median(), "val": g["earnings_yield"].median()})
    s = s[s["n"] >= 3]
    if s.empty:
        return pd.DataFrame(columns=["n", "mom", "val", "score"])
    w = cfg_td.get("sector_weights", {"momentum": 0.5, "valuation": 0.5})
    s["mom_pct"] = s["mom"].rank(pct=True)
    s["val_pct"] = s["val"].rank(pct=True)
    s["score"] = 100 * (w["momentum"] * s["mom_pct"].fillna(0.5) + w["valuation"] * s["val_pct"].fillna(0.5))
    return s.sort_values("score", ascending=False)


def market_regime(index_monthly: pd.Series, date: pd.Timestamp, ma: int = 10) -> dict:
    s = index_monthly[index_monthly.index <= date].dropna()
    if len(s) < ma:
        return {"bull": None, "last": None, "ma": None}
    m = s.iloc[-ma:].mean()
    return {"bull": bool(s.iloc[-1] >= m), "last": float(s.iloc[-1]), "ma": float(m)}


# ----------------------------------------------------------------------------
def flatten_weights(rank_cfg: dict) -> dict:
    w = {}
    for grp in rank_cfg["bottom_up"].values():
        w.update(grp)
    return w


def rank_stocks(df: pd.DataFrame, rank_cfg: dict, td_cfg: dict,
                sector_tbl: pd.DataFrame | None, industry_col: str = "industry") -> pd.DataFrame:
    """對 df（通常是通過 hard filters 的股票）計算綜合分數。百分位在 df 內部計算。"""
    df = df.copy()
    if df.empty:
        df["score"] = []
        return df
    w = flatten_weights(rank_cfg)
    total = 0.0
    score = pd.Series(0.0, index=df.index)
    for m, wt in w.items():
        if m not in df:
            continue
        pct = df[m].replace([np.inf, -np.inf], np.nan).rank(pct=True).fillna(0.5)
        df[f"pct_{m}"] = pct * 100
        score += wt * pct * 100
        total += wt
    td_w = rank_cfg.get("top_down", 0.0)
    if sector_tbl is not None and len(sector_tbl):
        df["top_down_score"] = df[industry_col].map(sector_tbl["score"]).fillna(50.0)
    else:
        df["top_down_score"] = 50.0
    score += td_w * df["top_down_score"]
    total += td_w
    score = score / total if total else score
    b = rank_cfg.get("bonuses", {})
    df["bonus"] = 0.0
    if "cf_type" in df:
        df.loc[df["cf_type"] == "甲", "bonus"] += b.get("cf_type_a", 0)
    if "cfo_to_ni" in df:
        df.loc[df["cfo_to_ni"] >= 1.0, "bonus"] += b.get("cfo_to_ni_100", 0)
    df["bottom_up_score"] = (score * total - td_w * df["top_down_score"]) / max(total - td_w, 1e-9)
    df["score"] = score + df["bonus"]
    cc = td_cfg.get("cross_check", {"bottom_up_top": 1 / 3, "sector_top": 0.5})
    bu_rank = df["bottom_up_score"].rank(pct=True, ascending=False)
    sec_ok = df["top_down_score"] >= 100 * (1 - cc["sector_top"])
    df["cross_ok"] = (bu_rank <= cc["bottom_up_top"]) & sec_ok
    return df.sort_values("score", ascending=False)


def select_portfolio(ranked: pd.DataFrame, max_n: int) -> list[str]:
    return list(ranked.head(max_n)["id"])
