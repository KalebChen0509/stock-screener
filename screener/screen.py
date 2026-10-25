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


def assign_groups(df: pd.DataFrame, groups_cfg: dict, market: str,
                  industry_col: str = "industry") -> pd.Series:
    """依產業／代號／規則，把每檔股票歸入一或多個「但書群組」。回傳每列的群組清單。"""
    out = [[] for _ in range(len(df))]
    for name, g in (groups_cfg or {}).items():
        if g.get("enabled") is False:
            continue
        mask = pd.Series(False, index=df.index)
        inds = (g.get("industries") or {}).get(market) or []
        if inds:
            mask |= df[industry_col].isin(inds)
        ids = (g.get("ids") or {}).get(market) or []
        if ids:
            mask |= df["id"].astype(str).isin([str(i) for i in ids])
        rule = g.get("rule")
        if rule and rule["metric"] in df:
            mask |= OPS[rule["op"]](pd.to_numeric(df[rule["metric"]], errors="coerce"), rule["value"]).fillna(False)
        for i, m in enumerate(mask.values):
            if m:
                out[i].append(name)
    return pd.Series(out, index=df.index)


def _relax(op: str, v, tol: float):
    if tol <= 0 or isinstance(v, bool) or v is None or v == 0:
        return v
    if op in (">=", ">"):
        return v * (1 - tol) if v > 0 else v * (1 + tol)
    if op in ("<=", "<"):
        return v * (1 + tol) if v > 0 else v * (1 - tol)
    return v


def apply_hard_filters(df: pd.DataFrame, filters: list[dict], market: str,
                       industry_col: str = "industry", overrides: dict | None = None,
                       tol: float = 0.0, prefix: str = "f_") -> pd.DataFrame:
    """在 df 加上 f_<id>（是否通過）、pass_all、fail_count、failed（未通過清單）。
    overrides：{群組: {filter_id: {value / metric / enabled}}}，需要 df 有 groups 欄位。
    tol：放寬比例（換股緩衝區用），例如 0.1 = 門檻放寬 10%。
    op 可用 >=、<=、>、<、==、<=peer（≤ 同業中位數＋k 個標準差），
    以及 peer_pct>=、peer_pct<=（在同業比較群組 industry_col 裡的百分位，0～1）。"""
    df = df.copy()
    fcols = []
    groups = df["groups"] if "groups" in df else pd.Series([[] for _ in range(len(df))], index=df.index)
    for f in filters:
        col = f"{prefix}{f['id']}"
        # 每列各自的 metric、門檻、是否啟用（但書覆寫）
        metric = pd.Series(f["metric"], index=df.index)
        enabled = pd.Series(bool(f.get("enabled", True)), index=df.index)
        v0 = _threshold(f.get("value"), market)
        thr = pd.Series([v0] * len(df), index=df.index, dtype=object)
        for gname, ov in (overrides or {}).items():
            o = ov.get(f["id"])
            if not o:
                continue
            m = groups.map(lambda gs: gname in gs)
            if not m.any():
                continue
            if "metric" in o:
                metric[m] = o["metric"]
            if "value" in o:
                vv = _threshold(o["value"], market)
                thr[m] = pd.Series([vv] * int(m.sum()), index=m[m].index, dtype=object)
            if "enabled" in o:
                enabled[m] = bool(o["enabled"])
        if not enabled.any():
            continue
        vals = pd.Series(np.nan, index=df.index, dtype=object)
        op = f["op"]
        for mname in metric.unique():
            sel = metric == mname
            if op.startswith("peer_pct"):
                # 同業百分位：在同業比較群組裡（同一個指標）排第幾（0 = 最低、1 = 最高）
                src = pd.to_numeric(df[mname], errors="coerce") if mname in df else pd.Series(np.nan, index=df.index)
                grp_key = df[industry_col] if industry_col in df else pd.Series("all", index=df.index)
                pr = src.groupby(grp_key).rank(pct=True, method="average")
                vals[sel] = pr[sel]
                df.loc[sel, f"peer_{f['id']}"] = pr[sel]
            else:
                vals[sel] = df.loc[sel, mname] if mname in df else np.nan
        if op.startswith("peer_pct"):
            op = op[len("peer_pct"):]          # 之後當一般的 >=、<= 比較
        if op == "<=peer":
            s = pd.to_numeric(vals, errors="coerce")
            k = f.get("peer_k_std", 1.0)
            min_g = f.get("peer_min_group", 5)
            grp = s.groupby(df[industry_col])
            med, std, cnt = grp.transform("median"), grp.transform("std"), grp.transform("count")
            lim = med + k * std.fillna(0)
            all_lim = s.median() + k * s.std()
            lim = lim.where(cnt >= min_g, all_lim)
            if tol:
                lim = lim * (1 + tol)
            df[f"peer_limit_{f['metric']}"] = lim
            ok = s <= lim
        else:
            res = []
            for x, v in zip(vals.values, thr.values):
                if v is None:
                    res.append(True)
                    continue
                if x is None or (isinstance(x, float) and np.isnan(x)):
                    res.append(bool(f.get("na_pass", False)))
                    continue
                if isinstance(v, bool):
                    res.append(bool(x) == v)
                elif isinstance(v, str) and op == "==":   # 文字比對，例如 market == "us"
                    res.append(str(x) == v)
                else:
                    try:
                        res.append(bool(OPS[op](float(x), _relax(op, float(v), tol))))
                    except (TypeError, ValueError):
                        res.append(False)
            ok = pd.Series(res, index=df.index)
        ok = ok.fillna(False).astype(bool) | ~enabled
        df[col] = ok
        fcols.append(col)
    pass_col = "pass_all" if prefix == "f_" else prefix + "pass"
    if fcols:
        passed = df[fcols]
        df[pass_col] = passed.all(axis=1)
        if prefix == "f_":
            df["fail_count"] = (~passed).sum(axis=1)
            names = np.array([c[len(prefix):] for c in fcols])
            df["failed"] = [list(names[~row]) for row in passed.values]
    else:
        df[pass_col] = True
        if prefix == "f_":
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


