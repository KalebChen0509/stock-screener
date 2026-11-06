"""【2026/11/06】下市樣本在 cache/us_delisted（美股下市資料 workflow）。⚠ 固定前 N 名＋抽樣權重的結果被個別股主導，結論以 us_surv2.py 為準。美股倖存者偏差估計：現存股票（回測股票池）vs 現存＋抽樣下市股票（加權）。
只用股價能算的條件：股價 ≥ 5 美元、近 3 個月日均成交值 ≥ 200 萬美元。組合每月調整、等權（抽樣股乘權重）、不扣成本。"""
import os  # noqa: F401
import sys  # noqa: F401
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _paths import CACHE, OUT, ROOT  # noqa: E402,F401
import json
import sys

import numpy as np
import pandas as pd

C = CACHE
U = CACHE + "/us_delisted"
START, END = pd.Timestamp("2016-09-30"), pd.Timestamp("2026-08-31")

# ---- 現存股票（回測的美股股票池）
uni = pd.read_csv(C + "/us_universe.csv", dtype=str)
alive = set(uni["yahoo"].dropna())
m = pd.read_csv(C + "/prices_monthly.csv.gz", parse_dates=["month"])
m = m[m["ticker"].isin(alive)]
adj_a = m.pivot_table(index="month", columns="ticker", values="adj_close")
cls_a = m.pivot_table(index="month", columns="ticker", values="close")
vol_a = m.pivot_table(index="month", columns="ticker", values="volume")

# ---- 抽樣下市股票（Tiingo 日線 → 月資料）
st = json.load(open(U + "/state.json"))
NOT_COMMON = r"[A-Z]{4}[WUR]"                                     # 5 個字母、最後是 W／U／R＝權證、單位、認股權，不是普通股
cand_all = pd.read_csv(U + "/tickers.csv")
n_cand = int((~cand_all["ticker"].str.fullmatch(NOT_COMMON)).sum())
d = pd.read_csv(U + "/daily.csv.gz", parse_dates=["date"])
d = d[~d["ticker"].str.fullmatch(NOT_COMMON)]
# 資料不可靠：單日漲超過 3 倍或跌超過 75%（多半是分割沒還原）→ 整檔不用（當作隨機缺失，權重重新分配）
dr = d.sort_values(["ticker", "date"]).groupby("ticker")["adj_close"].pct_change()
bad = set(d.loc[(dr > 2.0) | (dr < -0.75), "ticker"])
d = d[~d["ticker"].isin(bad)]
n_ok = d["ticker"].nunique()
w_dead = n_cand / n_ok
print(f"排除：權證／單位等 {len(cand_all) - n_cand} 檔（候選）、日線錯價 {len(bad)} 檔：{sorted(bad)[:12]}")
d["dv"] = d["close"] * d["volume"]
g = d.set_index("date").groupby("ticker")
adj_d = g["adj_close"].resample("ME").last().unstack(0)
cls_d = g["close"].resample("ME").last().unstack(0)
dv_d = g["dv"].resample("ME").sum().unstack(0)
last_day = d.groupby("ticker")["date"].max()

idx = adj_a.index[(adj_a.index >= START - pd.offsets.MonthEnd(13)) & (adj_a.index <= END)]
adj = pd.concat([adj_a.reindex(idx), adj_d.reindex(idx)], axis=1)
cls = pd.concat([cls_a.reindex(idx), cls_d.reindex(idx)], axis=1)
tv = pd.concat([(vol_a * cls_a).reindex(idx), dv_d.reindex(idx)], axis=1)       # 每月成交值
weight = pd.Series(1.0, index=adj.columns)
dead = [c for c in adj_d.columns]
weight[dead] = w_dead


def fwd_returns(t, t1, haircut: float):
    r = adj.loc[t1] / adj.loc[t] - 1
    # 下市當月：最後成交價；悲觀情境：下市前 6 個月已腰斬的（多半是經營出問題）再打折
    for tk in dead:
        if pd.isna(r.get(tk)) or not (t < last_day[tk] <= t1):
            continue
        if haircut < 1.0:
            i = idx.get_loc(t)
            past = adj[tk].iloc[max(0, i - 6)]
            if past and adj.loc[t1, tk] / past < 0.5:
                r[tk] = (1 + r[tk]) * haircut - 1
    return r.where(r.abs() <= 3)


def top_weighted(score: pd.Series, w: pd.Series, n: float) -> pd.Series:
    """依分數由高到低取，累計權重到 n 為止（抽樣股一檔代表 w 檔）；回傳每檔實際權重。"""
    sc = score.dropna().sort_values(ascending=False)
    out, acc = {}, 0.0
    for tk in sc.index:
        take = min(w[tk], n - acc)
        if take <= 0:
            break
        out[tk] = take
        acc += take
    return pd.Series(out)


months = [t for t in idx if START <= t < END]
res = {}
for incl in (False, True):
    for haircut in ((1.0, 0.7) if incl else (1.0,)):
        port = {"全部可選股 等權": [], "動能前 20 名": [], "動能前 10 名": []}
        dead_share = []
        for t in months:
            t1 = idx[idx.get_loc(t) + 1]
            i = idx.get_loc(t)
            ok = (cls.loc[t] >= 5) & (tv.iloc[max(0, i - 2):i + 1].mean() / 21 >= 2e6) & adj.loc[t].notna()
            if not incl:
                ok[dead] = False
            mom = adj.iloc[i - 1] / adj.iloc[i - 12] - 1 if i >= 12 else pd.Series(dtype=float)
            r = fwd_returns(t, t1, haircut).fillna(0.0)
            w = weight[ok[ok].index]
            port["全部可選股 等權"].append((w * r[w.index]).sum() / w.sum())
            for name, n in (("動能前 20 名", 20), ("動能前 10 名", 10)):
                pick = top_weighted(mom[w.index], weight, n)
                port[name].append((pick * r[pick.index]).sum() / pick.sum())
                if name == "動能前 20 名":
                    dead_share.append(pick[[x for x in pick.index if x in dead]].sum() / pick.sum())
        key = "只有現存股票" if not incl else f"加上下市股票（{'最後成交價' if haircut == 1 else '腰斬者再打 7 折'}）"
        res[key] = {k: pd.Series(v, index=months) for k, v in port.items()}
        if incl:
            res[key]["_dead_share"] = float(np.mean(dead_share))


def cagr(r):
    return (1 + r).prod() ** (12 / len(r)) - 1


print(f"抽樣：{len(st['done'])} 檔有資料、{len(st['miss'])} 檔查不到；可用 {n_ok} 檔；普通股候選 {n_cand} 檔 → 每檔代表 {w_dead:.1f} 檔")
base = res["只有現存股票"]
print(f"\n{'組合':<14}" + "".join(f"{k:>26}" for k in res))
for p in base:
    vals = "".join(f"{cagr(res[k][p]):>26.1%}" for k in res)
    print(f"{p:<14}{vals}")
print("\n偏差（現存 − 加上下市；正數＝回測高估多少／年）：")
for k in list(res)[1:]:
    print(" ", k, {p: f"{cagr(base[p]) - cagr(res[k][p]):+.2%}" for p in base}, f"動能前 20 名裡下市股平均占 {res[k]['_dead_share']:.1%}")
h = len(months) // 2
k1 = list(res)[1]
print("\n前後 5 年的偏差（最後成交價情境）：", {p: (f"{cagr(base[p][:h]) - cagr(res[k1][p][:h]):+.2%}", f"{cagr(base[p][h:]) - cagr(res[k1][p][h:]):+.2%}") for p in base})
