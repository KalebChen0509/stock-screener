"""由季度財報計算 TTM 與各項比率；再結合價格算估值。"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .prices import future_split_factor

TTM_FIELDS = ["revenue", "gross_profit", "op_income", "nonop", "pretax", "net_income",
              "rd", "cfo", "capex", "cfi", "cff"]
# 額外欄位（美股才有；台股為空值）
EXTRA_FIELDS = ["sbc", "interest", "da", "div_paid", "buyback"]


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
    for f in EXTRA_FIELDS:
        if f not in df:
            df[f] = np.nan
        df[f] = pd.to_numeric(df[f], errors="coerce")
    df["_key"] = df["market"].astype(str) + ":" + df["id"].astype(str)

    def grp(col):
        return df.groupby("_key", sort=False)[col]

    # 4 季是否連續（第 i 列與第 i-3 列相隔約 9 個月）
    span3 = (df["period_end"] - grp("period_end").shift(3)).dt.days
    ok4 = span3.between(240, 300)
    for f in TTM_FIELDS + EXTRA_FIELDS:
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
    for f in ("cfo", "cfi", "cff", "capex", "sbc"):
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
    # 近 5 年平均 ROE（每年取一個近四季 ROE，至少 3 年）：排名用，避免單一好年份衝高
    df["_roe"] = out["roe"]
    roes = pd.concat([df["_roe"]] + [lag("_roe", n) for n in (4, 8, 12, 16)], axis=1)
    out["roe_5y"] = roes.mean(axis=1).where(roes.notna().sum(axis=1) >= 3)
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
    # 成長選股用：營收、每股盈餘近一年成長率（去年同期為負時不計算）；財務槓桿只看「借款」，不含應付帳款
    out["rev_growth_1y"] = (_div(df["revenue_ttm"], df["revenue_ttm_lag4"]) - 1).clip(-0.9, 5)
    eps, eps_l4 = _div(df["net_income_ttm"], df["adj_shares"]), _div(df["ni_ttm_lag4"], df["adj_shares_lag4"])
    out["eps_growth_1y"] = (_div(eps, eps_l4) - 1).clip(-0.9, 5)
    out["debt_to_equity"] = _div(df["debt"].fillna(0), df["equity"])
    out["adj_shares"] = df["adj_shares"]
    out["equity"] = df["equity"]
    out["cash_total"] = df["cash"].fillna(0) + df["st_inv"].fillna(0)
    out["debt"] = df["debt"].fillna(0)
    out["equity_lag4"] = df["equity_lag4"]
    capex3 = df["capex_ttm"] + df["capex_ttm_lag4"] + df["capex_ttm_lag8"]
    out["fcf_3y_avg"] = (c3 - capex3) / 3
    sd = df["_ni_d4_sd"]
    out["sue"] = (df["_ni_d4"] / sd.where(sd > 0)).clip(-10, 10)
    # 額外欄位（美股）：股票酬勞、利息保障倍數、EBITDA、股東回饋
    out["sbc"] = df["sbc_ttm"]
    sbc3 = (df["sbc_ttm"].fillna(0) + df["sbc_ttm_lag4"].fillna(0) + df["sbc_ttm_lag8"].fillna(0)) / 3
    out["fcf_after_sbc"] = out["fcf"] - df["sbc_ttm"].fillna(0)
    out["fcf_3y_avg_adj"] = out["fcf_3y_avg"] - sbc3          # 美股估值用：自由現金流再扣股票酬勞
    out["interest"] = df["interest_ttm"]
    out["int_cover"] = _div(df["op_income_ttm"], df["interest_ttm"])
    out["ebitda"] = df["op_income_ttm"] + df["da_ttm"]
    out["payout_cash"] = df["div_paid_ttm"].fillna(0) + df["buyback_ttm"].fillna(0)
    out.loc[df["div_paid_ttm"].isna() & df["buyback_ttm"].isna(), "payout_cash"] = np.nan
    # 歷史股價淨值比用：每股淨值（分割還原）
    out["bvps"] = _div(df["equity"], df["adj_shares"])
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
    df["ep"] = _div(df["net_income"], df["market_cap"])                 # 本益比倒數（金融股比便宜用）
    if "payout_cash" in df:
        df["shareholder_yield"] = _div(df["payout_cash"], df["market_cap"])   # 股利＋買回 ÷ 市值（美股）
    if "ebitda" in df:
        df["ev_ebitda"] = _div(ev, df["ebitda"])
    return df


def history_metrics(panel: pd.DataFrame, date: pd.Timestamp, close: pd.DataFrame | pd.Series | None = None,
                    ymap: dict | None = None, pb_now: pd.Series | None = None, years: int = 10) -> pd.DataFrame:
    """景氣循環用的長期指標（只用 date 當下已公布的財報與當時的股價）。

    roe_avg5y / roe_avg10y：近 5／10 年（每年取一個近四季 ROE）的平均，至少 3／5 年才計算
    gm_avg_long：近 10 年平均毛利率（至少 3 年）；gm_peak_ratio：目前毛利率 ÷ 長期平均（> 1.3 = 可能是景氣高峰）
    pb_hist_median：近 10 年每季（財報公布當月月底股價 ÷ 每股淨值）的中位數，至少 12 季
    pb_hist_pct：目前股價淨值比在自己歷史中的百分位（0 = 史上最便宜，1 = 最貴）
    """
    cols = ["roe_avg5y", "roe_avg10y", "roe_years", "gm_avg_long", "gm_peak_ratio",
            "pb_hist_median", "pb_hist_n", "pb_hist_pct"]
    if panel is None or panel.empty:
        return pd.DataFrame(columns=cols)
    date = pd.Timestamp(date)
    p = panel[(panel["available_date"] <= date) &
              (panel["period_end"] > date - pd.DateOffset(years=years, months=3))]
    if p.empty:
        return pd.DataFrame(columns=cols)
    p = p.sort_values(["id", "period_end"])
    pos = p.groupby("id").cumcount(ascending=False)          # 0 = 最新一季
    yearly = p[(pos % 4 == 0) & (pos < 4 * years)].copy()
    yearly["k"] = pos[(pos % 4 == 0) & (pos < 4 * years)] // 4
    g = yearly.groupby("id")
    roe5 = yearly[yearly["k"] < 5].groupby("id")["roe"]
    out = pd.DataFrame({
        "roe_avg5y": roe5.mean().where(roe5.count() >= 3),
        "roe_avg10y": g["roe"].mean().where(g["roe"].count() >= 5),
        "roe_years": g["roe"].count(),
        "gm_avg_long": g["gross_margin"].mean().where(g["gross_margin"].count() >= 3),
    })
    gm_now = yearly[yearly["k"] == 0].set_index("id")["gross_margin"]
    out["gm_peak_ratio"] = _div(gm_now.reindex(out.index), out["gm_avg_long"])
    # 歷史股價淨值比（需要月股價寬表）
    out["pb_hist_median"] = np.nan
    out["pb_hist_n"] = 0
    out["pb_hist_pct"] = np.nan
    if close is not None and ymap and "bvps" in p:
        q = p[["id", "available_date", "bvps"]].copy()
        q = q[q["bvps"] > 0]
        q["tk"] = q["id"].map(ymap)
        q["m"] = q["available_date"] + pd.offsets.MonthEnd(0)
        q = q.dropna(subset=["tk"])
        if isinstance(close, pd.Series):          # 已經 stack 好的（月, 代號）→ 收盤價，回測時重複使用
            st, tks = close, True
        else:
            tks = [t for t in q["tk"].unique() if t in close.columns]
            st = close[tks].stack() if tks else None
        if tks:
            st.index.names = ["m", "tk"]
            q["px"] = st.reindex(pd.MultiIndex.from_frame(q[["m", "tk"]])).values
            q["pbq"] = q["px"] / q["bvps"]
            q = q[np.isfinite(q["pbq"]) & (q["pbq"] > 0)]
            gq = q.groupby("id")["pbq"]
            n = gq.count()
            out["pb_hist_median"] = gq.median().where(n >= 12).reindex(out.index)
            out["pb_hist_n"] = n.reindex(out.index).fillna(0).astype(int)
            if pb_now is not None and len(pb_now):
                q["now"] = q["id"].map(pb_now)
                q = q[np.isfinite(q["now"])]
                pct = (q["pbq"] <= q["now"]).groupby(q["id"]).mean()
                out["pb_hist_pct"] = pct.where(n.reindex(pct.index) >= 12).reindex(out.index)
    out.index.name = "id"
    return out
