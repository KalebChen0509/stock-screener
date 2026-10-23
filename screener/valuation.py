"""估值系統：用投資學的方法估「合理股價」，並反推市場隱含的成長率。依產業模板選擇模型。

一、折現率（股東要求報酬，CAPM）
    r = 無風險利率 + β × 市場風險溢酬
    β：近 60 個月（至少 24 個月）月報酬對大盤迴歸，再做 Blume 調整（0.67β + 0.33），限制在 0.3～2.0
    無風險利率：美股用 FRED 10 年期美債殖利率（DGS10），台股用設定檔數值
    r 限制在 6%～16%，避免極端值

二、模型（每股價值）
  RIM  剩餘收益模型：價值 = 每股淨值 + 未來超額報酬（(ROE − r) × 期初淨值）的折現值；ROE 在 fade_years 年內回到 r
       ROE 用「正常化 ROE」：一般公司用近 5 年平均，循環股用近 10 年平均（避免拿景氣高峰的獲利往後推）
       淨值成長 = ROE × 保留率；保留率用近一年「淨值增加 ÷ 淨利」估，限制在 0.2～0.9
  DCF  兩階段自由現金流折現：近 3 年平均自由現金流（美股再扣股票酬勞），第 1～5 年成長率 = 近 3 年營收年化成長率
       （−3%～12%），第 6～10 年線性降到永續成長率；企業價值 − 負債 + 現金 = 股東價值
  REL  同業相對估值：同業比較群組（同市場同產業別，家數不足時同模板）的倍數中位數 × 這家公司的數字
  PBH  歷史股價淨值比：自己近 10 年股價淨值比中位數 × 目前淨值（循環股、營建）

三、各模板用哪些模型
  一般、防禦、公用：RIM、DCF、REL（本益比、股價淨值比、EV/EBIT）
  循環：RIM（10 年平均 ROE）、PBH、REL（只用股價淨值比，景氣高峰時本益比會假性便宜）
  營建：RIM（5 年平均 ROE）、PBH、REL（股價淨值比）
  金融：RIM（等同「合理股價淨值比」）、REL（股價淨值比、本益比）；不用現金流與企業價值
  成長觀察：DCF、REL（EV/營收）；只觀察不買
  新藥研究、排除：不估值
  股東權益為負或很薄（權益 ÷ 資產 < 5%，常見於大量買回的美股）：停用 RIM 與股價淨值比

四、反推 DCF：讓 DCF 價值剛好等於目前市值的「第 1～5 年成長率」＝市場隱含成長率
五、合理價區間：各模型的最低、中位數、最高；安全邊際 = 中位數 ÷ 股價 − 1
"""
from __future__ import annotations

import numpy as np
import pandas as pd

DEFAULTS = {
    "rf": {"tw": 0.016, "us": 0.045},          # 無風險利率（美股會用 FRED 最新值覆蓋）
    "erp": {"tw": 0.055, "us": 0.045},         # 市場風險溢酬（假設）
    "terminal_g": {"tw": 0.02, "us": 0.025},  # 永續成長率（低於名目 GDP 成長）
    "beta_months": 60,
    "fade_years": 10,
    "g1_bounds": [-0.03, 0.12],
    "re_bounds": [0.06, 0.16],
    "beta_bounds": [0.3, 2.0],
    "min_peers": 5,
    "undervalued": 0.30,                       # 安全邊際 ≥ 30% 標為低估
    "overvalued": -0.20,                       # ≤ −20% 標為高估
}
MODEL_LABEL = {"rim": "RIM", "dcf": "DCF", "rel": "同業", "pbh": "歷史P/B"}
TEMPLATE_MODELS = {
    "general": ("rim", "dcf", "rel"), "defensive": ("rim", "dcf", "rel"), "utility": ("rim", "dcf", "rel"),
    "cyclical": ("rim", "pbh", "rel"), "construction": ("rim", "pbh", "rel"),
    "financial": ("rim", "rel"), "growth_watch": ("dcf", "rel"),
    "biotech_research": (), "exclude": (),
}
REL_MULTIPLES = {
    "general": ("pe", "pb", "evebit"), "defensive": ("pe", "pb", "evebit"), "utility": ("pe", "pb", "evebit"),
    "cyclical": ("pb",), "construction": ("pb",), "financial": ("pb", "pe"), "growth_watch": ("evsales",),
}