def market_pct(values: pd.Series, ref: pd.Series) -> pd.Series:
    """values 在 ref（全市場）分布中的百分位（0～1，平均名次，同 rank(pct=True)）；缺值回傳 NaN。"""
    r = pd.to_numeric(ref, errors="coerce").replace([np.inf, -np.inf], np.nan).dropna().values
    v = pd.to_numeric(values, errors="coerce").replace([np.inf, -np.inf], np.nan)
    if len(r) == 0:
        return pd.Series(np.nan, index=values.index)
    r = np.sort(r)
    lo = np.searchsorted(r, v.fillna(0).values, side="left")
    hi = np.searchsorted(r, v.fillna(0).values, side="right")
    pct = np.where(hi > lo, (lo + (hi - lo + 1) / 2) / len(r), lo / len(r) + 0.5 / len(r))
    return pd.Series(np.clip(pct, 0, 1), index=values.index).where(v.notna())


def rank_stocks(df: pd.DataFrame, rank_cfg: dict, td_cfg: dict,
                sector_tbl: pd.DataFrame | None, industry_col: str = "industry",
                ref: pd.DataFrame | None = None, pre_pct: bool = False) -> pd.DataFrame:
    """對 df（通常是通過 hard filters 的股票）計算綜合分數。
    ref：全市場資料，百分位改成「在全市場排第幾」（分數 = 贏過全市場百分之幾的加權）；沒給時在 df 內部排名。
    pre_pct：df 的排名指標已經是 0～1 的百分位（新規則的同業百分位），直接使用。"""
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
        if pre_pct:
            pct = pd.to_numeric(df[m], errors="coerce").fillna(0.5)
        elif ref is not None and m in ref:
            pct = market_pct(df[m], ref[m]).fillna(0.5)
        else:
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
