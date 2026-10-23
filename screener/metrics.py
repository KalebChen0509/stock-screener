"""由季度財報計算 TTM 與各項比率；再結合價格算估值。"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .prices import future_split_factor

TTM_FIELDS = ["revenue", "gross_profit", "op_income", "nonop", "pretax", "net_income",
              "rd", "cfo", "capex", "cfi", "cff"]


def _div(a, b, pos_den=True):
    a = pd.to_numeric(a, errors="coerce")
    b = pd.to_numeric(b, errors="coerce")
    with np.errstate(divide="ignore", invalid="ignore"):
        r = a / b
    if pos_den:
        r = r.where(b > 0)
    return r.replace([np.inf, -np.inf], np.nan)


def fundamental_panel(fund: pd.DataFrame, splits: pd.DataFrame | None = None,
                      yahoo_map: dict | None = None) -> pd.DataFrame:
    """輸入標準化季資料（多家公司），輸出每家公司每季的 TTM 指標（不含價格）。"""
    df = fund.dropna(subset=["period_end", "available_date"]).copy()
    df = df.sort_values(["market", "id", "period_end"]).drop_duplicates(
        ["market", "id", "period_end"], keep="last")
    df = df.reset_index(drop=True)
    df["capex"] = df["capex"].fillna(0.0)
    df["_key"] = df["market"].astype(str) + ":" + df["id"].astype(str)

    def grp(col):
        return df.groupby("_key", sort=False)[col]

    # 4 季是否連續（第 i 列與第 i-3 列相隔約 9 個月）
    span3 = (df["period_end"] - grp("period_end").shift(3)).dt.days
    ok4 = span3.between(240, 300)
    for f in TTM_FIELDS:
        roll = grp(f).transform(lambda x: x.rolling(4, min_periods=4).sum())
        df[f + "_ttm"] = roll.where(ok4)

    def lag(col, n):
        s = grp(col).shift(n)
        span = (df["period_end"] - grp("period_end").shift(n)).dt.days
        return s.where(span.between(n * 91 - 25, n * 91 + 25))

    df["ni_ttm_lag4"] = lag("net_income_ttm", 4)
    df["ni_ttm_lag8"] = lag("net_income_ttm", 8)
    df["equity_lag4"] = lag("equity", 4)
    for f in ("revenue", "gross_profit", "op_income"):
        df[f"{f}_ttm_lag4"] = lag(f"{f}_ttm", 4)
        df[f"{f}_ttm_lag8"] = lag(f"{f}_ttm", 8)
    df["revenue_ttm_lag12"] = lag("revenue_ttm", 12)
    for f in ("cfo", "cfi", "cff", "capex"):
        df[f"{f}_ttm_lag4"] = lag(f"{f}_ttm", 4)
        df[f"{f}_ttm_lag8"] = lag(f"{f}_ttm", 8)
    # 財報驚喜（SUE）：單季淨利與去年同季的差額 ÷ 過去 8 季這個差額的標準差
    df["_ni_d4"] = df["net_income"] - lag("net_income", 4)
    df["_ni_d4_sd"] = df.groupby("_key", sort=False)["_ni_d4"].transform(
        lambda x: x.shift(1).rolling(8, min_periods=4).std())
    df["n_quarters"] = df.groupby("_key", sort=False).cumcount() + 1

    # 分割調整後股數（換成今天的股數基準）
    if "shares_date" not in df:
        df["shares_date"] = df["period_end"]
    df["shares_date"] = pd.to_datetime(df["shares_date"]).fillna(df["period_end"])
    if splits is not None and yahoo_map is not None and not splits.empty:
        td = pd.DataFrame({"ticker": df["id"].map(yahoo_map), "date": df["shares_date"]},
                          index=df.index)
        df["adj_shares"] = df["shares"] * future_split_factor(splits, td)
    else:
        df["adj_shares"] = df["shares"]
    df["adj_shares_lag4"] = lag("adj_shares", 4)

    ta = df["total_assets"]
    out = df[["market", "id", "period_end", "available_date", "n_quarters"]].copy()
    out["total_assets"] = ta
    out["cash_ratio"] = _div(df["cash"].fillna(0) + df["st_inv"].fillna(0), ta)
    out["intangible_ratio"] = _div(df["goodwill"].fillna(0) + df["intangibles"].fillna(0), ta)
    out["lt_invest_ratio"] = _div(df["lt_inv"].fillna(0), ta)
    out["inventory_ratio"] = _div(df["inventory"].fillna(0), ta)
    out["equity_ratio"] = _div(df["equity"], ta)
    out["current_ratio"] = _div(df["current_assets"], df["current_liab"])
    out["quick_ratio"] = _div(df["current_assets"] - df["inventory"].fillna(0), df["current_liab"])
    out["revenue"] = df["revenue_ttm"]
    out["gross_margin"] = _div(df["gross_profit_ttm"], df["revenue_ttm"])
    out["op_margin"] = _div(df["op_income_ttm"], df["revenue_ttm"])
    out["op_to_gross"] = _div(df["op_income_ttm"], df["gross_profit_ttm"])
    # 近 3 年（每段近四季）毛利率、營益率：取最低值（每年都要達標）與平均值（景氣循環股用）
    gms = [_div(df[f"gross_profit_ttm{s}"], df[f"revenue_ttm{s}"]) for s in ("", "_lag4", "_lag8")]
    oms = [_div(df[f"op_income_ttm{s}"], df[f"revenue_ttm{s}"]) for s in ("", "_lag4", "_lag8")]
    g3, o3 = pd.concat(gms, axis=1), pd.concat(oms, axis=1)
    full = g3.notna().all(axis=1)
    out["gross_margin_min3y"] = g3.min(axis=1).where(full)
    out["gross_margin_avg3y"] = g3.mean(axis=1).where(full)
    full = o3.notna().all(axis=1)
    out["op_margin_min3y"] = o3.min(axis=1).where(full)
    out["op_margin_avg3y"] = o3.mean(axis=1).where(full)
    out["rd_to_revenue"] = _div(df["rd_ttm"].fillna(0), df["revenue_ttm"])
    r12 = df["revenue_ttm_lag12"]
    out["revenue_cagr_3y"] = (_div(df["revenue_ttm"], r12) ** (1 / 3) - 1).where(r12 > 0)
    out["nonop_share"] = _div(df["nonop_ttm"], df["pretax_ttm"])
    out["net_income"] = df["net_income_ttm"]
    out["op_income"] = df["op_income_ttm"]
    out["cfo"] = df["cfo_ttm"]
    out["fcf"] = df["cfo_ttm"] - df["capex_ttm"]
    out["cff"] = df["cff_ttm"]
    out["cfo_to_ni"] = _div(out["cfo"], out["net_income"])
    out["fcf_to_ni"] = _div(out["fcf"], out["net_income"])
    out["roe"] = _div(df["net_income_ttm"], (df["equity"] + df["equity_lag4"].fillna(df["equity"])) / 2)
    out["profitable_3y"] = (df["net_income_ttm"] > 0) & (df["ni_ttm_lag4"] > 0) & (df["ni_ttm_lag8"] > 0)
    # 現金流量與公司類型（近 3 年合計）：甲＝營+投−籌−、乙＝營+投−籌+，其餘不要碰
    c3 = df["cfo_ttm"] + df["cfo_ttm_lag4"] + df["cfo_ttm_lag8"]
    i3 = df["cfi_ttm"] + df["cfi_ttm_lag4"] + df["cfi_ttm_lag8"]
    f3 = df["cff_ttm"] + df["cff_ttm_lag4"] + df["cff_ttm_lag8"]
    out["cfo_3y"], out["cfi_3y"], out["cff_3y"] = c3, i3, f3
    out["cfo_positive_3y"] = (df["cfo_ttm"] > 0) & (df["cfo_ttm_lag4"] > 0) & (df["cfo_ttm_lag8"] > 0)
    t = np.where(c3.isna() | i3.isna() | f3.isna(), None,
         np.where((c3 > 0) & (i3 < 0) & (f3 < 0), "甲",
         np.where((c3 > 0) & (i3 < 0), "乙",
         np.where((c3 <= 0) & (i3 < 0) & (f3 > 0), "丙",
         np.where((c3 <= 0) & (i3 >= 0) & (f3 > 0), "戊",
         np.where(c3 <= 0, "丁", "其他"))))))
    out["cf_type"] = t
    out["cf_type_ok"] = pd.Series(t, index=df.index).isin(["甲", "乙"])
    out["share_growth"] = _div(df["adj_shares"], df["adj_shares_lag4"]) - 1
    out["adj_shares"] = df["adj_shares"]
    out["equity"] = df["equity"]
    out["cash_total"] = df["cash"].fillna(0) + df["st_inv"].fillna(0)
    out["debt"] = df["debt"].fillna(0)
    out["equity_lag4"] = df["equity_lag4"]
    capex3 = df["capex_ttm"] + df["capex_ttm_lag4"] + df["capex_ttm_lag8"]
    out["fcf_3y_avg"] = (c3 - capex3) / 3
    sd = df["_ni_d4_sd"]
    out["sue"] = (df["_ni_d4"] / sd.where(sd > 0)).clip(-10, 10)
    return out


def asof_fundamentals(panel: pd.DataFrame, date: pd.Timestamp) -> pd.DataFrame:
    """在 date 當下「已公布」的最新一季資料。"""
    p = panel[panel["available_date"] <= date]
    if p.empty:
        return p
    idx = p.groupby(["market", "id"])["period_end"].idxmax()
    res = p.loc[idx]
    # 太舊（超過 9 個月沒新財報）視為無效
    return res[(date - res["period_end"]).dt.days <= 270]


def add_valuation(f: pd.DataFrame, px: pd.DataFrame) -> pd.DataFrame:
    """px：index=id，欄位 close（分割還原）、avg_traded_value。"""
    df = f.join(px, on="id", how="left")
    df["market_cap"] = df["close"] * df["adj_shares"]
    df["pe"] = _div(df["market_cap"], df["net_income"])
    df.loc[df["net_income"] <= 0, "pe"] = np.inf
    df["pb"] = _div(df["market_cap"], df["equity"])
    df.loc[df["equity"] <= 0, "pb"] = np.inf
    ev = df["market_cap"] + df["debt"] - df["cash_total"]
    df["ev"] = ev
    df["earnings_yield"] = _div(df["op_income"], ev)
    df["fcf_yield"] = _div(df["fcf"], df["market_cap"])
    df["book_to_price"] = _div(df["equity"], df["market_cap"])
    return df
