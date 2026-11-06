"""【2026/11/06】先跑 sleeve_runs.py。選股部位（2016～2026）：停損 vs 大盤趨勢避險（月底判斷，下個月生效）。
台股部位看加權指數、美股部位看 SPY；做法：換現金（台股含證交稅）或放空台指期（只有台股）。"""
import os  # noqa: F401
import sys  # noqa: F401
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _paths import CACHE, OUT, ROOT  # noqa: E402,F401
import pickle
import sys

import numpy as np
import pandas as pd

from screener import derivs  # noqa: E402

S, C = str(OUT), CACHE
runs = pickle.load(open(S + "/sleeves.pkl", "rb"))
sd = pd.read_csv(C + "/stress_daily.csv.gz", parse_dates=["date"]).pivot_table(index="date", columns="ticker", values="adj_close").sort_index()
fut = pd.read_csv(C + "/derivs/fut_daily.csv.gz", parse_dates=["date"], dtype={"expiry": str})
rf_d = derivs.front_returns(fut, "TX")


def signals(px: pd.Series) -> dict:
    px = px.dropna()
    me = px.resample("ME").last()
    ma200 = px.rolling(200).mean().resample("ME").last()
    return {"月底跌破 10 個月均線": me < me.rolling(10).mean(), "月底跌破 200 日均線": me < ma200}


SIG = {"tw": signals(sd["^TWII"]), "us": signals(sd["SPY"])}
COST = {"tw": (0.004425, 0.001425), "us": (0.0013, 0.0013)}    # (降低曝險＝賣, 恢復＝買)
rf_m = (1 + rf_d).resample("ME").prod() - 1


def overlay(r: pd.Series, mkt: str, sig: pd.Series, h: float, how: str) -> pd.Series:
    # 報酬列的索引是「持有月份的月底」，訊號用前一個月底
    on = sig.shift(1).reindex(r.index).fillna(False).astype(float) * h
    d = on.diff().fillna(on.iloc[0])
    if how == "期貨":
        return r - on * rf_m.reindex(r.index).fillna(0) - d.abs() * 0.0001
    sell, buy = COST[mkt]
    return (1 - on) * r - np.where(d > 0, d * sell, -d * buy)


def st(r):
    nav = (1 + r).cumprod()
    cagr = nav.iloc[-1] ** (12 / len(r)) - 1
    v = r.std() * np.sqrt(12)
    return cagr, float((nav / nav.cummax() - 1).min()), cagr / v


EP = {"2018Q4": ("2018-10", "2018-12"), "2020/2-3": ("2020-02", "2020-03"), "2022": ("2022-01", "2022-10"),
      "2024/7-8": ("2024-07", "2024-08"), "2025/3-4": ("2025-03", "2025-04")}


def ep(r):
    return " ".join(f"{(1 + r.loc[a:b]).prod() - 1:>7.1%}" for a, b in EP.values())


for mkt, col, bm in (("tw", "sleeve_tw", "bench_0050.TW"), ("us", "sleeve_us", "bench_SPY")):
    print(f"\n==== {'台股' if mkt == 'tw' else '美股'}選股部位（2016-10～2026-09；比 {bm[6:]}）====")
    print(f"{'版本＋避險':<46}{'年化':>7}{'最大跌':>8}{'報酬/波動':>8}   " + " ".join(f"{k:>7}" for k in EP))
    b = next(iter(runs.values()))[bm]
    c_, m_, s_ = st(b)
    print(f"{'大盤 ' + bm[6:]:<46}{c_:>7.1%}{m_:>8.1%}{s_:>8.2f}   {ep(b)}")
    for name, df in runs.items():
        r = df[col]
        c_, m_, s_ = st(r)
        print(f"{name:<46}{c_:>7.1%}{m_:>8.1%}{s_:>8.2f}   {ep(r)}")
        if "不停損" in name:
            for sname, sg in SIG[mkt].items():
                for how in (("現金", "期貨") if mkt == "tw" else ("現金",)):
                    for h in (0.5, 1.0):
                        rr = overlay(r, mkt, sg, h, how)
                        c_, m_, s_ = st(rr)
                        lab = f"  ＋{sname}→{how}{int(h * 100)}%"
                        print(f"{lab:<46}{c_:>7.1%}{m_:>8.1%}{s_:>8.2f}   {ep(rr)}")
print("\n訊號在最近幾個月底的狀態：")
for mkt in SIG:
    print(mkt, {k: v.iloc[-4:].astype(int).tolist() for k, v in SIG[mkt].items()}, "（月份：", [d.strftime("%Y-%m") for d in SIG[mkt]["月底跌破 10 個月均線"].index[-4:]], "）")