def _cfg(cfg: dict | None) -> dict:
    out = dict(DEFAULTS)
    for k, v in (cfg or {}).items():
        out[k] = v
    return out


def betas(adj: pd.DataFrame, tickers: dict, index_ticker: str, date, months: int = 60,
          bounds=(0.3, 2.0)) -> pd.Series:
    """adj：月底含息還原價寬表；tickers：id -> 代號。回傳 id -> Blume 調整後 β（向量化計算）。"""
    if adj is None or index_ticker not in adj:
        return pd.Series(dtype=float)
    a = adj.loc[:pd.Timestamp(date)].iloc[-(months + 1):]
    if len(a) < 25:
        return pd.Series(dtype=float)
    R = a.pct_change().iloc[1:]
    rm = R[index_ticker].values
    cols = sorted({t for t in tickers.values() if t in R.columns})
    if not cols:
        return pd.Series(dtype=float)
    X = R[cols].values
    mask = np.isfinite(X) & np.isfinite(rm)[:, None]
    n = mask.sum(axis=0)
    x = np.where(mask, X, np.nan)
    m = np.where(mask, rm[:, None], np.nan)
    with np.errstate(invalid="ignore", divide="ignore"):
        xc = x - np.nanmean(x, axis=0)
        mc = m - np.nanmean(m, axis=0)
        cov = np.nansum(xc * mc, axis=0) / (n - 1)
        var = np.nansum(mc * mc, axis=0) / (n - 1)
        b = cov / var
    b = np.where((n >= 24) & (var > 0), np.clip(0.67 * b + 0.33, *bounds), np.nan)
    bt = pd.Series(b, index=cols)
    out = pd.Series({sid: bt.get(tk, np.nan) for sid, tk in tickers.items()}, dtype=float)
    return out.dropna()


# ---------------- 單檔版本（說明與測試用） ----------------
def rim_value(b0, roe0, r, retention, years=10):
    v = _rim_vec(np.array([b0], float), np.array([roe0], float), np.array([r], float),
                 np.array([retention], float), years)
    return float(v[0])


def dcf_value(fcf0, g1, gT, r, years1=5, years2=5):
    v = _dcf_vec(np.array([fcf0], float), np.array([g1], float), gT, np.array([r], float), years1, years2)
    return float(v[0])


def implied_growth(target_ev, fcf0, gT, r, lo=-0.10, hi=0.40):
    v = _implied_vec(np.array([target_ev], float), np.array([fcf0], float), gT, np.array([r], float), lo, hi)
    return float(v[0])


# ---------------- 向量化版本 ----------------
def _rim_vec(b0, roe0, r, ret, years=10):
    ok = np.isfinite(b0) & (b0 > 0) & np.isfinite(roe0) & np.isfinite(r)
    roe0 = np.clip(np.nan_to_num(roe0), -0.10, 0.40)
    r_ = np.nan_to_num(r, nan=0.1)
    ret = np.nan_to_num(ret, nan=0.5)
    v = np.nan_to_num(b0).astype(float)
    b = v.copy()
    for t in range(1, years + 1):
        roe_t = roe0 + (r_ - roe0) * t / years
        v = v + (roe_t - r_) * b / (1 + r_) ** t
        b = b * (1 + roe_t * ret)
    return np.where(ok, v, np.nan)


def _dcf_vec(fcf0, g1, gT, r, years1=5, years2=5):
    ok = np.isfinite(fcf0) & (fcf0 > 0) & np.isfinite(r) & (r > gT) & np.isfinite(g1)
    r_ = np.where(ok, r, 0.1)
    cf = np.where(ok, fcf0, 0.0).astype(float)
    g1 = np.nan_to_num(g1)
    v = np.zeros_like(cf)
    n = years1 + years2
    for t in range(1, n + 1):
        g = g1 if t <= years1 else g1 + (gT - g1) * (t - years1) / years2
        cf = cf * (1 + g)
        v = v + cf / (1 + r_) ** t
    v = v + cf * (1 + gT) / (r_ - gT) / (1 + r_) ** n
    return np.where(ok, v, np.nan)


