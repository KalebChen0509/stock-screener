"""估值系統：用投資學的方法估「合理股價」，並反推市場隱含的成長率。

一、折現率（股東要求報酬，CAPM）
    r = 無風險利率 + β × 市場風險溢酬
    β：近 60 個月（至少 24 個月）月報酬對大盤迴歸，再做 Blume 調整（0.67β + 0.33），限制在 0.3～2.0
    無風險利率：美股用 FRED 10 年期美債殖利率（DGS10），台股用設定檔數值
    r 限制在 6%～16%，避免極端值

二、三種模型（每股價值）
  1. 剩餘收益模型（Residual Income Model, RIM）
     價值 = 目前每股淨值 + 未來每年「超額報酬」（(ROE − r) × 期初淨值）的折現值
     ROE 在 fade_years 年內線性回到 r（競爭會讓超額報酬消失，保守）
     淨值成長 = ROE × 保留率；保留率用近一年「淨值增加 ÷ 淨利」估，限制在 0.2～0.9
     → 最適合價值投資：以帳面價值為錨，不依賴遙遠的終值
  2. 自由現金流折現（DCF，兩階段）
     基準現金流 = 近 3 年平均自由現金流（營業現金流 − 資本支出），避免單年波動
     第 1～5 年成長率 = 近 3 年營收年化成長率（限制在 −3%～12%），第 6～10 年線性降到永續成長率
     企業價值 − 負債 + 現金 = 股東價值
     （簡化：用股東要求報酬 r 折現；通過篩選的公司負債低，誤差不大）
  3. 相對估值
     同市場、同產業（至少 5 家）的本益比、股價淨值比、EV/EBIT 中位數，套用到這家公司，取三者中位數

三、反推 DCF：讓 DCF 價值剛好等於目前市值的「第 1～5 年成長率」
    → 市場隱含成長率；高於公司過去成長率很多 = 市場期待很高

四、合理價區間：三個模型的最低、中位數、最高；安全邊際 = 中位數 ÷ 股價 − 1

沒有用到、需要時再補的資料：分析師預估（付費）、股利（目前用保留率替代，在乾淨盈餘假設下 RIM 與股利折現等價）。
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


def _cfg(cfg: dict | None) -> dict:
    out = dict(DEFAULTS)
    for k, v in (cfg or {}).items():
        out[k] = v
    return out


def betas(adj: pd.DataFrame, tickers: dict, index_ticker: str, date, months: int = 60,
          bounds=(0.3, 2.0)) -> pd.Series:
    """adj：月底含息還原價寬表；tickers：id -> 代號。回傳 id -> 調整後 β。"""
    if index_ticker not in adj:
        return pd.Series(dtype=float)
    a = adj.loc[:pd.Timestamp(date)].iloc[-(months + 1):]
    rm = a[index_ticker].pct_change()
    out = {}
    for sid, tk in tickers.items():
        if tk not in a:
            continue
        ri = a[tk].pct_change()
        ok = ri.notna() & rm.notna()
        if ok.sum() < 24:
            continue
        x, y = rm[ok].values, ri[ok].values
        vx = np.var(x, ddof=1)
        if vx <= 0:
            continue
        b = np.cov(x, y, ddof=1)[0, 1] / vx
        out[sid] = float(np.clip(0.67 * b + 0.33, *bounds))
    return pd.Series(out, dtype=float)


def rim_value(b0, roe0, r, retention, years=10):
    """剩餘收益模型：ROE 在 years 年內線性回到 r。回傳總股東價值。"""
    if not (np.isfinite(b0) and b0 > 0 and np.isfinite(roe0) and np.isfinite(r)):
        return np.nan
    roe0 = float(np.clip(roe0, -0.10, 0.40))
    v, b = b0, b0
    for t in range(1, years + 1):
        roe_t = roe0 + (r - roe0) * t / years
        v += (roe_t - r) * b / (1 + r) ** t
        b *= 1 + roe_t * retention
    return v


def dcf_value(fcf0, g1, gT, r, years1=5, years2=5):
    """兩階段 DCF：前 years1 年成長 g1，之後 years2 年線性降到 gT，再接永續成長。回傳企業價值。"""
    if not (np.isfinite(fcf0) and fcf0 > 0 and r > gT):
        return np.nan
    v, cf = 0.0, fcf0
    n = years1 + years2
    for t in range(1, n + 1):
        g = g1 if t <= years1 else g1 + (gT - g1) * (t - years1) / years2
        cf *= 1 + g
        v += cf / (1 + r) ** t
    v += cf * (1 + gT) / (r - gT) / (1 + r) ** n
    return v


def implied_growth(target_ev, fcf0, gT, r, lo=-0.10, hi=0.40):
    """反推：讓 DCF 企業價值 = target_ev 的第一階段成長率。"""
    if not (np.isfinite(target_ev) and target_ev > 0 and np.isfinite(fcf0) and fcf0 > 0 and r > gT):
        return np.nan
    f = lambda g: dcf_value(fcf0, g, gT, r) - target_ev  # noqa: E731
    flo, fhi = f(lo), f(hi)
    if not (np.isfinite(flo) and np.isfinite(fhi)):
        return np.nan
    if flo > 0:
        return lo      # 就算衰退 10% 價值仍高於市值：市場預期非常低
    if fhi < 0:
        return hi      # 要成長 40% 以上才撐得起：市場預期非常高
    for _ in range(60):
        m = (lo + hi) / 2
        if f(m) > 0:
            hi = m
        else:
            lo = m
    return (lo + hi) / 2


def peer_medians(df: pd.DataFrame, industry_col: str = "industry", min_peers: int = 5) -> pd.DataFrame:
    """同產業的本益比、股價淨值比、EV/EBIT 中位數（只用合理值）。產業家數不足用全市場中位數。"""
    d = df.copy()
    d["_pe"] = d["pe"].where((d["pe"] > 0) & (d["pe"] < 100))
    d["_pb"] = d["pb"].where((d["pb"] > 0) & (d["pb"] < 30))
    ev_ebit = d["ev"] / d["op_income"]
    d["_evebit"] = ev_ebit.where((d["op_income"] > 0) & (ev_ebit > 0) & (ev_ebit < 100))
    out = pd.DataFrame(index=d.index)
    for c in ("_pe", "_pb", "_evebit"):
        grp = d.groupby(industry_col)[c]
        med, cnt = grp.transform("median"), grp.transform("count")
        out[c] = med.where(cnt >= min_peers, d[c].median())
    return out


def value_frame(df: pd.DataFrame, beta: pd.Series, market: str, cfg: dict | None = None,
                rf: float | None = None) -> pd.DataFrame:
    """df：screen_market 的 all（含 close、adj_shares、equity、roe、fcf_3y_avg 等）。回傳每檔估值。"""
    c = _cfg(cfg)
    rf_ = rf if rf is not None and np.isfinite(rf) else c["rf"][market]
    erp, gT = c["erp"][market], c["terminal_g"][market]
    peers = peer_medians(df, min_peers=c["min_peers"])
    rows = []
    for idx, x in df.iterrows():
        sid = x["id"]
        sh, px = x.get("adj_shares"), x.get("close")
        if not (np.isfinite(sh) and sh > 0 and np.isfinite(px) and px > 0):
            continue
        b = beta.get(sid, np.nan)
        b_used = b if np.isfinite(b) else 1.0
        r = float(np.clip(rf_ + b_used * erp, *c["re_bounds"]))
        # 保留率：近一年淨值增加 ÷ 淨利
        ni, eq, eq4 = x.get("net_income"), x.get("equity"), x.get("equity_lag4")
        ret = (eq - eq4) / ni if (np.isfinite(ni) and ni > 0 and np.isfinite(eq4)) else np.nan
        ret = float(np.clip(ret, 0.2, 0.9)) if np.isfinite(ret) else 0.5
        rim = rim_value(eq, x.get("roe"), r, ret, c["fade_years"])
        fcf0 = x.get("fcf_3y_avg")
        hist_g = x.get("revenue_cagr_3y")
        g1 = float(np.clip(hist_g, *c["g1_bounds"])) if np.isfinite(hist_g) else 0.0
        net_cash = (x.get("cash_total") or 0) - (x.get("debt") or 0)
        ev_dcf = dcf_value(fcf0, g1, gT, r)
        dcf = ev_dcf + net_cash if np.isfinite(ev_dcf) else np.nan
        mcap = px * sh
        imp_g = implied_growth(mcap - net_cash, fcf0, gT, r)
        p = peers.loc[idx]
        rel = []
        if np.isfinite(p["_pe"]) and np.isfinite(ni) and ni > 0:
            rel.append(p["_pe"] * ni)
        if np.isfinite(p["_pb"]) and np.isfinite(eq) and eq > 0:
            rel.append(p["_pb"] * eq)
        ebit = x.get("op_income")
        if np.isfinite(p["_evebit"]) and np.isfinite(ebit) and ebit > 0:
            rel.append(p["_evebit"] * ebit + net_cash)
        relv = float(np.median(rel)) if rel else np.nan
        per = {k: (v / sh if np.isfinite(v) and v > 0 else np.nan) for k, v in
               (("rim", rim), ("dcf", dcf), ("rel", relv))}
        vals = [v for v in per.values() if np.isfinite(v)]
        if not vals:
            continue
        base = float(np.median(vals))
        mos = base / px - 1
        spread = max(vals) / min(vals)
        conf = "高" if len(vals) == 3 and spread < 2.0 else ("中" if len(vals) >= 2 and spread < 3.5 else "低")
        rows.append({"id": sid, "fair_low": min(vals), "fair_base": base, "fair_high": max(vals),
                     "mos": mos, "val_rim": per["rim"], "val_dcf": per["dcf"], "val_rel": per["rel"],
                     "implied_g": imp_g, "hist_g": hist_g if np.isfinite(hist_g) else np.nan,
                     "beta": b if np.isfinite(b) else np.nan, "r_e": r, "rf": rf_, "erp": erp,
                     "retention": ret, "g1": g1, "terminal_g": gT, "val_conf": conf,
                     "val_label": "低估" if mos >= c["undervalued"] else ("高估" if mos <= c["overvalued"] else "合理")})
    return pd.DataFrame(rows).set_index("id") if rows else pd.DataFrame()
