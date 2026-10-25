"""單一時點的完整篩選（回測與每日更新共用）。"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .events import event_status
from .industries import NOT_BUYABLE, assign as assign_industry
from .metrics import add_valuation, asof_fundamentals, history_metrics
from .screen import apply_hard_filters, assign_groups, flatten_weights, rank_stocks, sector_scores


def screen_market(panel: pd.DataFrame, info: pd.DataFrame, px: pd.DataFrame,
                  ret6: pd.Series, date: pd.Timestamp, cfg: dict, market: str,
                  ret12: pd.Series | None = None, events: pd.DataFrame | None = None,
                  pledge: pd.Series | None = None, month_rev: pd.DataFrame | None = None,
                  ctx: dict | None = None) -> dict:
    """
    panel：該市場的 fundamental_panel
    info：index=id，欄位 name, industry, is_financial, yahoo（美股另有 sic）
    px：index=id，欄位 close, avg_traded_value
    ret6 / ret12：index=id 的 6 個月報酬、12 個月（扣最近 1 個月）報酬
    ctx：估值需要的資料 {"adj": 月含息還原價寬表, "close": 月收盤價寬表, "ymap": id→代號, "index": 大盤代號,
         "rf": 無風險利率（None 用設定檔）, "implied": 是否算市場隱含成長率, "valuation": 舊規則也要估值（網站顯示用）}
    回傳 {"all": 全部股票指標＋過濾結果, "ranked": 候選排名（含放寬後仍通過者）, "sectors": 產業分數}
    """
    ctx = ctx or {}
    # 景氣階段：依階段決定用哪一套規則、金融股可否買（cycle.enabled 且有階段時）
    phase = ctx.get("phase")
    if phase and (cfg.get("cycle") or {}).get("enabled"):
        from .cycle import apply_phase
        cfg = apply_phase(cfg, phase)
    ir = cfg.get("industry_rules") or {}
    use_ir = bool(ir.get("enabled"))
    pm = panel[panel["market"] == market]
    f = asof_fundamentals(pm, date)
    empty = {"all": pd.DataFrame(), "ranked": pd.DataFrame(), "sectors": pd.DataFrame()}
    if f.empty:
        return empty
    icols = [c_ for c_ in ("name", "industry", "is_financial", "sic") if c_ in info]
    f = f.join(info[icols], on="id", how="inner")
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
    # 產業模板與同業比較群組（新舊規則都標示，網站顯示用）
    df = assign_industry(df, market, int(ir.get("min_peers", 15)))
    df["phase"] = phase
    df["buyable"] = ~df["template"].isin(NOT_BUYABLE)
    df["fin_allowed"] = (df["template"] != "financial") | bool(ir.get("financials", False))
    # 長期指標（正常化 ROE、毛利高峰、歷史股價淨值比）與估值（新規則用安全邊際篩選）
    if use_ir or ctx.get("valuation"):
        hist = history_metrics(pm, date, ctx.get("close"), ctx.get("ymap"),
                               df.set_index("id")["pb"].replace([np.inf, -np.inf], np.nan))
        df = df.join(hist, on="id")
        # 正常化 ROE：循環股用 10 年平均、其他用 5 年平均；年數不足時退回較短的平均或近四季
        r5 = df["roe_avg5y"].fillna(df["roe"])
        df["roe_norm"] = np.where(df["template"] == "cyclical", df["roe_avg10y"].fillna(r5), r5)
        from .valuation import betas, value_frame
        vcfg = cfg.get("valuation", {}) or {}
        bt = betas(ctx["adj"], ctx.get("ymap") or {}, ctx.get("index"), date, int(vcfg.get("beta_months", 60))) \
            if ctx.get("adj") is not None else pd.Series(dtype=float)
        vf = value_frame(df, bt, market, vcfg, rf=ctx.get("rf"), implied=bool(ctx.get("implied", False)))
        if len(vf):
            df = df.join(vf, on="id")
    df["groups_legacy"] = assign_groups(df, cfg.get("groups", {}), market)   # 舊規則的但書群組（網站比較用）
    if use_ir:
        # 新規則：依產業模板＋產業別覆寫門檻；估值與獲利條件用同業百分位
        ov = dict(ir.get("template_overrides") or {})
        for ind, o in ((ir.get("industry_overrides") or {}).get(market) or {}).items():
            ov[f"ind:{ind}"] = o
        df["groups"] = [[t_, f"ind:{i_}"] for t_, i_ in zip(df["template"], df["industry_norm"])]
        filters, icol = ir["filters"], "peer_group"
    else:
        # 舊規則：景氣循環但書群組
        df["groups"] = df["groups_legacy"]
        ov, filters, icol = cfg.get("overrides", {}), cfg["hard_filters"], "industry"
    df = apply_hard_filters(df, filters, market, industry_col=icol, overrides=ov)
    tol = cfg.get("buffer", {}).get("tolerance", 0.0)
    df = apply_hard_filters(df, filters, market, industry_col=icol, overrides=ov, tol=tol, prefix="r_")
    sectors = sector_scores(df[~df["is_financial"].astype(bool)], ret6, cfg["top_down"])
    cand = df[df["r_pass"]]
    excl = cfg["top_down"].get("exclude_bottom_sectors_pct", 0) or 0
    if excl > 0 and len(sectors):
        cutoff = sectors["score"].quantile(excl)
        bad = set(sectors.index[sectors["score"] < cutoff])
        cand = cand[~cand["industry"].isin(bad)]
    # 綜合分數：每個排名指標用「在全市場（新規則：同業群組）排第幾」的百分位加權
    # → 分數代表贏過全市場百分之幾，不會因為通過的股票少就被壓低
    if len(cand):
        cand = cand.copy()
        grp = df["peer_group"] if use_ir else pd.Series("all", index=df.index)
        fin = (cand["template"] == "financial") if use_ir else cand["is_financial"].astype(bool)
        for m_ in flatten_weights(cfg["ranking"]):
            if m_ not in df:
                continue
            pr = pd.to_numeric(df[m_], errors="coerce").replace([np.inf, -np.inf], np.nan).groupby(grp).rank(pct=True)
            cand[m_] = pr.reindex(cand.index)
            if fin.any():
                # 金融股：價值改用 E/P；現金流、毛利、流動性類指標不適用 → 中性
                if m_ == "earnings_yield" and "ep" in df:
                    prf = pd.to_numeric(df["ep"], errors="coerce").replace([np.inf, -np.inf], np.nan).groupby(grp).rank(pct=True)
                    cand.loc[fin, m_] = prf.reindex(cand.index)[fin]
                elif m_ not in ("roe", "roe_5y", "book_to_price"):
                    cand.loc[fin, m_] = np.nan
        if fin.any():
            cand.loc[fin, "cf_type"] = None
    ranked = rank_stocks(cand, cfg["ranking"], cfg["top_down"], sectors, pre_pct=True)
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