def _implied_vec(target, fcf0, gT, r, lo=-0.10, hi=0.40, iters=50):
    ok = np.isfinite(target) & (target > 0) & np.isfinite(fcf0) & (fcf0 > 0) & np.isfinite(r) & (r > gT)
    n = len(target)
    a, b = np.full(n, lo), np.full(n, hi)
    f = lambda g: _dcf_vec(fcf0, g, gT, r) - target  # noqa: E731
    flo, fhi = f(a), f(b)
    res = np.full(n, np.nan)
    res[ok & (flo > 0)] = lo           # 就算衰退 10% 價值仍高於市值：市場預期非常低
    res[ok & (fhi < 0)] = hi           # 要成長 40% 以上才撐得起：市場預期非常高
    todo = ok & (flo <= 0) & (fhi >= 0)
    for _ in range(iters):
        m = (a + b) / 2
        fm = f(m)
        up = fm > 0
        b = np.where(todo & up, m, b)
        a = np.where(todo & ~up, m, a)
    res[todo] = ((a + b) / 2)[todo]
    return res


def peer_medians(df: pd.DataFrame, peer_col: str = "industry", min_peers: int = 5) -> pd.DataFrame:
    """同業的本益比、股價淨值比、EV/EBIT、EV/營收中位數（只用合理值）。家數不足用全市場中位數。"""
    d = pd.DataFrame(index=df.index)
    pe, pb = pd.to_numeric(df["pe"], errors="coerce"), pd.to_numeric(df["pb"], errors="coerce")
    d["pe"] = pe.where((pe > 0) & (pe < 100))
    d["pb"] = pb.where((pb > 0) & (pb < 30))
    with np.errstate(divide="ignore", invalid="ignore"):
        ev_ebit = df["ev"] / df["op_income"]
        ev_sales = df["ev"] / df["revenue"] if "revenue" in df else pd.Series(np.nan, index=df.index)
    d["evebit"] = ev_ebit.where((df["op_income"] > 0) & (ev_ebit > 0) & (ev_ebit < 100))
    d["evsales"] = ev_sales.where((ev_sales > 0) & (ev_sales < 50))
    key = df[peer_col] if peer_col in df else pd.Series("all", index=df.index)
    out = pd.DataFrame(index=df.index)
    for c in ("pe", "pb", "evebit", "evsales"):
        grp = d[c].groupby(key)
        med, cnt = grp.transform("median"), grp.transform("count")
        out[c] = med.where(cnt >= min_peers, d[c].median())
    return out


