"""【2026/11/06】先跑 factor_data.py；用法：research/factor_scan.py 10（或 30）。單一因子掃描：每月買分數最高 N 檔（等權重、扣交易成本），跟大盤比。"""
import os  # noqa: F401
import sys  # noqa: F401
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _paths import CACHE, OUT, ROOT  # noqa: E402,F401
import pickle
import sys

import numpy as np
import pandas as pd

d = pickle.load(open(OUT / "factor.pkl", "rb"))
df, bench = d["df"], d["bench"]
N = int(sys.argv[1]) if len(sys.argv) > 1 else 10
COST = {"tw": 0.001425 + 0.004425, "us": 0.0016 + 0.001}       # 換掉一檔＝賣＋買（台股含證交稅 0.3%；美股含匯差）
ELIG = {"tw": (3e9, 1e7), "us": (3e8, 2e6)}
BM = {"tw": "0050.TW", "us": "SPY"}


def rank_pct(s):
    return s.rank(pct=True)


FACTORS = {  # 名稱: (欄位或函式, 高分較好?)
    "動能 12-1 個月": ("ret_12m", True),
    "動能 6 個月": ("ret_6m", True),
    "價值 本益比倒數 E/P": ("earnings_yield", True),
    "價值 淨值股價比 B/P": ("book_to_price", True),
    "價值 自由現金流殖利率": ("fcf_yield", True),
    "品質 ROE": ("roe", True),
    "品質 毛利率": ("gross_margin", True),
    "成長 營收年增": ("rev_growth_1y", True),
    "成長 EPS 年增": ("eps_growth_1y", True),
    "財報驚喜 SUE": ("sue", True),
    "台股 月營收近3月年增": ("rev_yoy_3m", True),
    "台股 月營收加速": ("rev_accel", True),
    "股東回報率": ("shareholder_yield", True),
    "低波動": ("vol_12m", False),
    "市值最大": ("market_cap", True),
    # 事先定好的組合（各因子百分位平均，不調參數）
    "組合 動能＋品質": (lambda g: rank_pct(g["ret_12m"]) + rank_pct(g["roe"]), True),
    "組合 動能＋營收成長": (lambda g: rank_pct(g["ret_12m"]) + rank_pct(g["rev_growth_1y"]), True),
    "組合 動能＋財報驚喜": (lambda g: rank_pct(g["ret_12m"]) + rank_pct(g["sue"]), True),
    "組合 動能＋月營收": (lambda g: rank_pct(g["ret_12m"]) + rank_pct(g["rev_yoy_3m"]), True),
    "組合 價值＋動能": (lambda g: rank_pct(g["earnings_yield"]) + rank_pct(g["ret_12m"]), True),
    "組合 品質＋價值＋動能": (lambda g: rank_pct(g["roe"]) + rank_pct(g["earnings_yield"]) + rank_pct(g["ret_12m"]), True),
}


def stats(r: pd.Series, b: pd.Series) -> dict:
    r, b = r.dropna(), b.reindex(r.index).fillna(0)
    nav = (1 + r).cumprod()
    cagr = nav.iloc[-1] ** (12 / len(r)) - 1
    bc = (1 + b).prod() ** (12 / len(b)) - 1
    ex = r - b
    t = ex.mean() / (ex.std() / np.sqrt(len(ex))) if ex.std() > 0 else np.nan
    h = len(r) // 2
    c1 = (1 + r.iloc[:h]).prod() ** (12 / h) - 1 - ((1 + b.iloc[:h]).prod() ** (12 / h) - 1)
    c2 = (1 + r.iloc[h:]).prod() ** (12 / (len(r) - h)) - 1 - ((1 + b.iloc[h:]).prod() ** (12 / (len(r) - h)) - 1)
    rr = (1 + r).rolling(36).apply(np.prod, raw=True)
    rb = (1 + b).rolling(36).apply(np.prod, raw=True)
    beat36 = float((rr > rb).dropna().mean())
    return {"年化": cagr, "超額": cagr - bc, "前5年超額": c1, "後5年超額": c2, "t值": t,
            "最大跌幅": float((nav / nav.cummax() - 1).min()), "36月贏的比例": beat36}


def portfolio(g_all: pd.DataFrame, mkt: str, key, hi: bool, n: int = N):
    """每月取前 n 檔等權重；回傳月報酬（扣換股成本）與平均每月換手檔數。"""
    rets, prev, turns = {}, set(), []
    for t, g in g_all.groupby("date"):
        s = key(g) if callable(key) else g[key]
        g = g.assign(_s=s).dropna(subset=["_s"])
        if len(g) < n * 2:
            continue
        pick = g.nlargest(n, "_s") if hi else g.nsmallest(n, "_s")
        ids = set(pick["id"])
        k = len(ids - prev) if prev else n
        turns.append(k)
        rets[t] = pick["fwd"].mean() - k / n * COST[mkt]
        prev = ids
    return pd.Series(rets).sort_index(), float(np.mean(turns[1:])) if len(turns) > 1 else np.nan


rows = []
for mkt in ("tw", "us"):
    cap, liq = ELIG[mkt]
    g = df[(df["market"] == mkt) & df["has_px"] & (df["market_cap"] >= cap) & (df["avg_traded_value"] >= liq)
           & ~df["is_financial"].fillna(False).astype(bool)].copy()
    b = bench[BM[mkt]]
    n_univ = g.groupby("date").size()
    print(f"\n==== {mkt.upper()}（比 {BM[mkt]}）：每月可選 {int(n_univ.median())} 檔，前 {N} 名等權、扣換股成本 ====")
    bs = stats(b.loc[g["date"].min():g["date"].max()], b * 0)
    print(f"{'大盤 ' + BM[mkt]:<22}年化 {bs['年化']:6.1%}  最大跌幅 {bs['最大跌幅']:6.1%}")
    ew = g.groupby("date")["fwd"].mean()
    cw = g.groupby("date").apply(lambda x: np.average(x["fwd"], weights=x["market_cap"]))
    for name, s in (("全部可選股 等權重", ew), ("全部可選股 市值加權", cw)):
        st = stats(s, b)
        rows.append({"市場": mkt, "方法": name, **st, "換手": np.nan})
    vr = g.assign(_v=-g["value_rank"])
    for name, (key, hi) in [("目前策略（價值篩選前N）", ("_v", True))] + list(FACTORS.items()):
        src = vr if key == "_v" else g
        if not callable(key) and (key not in src or src[key].notna().sum() < 1000):
            continue
        r, turn = portfolio(src, mkt, key, hi)
        if len(r) < 60:
            continue
        rows.append({"市場": mkt, "方法": name, **stats(r, b), "換手": turn})
res = pd.DataFrame(rows)
pd.set_option("display.width", 220)
for mkt in ("tw", "us"):
    x = res[res["市場"] == mkt].drop(columns="市場").sort_values("超額", ascending=False)
    fmt = x.copy()
    for c in ("年化", "超額", "前5年超額", "後5年超額", "最大跌幅", "36月贏的比例"):
        fmt[c] = fmt[c].map(lambda v: f"{v:+.1%}" if c != "36月贏的比例" else f"{v:.0%}")
    fmt["t值"] = fmt["t值"].map(lambda v: f"{v:.2f}")
    fmt["換手"] = fmt["換手"].map(lambda v: "" if pd.isna(v) else f"{v:.1f}")
    print(f"\n---- {mkt.upper()} ----")
    print(fmt.to_string(index=False))
res.to_csv(OUT / f"factor_scan_n{N}.csv", index=False)
