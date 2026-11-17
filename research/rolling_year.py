"""【2026/11/17】滾動一年：10 年回測（2016-10～2026-09）裡，每個月當起點各算一年，看「任選一年」會是什麼結果。

資料：正式回測的結果檔 backtest.json（data 分支 site_data/）裡各方案的逐月淨值（扣費後，每月底一筆），不重跑回測。
規則事先定好：12 個月視窗、每個月滑動一次；比較對象＝只買 ETF（使用者的合格門檻）、0050 含息、SPY 含息（台幣）。
注意：相鄰的視窗有 11 個月重疊，所以 109 個視窗其實只有約 10 個「獨立的一年」；比例要當成大概，不是精確機率。
    SCREENER_CACHE=<data 分支>/cache .venv/bin/python research/rolling_year.py
"""
import json
import os
import pickle
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _paths import CACHE, OUT  # noqa: E402

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

D = OUT / "rolling_year"
D.mkdir(exist_ok=True)
VERS = ["成長動能（單檔上限 10%）", "成長動能（上限 10%）＋月底檢查停損", "大盤一半＋成長動能一半",
        "核心＋衛星（動能＋財報驚喜 10 檔，衛星 30%）", "只買 ETF（VT50＋0050 25＋SPY 25）", "目前版（買進價停損15%）"]
SHORT = {VERS[0]: "成長動能", VERS[1]: "成長（月底停損）", VERS[2]: "大盤半＋成長半", VERS[3]: "核心＋衛星",
         VERS[4]: "只買ETF", VERS[5]: "目前版（價值）", "0050.TW": "0050", "SPY": "SPY台幣"}
ETF = "只買ETF"


def main():
    b = json.load(open(os.path.join(os.path.dirname(CACHE), "site_data", "backtest.json")))
    idx = pd.PeriodIndex(b["months"], freq="M").to_timestamp("M")
    cap = float(b.get("initial_capital", 1_000_000))
    nav = {SHORT[v]: pd.Series(b["variants"][v]["nav"], index=idx, dtype=float) for v in VERS}
    for k in ("0050.TW", "SPY"):
        nav[SHORT[k]] = pd.Series(b["nav"][k], index=idx, dtype=float)
    N = pd.DataFrame(nav)
    R = pd.concat([N.iloc[:1] / cap - 1, N.pct_change().iloc[1:]])          # 逐月報酬
    print(f"回測：{idx[0]:%Y-%m}～{idx[-1]:%Y-%m}（{len(idx)} 個月；結果檔產生於 {b['generated']}）")

    # 跟「過去一年」回測（research/out/last_year）對照重疊的月份，確認兩邊算法一致
    ly = OUT / "last_year" / "full.pkl"
    if ly.exists():
        full = pickle.load(open(ly, "rb"))["full"]
        a, z = "2025-11-30", idx[-1]
        print(f"對照過去一年回測（{a[:7]}～{z:%Y-%m} 重疊月份累積報酬）：")
        for v in VERS:
            x = (1 + R[SHORT[v]].loc[a:z]).prod() - 1
            y = (1 + full[v]["monthly"]["ret_net"].loc[a:z]).prod() - 1
            print(f"  {SHORT[v]:<12} 10 年回測 {x:+.1%}／過去一年回測 {y:+.1%}")

    W = (1 + R).rolling(12).apply(np.prod, raw=True).dropna() - 1          # 結尾月份 → 那 12 個月的報酬
    W.index.name = "結束月"
    W.to_csv(D / "rolling12.csv")
    print(f"\n滾動一年：{len(W)} 個視窗（第一個 {W.index[0] - pd.offsets.MonthEnd(11):%Y-%m}～{W.index[0]:%Y-%m}，"
          f"最後一個 {W.index[-1] - pd.offsets.MonthEnd(11):%Y-%m}～{W.index[-1]:%Y-%m}）\n")

    rows = []
    for c in W.columns:
        w = W[c]
        ex = w - W[ETF]
        rows.append({"方案": c, "中位數": w.median(), "最差": w.min(), "最差的一年": f"{w.idxmin() - pd.offsets.MonthEnd(11):%Y-%m}～{w.idxmin():%Y-%m}",
                     "最好": w.max(), "賠錢的比例": (w < 0).mean(), "贏只買ETF的比例": (ex > 0).mean() if c != ETF else np.nan,
                     "比只買ETF 中位數差": ex.median() if c != ETF else np.nan, "輸最多": ex.min() if c != ETF else np.nan,
                     "贏0050的比例": (w > W["0050"]).mean() if c != "0050" else np.nan})
    T = pd.DataFrame(rows)
    f = T.copy()
    for c in ("中位數", "最差", "最好", "比只買ETF 中位數差", "輸最多"):
        f[c] = f[c].map(lambda v: "" if pd.isna(v) else f"{v:+.1%}")
    for c in ("賠錢的比例", "贏只買ETF的比例", "贏0050的比例"):
        f[c] = f[c].map(lambda v: "" if pd.isna(v) else f"{v:.0%}")
    pd.set_option("display.width", 250)
    print("==== 任選一年（12 個月）的結果分布 ====")
    print(f.to_string(index=False))
    T.to_csv(D / "summary.csv", index=False)

    print("\n==== 每年（日曆年 1～12 月；2016 只有 10～12 月、2026 只到 9 月）====")
    Y = (1 + R).groupby(R.index.year).prod() - 1
    print(Y.map(lambda v: f"{v:+.1%}").to_string())

    print("\n==== 分市場環境：那一年 0050 漲或跌 ====")
    up = W["0050"] >= 0
    for lab, msk in (("0050 上漲的年", up), ("0050 下跌的年", ~up)):
        s = W[msk]
        print(f"{lab}（{int(msk.sum())} 個視窗）：" + "、".join(f"{c} 中位數 {s[c].median():+.0%}" for c in W.columns)
              + "；贏只買ETF的比例：" + "、".join(f"{c} {(s[c] > s[ETF]).mean():.0%}" for c in W.columns if c != ETF))

    print("\n==== 連續輸給只買 ETF 最久（以滾動一年看，連續幾個視窗都輸）====")
    for c in W.columns:
        if c == ETF:
            continue
        lose = (W[c] <= W[ETF]).astype(int)
        run, best, end = 0, 0, None
        for d, x in lose.items():
            run = run + 1 if x else 0
            if run > best:
                best, end = run, d
        print(f"{c}：最長連續 {best} 個視窗輸（到 {end:%Y-%m} 為止的一年）" if best else f"{c}：從來沒輸過")

    W3 = ((1 + R).rolling(36).apply(np.prod, raw=True).dropna()) ** (1 / 3) - 1
    print(f"\n==== 參考：滾動三年（年化，{len(W3)} 個視窗）====")
    print("、".join(f"{c} 中位數 {W3[c].median():+.1%}／最差 {W3[c].min():+.1%}／贏只買ETF {(W3[c] > W3[ETF]).mean():.0%}" for c in W3.columns if c != ETF))


if __name__ == "__main__":
    main()