def value_frame(df: pd.DataFrame, beta: pd.Series, market: str, cfg: dict | None = None,
                rf: float | None = None, implied: bool = True) -> pd.DataFrame:
    """df：screen_market 的 all。有 template 欄位時依模板選模型；沒有時全部當一般公司。回傳 index=id 的估值表。"""
    if df is None or df.empty:
        return pd.DataFrame()
    c = _cfg(cfg)
    rf_ = rf if rf is not None and np.isfinite(rf) else c["rf"][market]
    erp, gT = c["erp"][market], c["terminal_g"][market]

    def col(name, default=np.nan):
        return pd.to_numeric(df[name], errors="coerce").values.astype(float) if name in df \
            else np.full(len(df), default, dtype=float)

    ids = df["id"].values
    tpl = df["template"].fillna("general").values if "template" in df else np.array(["general"] * len(df))
    sh, px = col("adj_shares"), col("close")
    valid = np.isfinite(sh) & (sh > 0) & np.isfinite(px) & (px > 0)
    b = pd.Series(ids).map(beta).values.astype(float) if beta is not None and len(beta) else np.full(len(df), np.nan)
    r = np.clip(rf_ + np.where(np.isfinite(b), b, 1.0) * erp, *c["re_bounds"])
    eq, ni, eq4 = col("equity"), col("net_income"), col("equity_lag4")
    with np.errstate(divide="ignore", invalid="ignore"):
        ret = (eq - eq4) / ni
    ret = np.where(np.isfinite(ret) & (ni > 0), np.clip(ret, 0.2, 0.9), 0.5)
    # 正常化 ROE：循環股 10 年平均、其他 5 年平均；不足時退回近四季 ROE
    roe, r5, r10 = col("roe"), col("roe_avg5y"), col("roe_avg10y")
    norm5 = np.where(np.isfinite(r5), r5, roe)
    norm10 = np.where(np.isfinite(r10), r10, norm5)
    roe_used = np.where(tpl == "cyclical", norm10, norm5) if "template" in df else roe
    eq_ratio = col("equity_ratio")
    thin = ~(eq > 0) | (np.isfinite(eq_ratio) & (eq_ratio < 0.05))
    cash, debt = np.nan_to_num(col("cash_total")), np.nan_to_num(col("debt"))
    net_cash = cash - debt
    fcf0 = col("fcf_3y_avg_adj") if "fcf_3y_avg_adj" in df else col("fcf_3y_avg")
    fcf0 = np.where(np.isfinite(fcf0), fcf0, col("fcf_3y_avg"))
    hist_g = col("revenue_cagr_3y")
    g1 = np.where(np.isfinite(hist_g), np.clip(hist_g, *c["g1_bounds"]), 0.0)
    ebit, rev = col("op_income"), col("revenue")

    # ---- 各模型（總股東價值）----
    rim = _rim_vec(eq, roe_used, r, ret, int(c["fade_years"]))
    dcf = _dcf_vec(fcf0, g1, gT, r) + net_cash
    peers = peer_medians(df, "peer_group" if "peer_group" in df else "industry", c["min_peers"])
    comp = {
        "pe": np.where(ni > 0, peers["pe"].values * ni, np.nan),
        "pb": np.where((eq > 0) & ~thin, peers["pb"].values * eq, np.nan),
        "evebit": np.where(ebit > 0, peers["evebit"].values * ebit + net_cash, np.nan),
        "evsales": np.where(rev > 0, peers["evsales"].values * rev + net_cash, np.nan),
    }
    rel = np.full(len(df), np.nan)
    for t_ in np.unique(tpl):
        sel = tpl == t_
        mults = REL_MULTIPLES.get(t_, ("pe", "pb", "evebit")) if "template" in df else ("pe", "pb", "evebit")
        if not mults:
            continue
        stack = np.vstack([comp[m][sel] for m in mults])
        stack = np.where(stack > 0, stack, np.nan)
        with np.errstate(all="ignore"):
            import warnings
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", RuntimeWarning)
                rel[sel] = np.nanmedian(stack, axis=0)
    pbh = col("pb_hist_median") * eq
    models = {"rim": rim, "dcf": dcf, "rel": rel, "pbh": pbh}
    use = {}
    for m in models:
        if "template" in df:
            u = np.array([m in TEMPLATE_MODELS.get(t_, ("rim", "dcf", "rel")) for t_ in tpl])
        else:
            u = np.full(len(df), m in ("rim", "dcf", "rel"))
        if m in ("rim",):
            u &= ~thin            # 股東權益很薄或為負：不用淨值類模型
        use[m] = u
    per = {}
    for m, v in models.items():
        with np.errstate(divide="ignore", invalid="ignore"):
            x = v / sh
        per[m] = np.where(use[m] & valid & np.isfinite(x) & (x > 0), x, np.nan)
    M = np.vstack([per[m] for m in ("rim", "dcf", "rel", "pbh")])
    cnt = np.isfinite(M).sum(axis=0)
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        base = np.nanmedian(M, axis=0)
        lo_, hi_ = np.nanmin(M, axis=0), np.nanmax(M, axis=0)
    with np.errstate(divide="ignore", invalid="ignore"):
        mos = base / px - 1
        spread = hi_ / lo_
    conf = np.where((cnt >= 3) & (spread < 2.0), "高", np.where((cnt >= 2) & (spread < 3.5), "中", "低"))
    label = np.where(mos >= c["undervalued"], "低估", np.where(mos <= c["overvalued"], "高估", "合理"))
    label = np.where(tpl == "growth_watch", "觀察", label)
    imp = np.full(len(df), np.nan)
    if implied:
        mcap = px * sh
        imp = _implied_vec(mcap - net_cash, fcf0, gT, r)
    names = []
    for i in range(len(df)):
        ms = [MODEL_LABEL[m] for m in ("rim", "dcf", "rel", "pbh") if np.isfinite(per[m][i])]
        names.append("、".join(ms))
    out = pd.DataFrame({
        "id": ids, "fair_low": lo_, "fair_base": base, "fair_high": hi_, "mos": mos,
        "val_rim": per["rim"], "val_dcf": per["dcf"], "val_rel": per["rel"], "val_pbh": per["pbh"],
        "implied_g": imp, "hist_g": hist_g, "beta": b, "r_e": r, "rf": rf_, "erp": erp,
        "retention": ret, "g1": g1, "terminal_g": gT, "val_conf": conf, "val_label": label,
        "roe_used": roe_used, "val_models": names, "val_n": cnt,
    })
    keep = valid & (cnt > 0)
    out = out[keep]
    return out.set_index("id")
