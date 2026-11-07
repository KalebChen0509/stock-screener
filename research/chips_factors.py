"""【2026/11/07】台股籌碼（三大法人、融資融券）能不能幫選股、能不能當大盤入場條件。

先跑 factor_data.py（產生 research/out/factor.pkl）；籌碼歷史在 cache/chips_hist/monthly.csv.gz（籌碼歷史回補 workflow）。
規則事先定好、不調參數：
A. 個股：每月買分數最高的 10／30 檔（等權、扣換股成本）vs 0050。籌碼都用「當月（含）以前」的資料，月底收盤後才決定。
B. 大盤：月底判斷，條件成立就持有 0050、不成立放現金（0%），下個月生效；扣 ETF 進出成本。
"""
import os  # noqa: F401
import sys  # noqa: F401
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _paths import CACHE, OUT, ROOT  # noqa: E402,F401
import pickle  # noqa: E402

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

COST = 0.001425 + 0.004425          # 個股換一檔＝賣＋買（含證交稅 0.3%）
ETF_BUY, ETF_SELL = 0.001425, 0.002425

d = pickle.load(open(OUT / "factor.pkl", "rb"))
df, bench = d["df"], d["bench"]
b0050 = bench["0050.TW"]
tw = df[(df["market"] == "tw") & df["has_px"] & (df["market_cap"] >= 3e9) & (df["avg_traded_value"] >= 1e7)
        & ~df["is_financial"].fillna(False).astype(bool)].copy()

# ---- 股價（算流通股數＝市值÷收盤）
uni = pd.read_csv(os.path.join(CACHE, "tw_universe.csv"), dtype=str)
ymap = dict(zip(uni["stock_id"] if "stock_id" in uni else uni["id"], uni["yahoo"]))
pm = pd.read_csv(os.path.join(CACHE, "prices_monthly.csv.gz"), parse_dates=["month"])
close = pm.pivot_table(index="month", columns="ticker", values="close", aggfunc="last")
tw["close"] = [close.at[t, ymap.get(i)] if ymap.get(i) in close and t in close.index else np.nan for i, t in zip(tw["id"], tw["date"])]
tw["shares"] = tw["market_cap"] / tw["close"]

# ---- 籌碼（每月彙總）→ 因子
ch = pd.read_csv(os.path.join(CACHE, "chips_hist", "monthly.csv.gz"), parse_dates=["month"], dtype={"id": str}).sort_values(["id", "month"])
g = ch.groupby("id")
for c in ("foreign", "trust", "total"):
    ch[f"{c}_3m"] = g[c].transform(lambda s: s.rolling(3, min_periods=3).sum())
ch["margin_prev"] = g["margin"].shift(1)
ch["fdays"] = ch["foreign_days"] / ch["n_days"].replace(0, np.nan)
ch["short_ratio"] = ch["short"] / ch["margin"].replace(0, np.nan)
tw = tw.merge(ch.rename(columns={"month": "date"}), on=["id", "date"], how="left")
sh = tw["shares"]
tw["f1"], tw["f3"] = tw["foreign"] / sh, tw["foreign_3m"] / sh
tw["t1"], tw["t3"] = tw["trust"] / sh, tw["trust_3m"] / sh
tw["all3"] = tw["total_3m"] / sh
tw["margin_chg"] = (tw["margin"] - tw["margin_prev"]) * 1000 / sh        # 張 → 股


def rp(s):
    return s.rank(pct=True)


FACTORS = {
    "外資買超 1 個月": ("f1", True), "外資買超 3 個月": ("f3", True),
    "投信買超 1 個月": ("t1", True), "投信買超 3 個月": ("t3", True),
    "三大法人合計 3 個月": ("all3", True), "外資買超天數比例": ("fdays", True),
    "融資減少（散戶退場）": ("margin_chg", False), "券資比高": ("short_ratio", True),
    "組合 動能＋外資 3 個月": (lambda g: rp(g["ret_12m"]) + rp(g["f3"]), True),
    "組合 動能＋投信 3 個月": (lambda g: rp(g["ret_12m"]) + rp(g["t3"]), True),
    "（參考）組合 動能＋財報驚喜": (lambda g: rp(g["ret_12m"]) + rp(g["sue"]), True),
}


def stats(r, b):
    r, b = r.dropna(), b.reindex(r.index).fillna(0)
    nav = (1 + r).cumprod()
    cagr = nav.iloc[-1] ** (12 / len(r)) - 1
    bc = (1 + b).prod() ** (12 / len(b)) - 1
    ex = r - b
    t = ex.mean() / (ex.std() / np.sqrt(len(ex))) if ex.std() > 0 else np.nan
    h = len(r) // 2
    ann = lambda x: (1 + x).prod() ** (12 / len(x)) - 1  # noqa: E731
    return {"年化": cagr, "超額": cagr - bc, "前5年超額": ann(r.iloc[:h]) - ann(b.iloc[:h]), "後5年超額": ann(r.iloc[h:]) - ann(b.iloc[h:]),
            "t值": t, "最大跌幅": float((nav / nav.cummax() - 1).min())}


