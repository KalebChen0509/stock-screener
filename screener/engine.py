"""單一時點的完整篩選（回測與每日更新共用）。"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .metrics import add_valuation, asof_fundamentals
from .screen import apply_hard_filters, rank_stocks, sector_scores


def screen_market(panel: pd.DataFrame, info: pd.DataFrame, px: pd.DataFrame,
                  ret6: pd.Series, date: pd.Timestamp, cfg: dict, market: str) -> dict:
    """
    panel：該市場的 fundamental_panel
    info：index=id，欄位 name, industry, is_financial, yahoo
    px：index=id，欄位 close, avg_traded_value
    ret6：index=id 的 6 個月報酬
    回傳 {"all": 全部股票指標+過濾結果, "ranked": 通過者排名, "sectors": 產業分數}
    """
    f = asof_fundamentals(panel[panel["market"] == market], date)
    if f.empty:
        return {"all": pd.DataFrame(), "ranked": pd.DataFrame(), "sectors": pd.DataFrame()}
    f = f.join(info[["name", "industry", "is_financial"]], on="id", how="inner")
    df = add_valuation(f, px)
    df = df[df["close"].notna() & df["market_cap"].notna()]
    df = apply_hard_filters(df, cfg["hard_filters"], market)
    sectors = sector_scores(df[~df["is_financial"].astype(bool)], ret6, cfg["top_down"])
    passed = df[df["pass_all"]]
    excl = cfg["top_down"].get("exclude_bottom_sectors_pct", 0) or 0
    if excl > 0 and len(sectors):
        cutoff = sectors["score"].quantile(excl)
        bad = set(sectors.index[sectors["score"] < cutoff])
        passed = passed[~passed["industry"].isin(bad)]
    ranked = rank_stocks(passed, cfg["ranking"], cfg["top_down"], sectors)
    return {"all": df, "ranked": ranked, "sectors": sectors}


def px_frame_monthly(close: pd.DataFrame, vol: pd.DataFrame, date: pd.Timestamp,
                     id_to_yahoo: dict) -> pd.DataFrame:
    """由月資料寬表取得某月的收盤價與近 3 月日均成交值。"""
    if date not in close.index:
        return pd.DataFrame(columns=["close", "avg_traded_value"])
    loc = close.index.get_loc(date)
    c = close.iloc[loc]
    tv = (vol.iloc[max(0, loc - 2):loc + 1] * close.iloc[max(0, loc - 2):loc + 1] / 21).mean()
    ids = pd.Series(id_to_yahoo)
    out = pd.DataFrame({"close": ids.map(c), "avg_traded_value": ids.map(tv)})
    out.index.name = "id"
    return out


def ret_n_months(adj: pd.DataFrame, date: pd.Timestamp, n: int, id_to_yahoo: dict) -> pd.Series:
    if date not in adj.index:
        return pd.Series(dtype=float)
    loc = adj.index.get_loc(date)
    if loc < n:
        return pd.Series(dtype=float)
    r = adj.iloc[loc] / adj.iloc[loc - n] - 1
    return pd.Series(id_to_yahoo).map(r)
