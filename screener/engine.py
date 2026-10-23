"""單一時點的完整篩選（回測與每日更新共用）。"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .events import event_status
from .metrics import add_valuation, asof_fundamentals
from .screen import apply_hard_filters, assign_groups, rank_stocks, sector_scores


def screen_market(panel: pd.DataFrame, info: pd.DataFrame, px: pd.DataFrame,
                  ret6: pd.Series, date: pd.Timestamp, cfg: dict, market: str,
                  ret12: pd.Series | None = None, events: pd.DataFrame | None = None,
                  pledge: pd.Series | None = None, month_rev: pd.DataFrame | None = None) -> dict:
    """
    panel：該市場的 fundamental_panel
    info：index=id，欄位 name, industry, is_financial, yahoo
    px：index=id，欄位 close, avg_traded_value
    ret6 / ret12：index=id 的 6 個月報酬、12 個月（扣最近 1 個月）報酬
    回傳 {"all": 全部股票指標＋過濾結果, "ranked": 候選排名（含放寬後仍通過者）, "sectors": 產業分數}
    """
    f = asof_fundamentals(panel[panel["market"] == market], date)
    empty = {"all": pd.DataFrame(), "ranked": pd.DataFrame(), "sectors": pd.DataFrame()}
    if f.empty:
        return empty
    f = f.join(info[["name", "industry", "is_financial"]], on="id", how="inner")
    df = add_valuation(f, px)
    df = df[df["close"].notna() & df["market_cap"].notna()].copy()
    if df.empty:
        return empty
    # 防價值陷阱：12 個月動能在市場中的百分位
    if ret12 is not None and len(ret12):
        r = df["id"].map(ret12)
        df["ret_12m"] = r
        df["mom_12m_pct"] = r.rank(pct=True)
    else:
        df["mom_12m_pct"] = np.nan
    # 重大事件
    st = event_status(events, date, cfg.get("events", {})) if events is not None else pd.DataFrame()
    if len(st):
        df["event_veto"] = df["id"].map(st["event_veto"]).map(lambda x: bool(x) if pd.notna(x) else False)
        df["event_flags"] = df["id"].map(st["event_flags"])
    else:
        df["event_veto"] = False
        df["event_flags"] = None
    df["pledge_ratio"] = df["id"].map(pledge) if pledge is not None else np.nan
    # 台股月營收動能（到上個月為止已公布的資料）
    if market == "tw" and month_rev is not None and len(month_rev):
        from .month_rev import metrics_asof
        rm = metrics_asof(month_rev, date)
        for c_ in ("rev_yoy_3m", "rev_yoy_12m", "rev_accel"):
            df[c_] = df["id"].map(rm[c_]) if c_ in rm else np.nan
    else:
        df["rev_yoy_3m"] = df["rev_yoy_12m"] = df["rev_accel"] = np.nan
    # 景氣循環但書群組
    df["groups"] = assign_groups(df, cfg.get("groups", {}), market)
    ov = cfg.get("overrides", {})
    df = apply_hard_filters(df, cfg["hard_filters"], market, overrides=ov)
    tol = cfg.get("buffer", {}).get("tolerance", 0.0)
    df = apply_hard_filters(df, cfg["hard_filters"], market, overrides=ov, tol=tol, prefix="r_")
    sectors = sector_scores(df[~df["is_financial"].astype(bool)], ret6, cfg["top_down"])
    cand = df[df["r_pass"]]
    excl = cfg["top_down"].get("exclude_bottom_sectors_pct", 0) or 0
    if excl > 0 and len(sectors):
        cutoff = sectors["score"].quantile(excl)
        bad = set(sectors.index[sectors["score"] < cutoff])
        cand = cand[~cand["industry"].isin(bad)]
    # 金融股（若啟用專屬條件而入選）：一般公司的現金流、毛利、流動性指標不適用 → 設為中性；價值改用 E/P
    fin = cand["is_financial"].astype(bool) if "is_financial" in cand else pd.Series(False, index=cand.index)
    if fin.any():
        cand = cand.copy()
        cand.loc[fin, "earnings_yield"] = cand.loc[fin, "net_income"] / cand.loc[fin, "market_cap"]
        for c_ in ("fcf_yield", "cfo_to_ni", "fcf_to_ni", "op_margin", "cash_ratio", "equity_ratio"):
            if c_ in cand:
                cand.loc[fin, c_] = np.nan
        cand.loc[fin, "cf_type"] = None
    ranked = rank_stocks(cand, cfg["ranking"], cfg["top_down"], sectors)
    if len(ranked):
        ranked["rank"] = np.arange(1, len(ranked) + 1)
    return {"all": df, "ranked": ranked, "sectors": sectors}


def select_with_buffer(ranked: pd.DataFrame, held: list[str], n: int, mult: float = 2.0) -> list[str]:
    """換股緩衝區：已持有者放寬門檻後仍通過、且排名 ≤ n×mult 就續抱；其餘名額由完全通過者依排名補滿。"""
    if ranked is None or ranked.empty:
        return []
    keep = [i for i in ranked[ranked["rank"] <= n * mult]["id"] if i in set(held)][:n]
    pick = list(keep)
    for i in ranked[ranked["pass_all"]]["id"]:
        if len(pick) >= n:
            break
        if i not in pick:
            pick.append(i)
    return pick


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


def ret_n_months(adj: pd.DataFrame, date: pd.Timestamp, n: int, id_to_yahoo: dict,
                 skip: int = 0) -> pd.Series:
    if date not in adj.index:
        return pd.Series(dtype=float)
    loc = adj.index.get_loc(date) - skip
    if loc < n:
        return pd.Series(dtype=float)
    r = adj.iloc[loc] / adj.iloc[loc - n] - 1
    return pd.Series(id_to_yahoo).map(r)