def portfolio(g_all, key, hi, n):
    rets, prev = {}, set()
    for t, g in g_all.groupby("date"):
        s = key(g) if callable(key) else g[key]
        g = g.assign(_s=s).dropna(subset=["_s"])
        if len(g) < n * 2:
            continue
        pick = g.nlargest(n, "_s") if hi else g.nsmallest(n, "_s")
        ids = set(pick["id"])
        k = len(ids - prev) if prev else n
        rets[t] = pick["fwd"].mean() - k / n * COST
        prev = ids
    return pd.Series(rets).sort_index()


print(f"==== A. 台股個股籌碼因子（{tw['date'].min().date()}～{tw['date'].max().date()}，比 0050；每月可選約 {int(tw.groupby('date').size().median())} 檔）====")
rows = []
for n in (10, 30):
    for name, (key, hi) in FACTORS.items():
        r = portfolio(tw, key, hi, n)
        if len(r) >= 60:
            rows.append({"檔數": n, "因子": name, **stats(r, b0050)})
ew = tw.groupby("date")["fwd"].mean()
rows.append({"檔數": "全部", "因子": "全部可選股 等權（基準線）", **stats(ew, b0050)})
res = pd.DataFrame(rows)
fmt = res.copy()
for c in ("年化", "超額", "前5年超額", "後5年超額", "最大跌幅"):
    fmt[c] = fmt[c].map(lambda v: f"{v:+.1%}")
fmt["t值"] = fmt["t值"].map(lambda v: f"{v:.2f}")
pd.set_option("display.width", 220)
print(fmt.to_string(index=False))
res.to_csv(OUT / "chips_factors_stock.csv", index=False)

# ==================== B. 大盤入場條件 ====================
pm_tw = pm[pm["ticker"].isin(["^TWII", "0050.TW"])].pivot_table(index="month", columns="ticker", values="adj_close", aggfunc="last")
pm_tw = pm_tw[pm_tw.index <= pd.Timestamp("2026-10-31")]
r50 = pm_tw["0050.TW"].pct_change()
twii = pm_tw["^TWII"]
trend = twii > twii.rolling(10).mean()
# 全市場外資、投信買賣超金額（股數 × 月底收盤；上市＋上櫃）
ch2 = ch.copy()
ch2["tk"] = ch2["id"].map(ymap)
ch2["px"] = [close.at[t, k] if k in close and t in close.index else np.nan for k, t in zip(ch2["tk"], ch2["month"])]
mkt = ch2.assign(fv=ch2["foreign"] * ch2["px"], tv=ch2["trust"] * ch2["px"]).groupby("month")[["fv", "tv"]].sum() / 1e8   # 億元
f1m, f3m = mkt["fv"] > 0, mkt["fv"].rolling(3).sum() > 0
SIG = {
    "永遠持有 0050": pd.Series(True, index=r50.index),
    "只看趨勢（站上 10 個月均線）": trend,
    "只看外資（當月淨買超）": f1m,
    "只看外資（近 3 個月淨買超）": f3m,
    "趨勢＋外資當月買超（都成立才進場）": trend & f1m,
    "趨勢＋外資 3 個月買超（都成立才進場）": trend & f3m,
    "趨勢或外資當月買超（任一成立就進場）": trend | f1m,
}
start = pd.Timestamp("2015-05-31")
idx = r50.index[(r50.index >= start)]
print(f"\n==== B. 大盤入場條件（{idx[0].date()}～{idx[-1].date()}，月底判斷、下個月持有 0050，否則現金）====")
out = []
for name, s in SIG.items():
    on = s.reindex(r50.index).fillna(False).astype(float).shift(1).reindex(idx).fillna(0)     # 前一個月底的訊號
    d_ = on.diff().fillna(on.iloc[0])
    r = on * r50.reindex(idx).fillna(0) - np.where(d_ > 0, d_ * ETF_BUY, -d_ * ETF_SELL)
    nav = (1 + r).cumprod()
    h = len(r) // 2
    ann = lambda x: (1 + x).prod() ** (12 / len(x)) - 1  # noqa: E731
    entries = idx[(d_ > 0).values]
    fwd6 = [float((1 + r50.reindex(idx)).loc[e:].iloc[:6].prod() - 1) for e in entries if len(r50.reindex(idx).loc[e:]) >= 6]
    out.append({"條件": name, "年化": ann(r), "最大跌幅": float((nav / nav.cummax() - 1).min()), "持有時間": float(on.mean()),
                "進場次數": int((d_ > 0).sum()), "前5年": ann(r.iloc[:h]), "後5年": ann(r.iloc[h:]),
                "進場後 6 個月平均": np.mean(fwd6) if fwd6 else np.nan})
B = pd.DataFrame(out)
fb = B.copy()
for c in ("年化", "最大跌幅", "持有時間", "前5年", "後5年", "進場後 6 個月平均"):
    fb[c] = fb[c].map(lambda v: "" if pd.isna(v) else f"{v:+.1%}" if c != "持有時間" else f"{v:.0%}")
print(fb.to_string(index=False))
B.to_csv(OUT / "chips_entry_market.csv", index=False)
print("\n最近 6 個月的狀態（月底）：")
st = pd.DataFrame({"站上均線": trend, "外資當月(億)": mkt["fv"].round(0), "外資3個月(億)": mkt["fv"].rolling(3).sum().round(0),
                   "投信當月(億)": mkt["tv"].round(0)}).loc["2026-05-31":"2026-10-31"]
print(st.to_string())
