"""【2026/11/14】stop_verify.py 的第二步：每日停損 vs 月底檢查的差異是不是真的、差在哪裡。

1. 顯著性：兩個方案每月報酬的差（同一段期間配對），平均、t 值、贏的月份比例；前後半；逐年。
2. 每日停損的 389 次：觸發前幾天有沒有超過漲跌幅的錯價（假停損）；賣出後 1／3／6 個月那檔股票 vs 大盤的走勢。
3. 一個波段裡停損集中在哪些月份（V 型急跌）。
    .venv/bin/python research/stop_verify_analyze.py
"""
import os
import pickle
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _paths import OUT  # noqa: E402

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

D = OUT / "stop_verify"
runs = pickle.load(open(D / "runs.pkl", "rb"))
dd = pickle.load(open(D / "daily.pkl", "rb"))
daily, fixlog = dd["daily"], dd["fixlog"]


def ann(x):
    return (1 + x).prod() ** (12 / len(x)) - 1


def paired(a_name, b_name, col):
    a, b = runs[a_name]["monthly"][col], runs[b_name]["monthly"][col]
    d = (a - b).dropna()
    t = d.mean() / (d.std() / np.sqrt(len(d)))
    h = len(d) // 2
    return {"比較": f"{a_name} − {b_name}", "欄位": col, "年化差": ann(a) - ann(b), "月平均差": d.mean(), "t值": t,
            "贏的月份": float((d > 0).mean()), "不同的月份": int((d.abs() > 1e-9).sum()),
            "前半年化差": ann(a.iloc[:h]) - ann(b.iloc[:h]), "後半年化差": ann(a.iloc[h:]) - ann(b.iloc[h:])}


pd.set_option("display.width", 250)
rows = []
for col in ("ret_net", "sleeve_tw", "sleeve_us"):
    rows.append(paired("成長｜月底檢查（check: monthly）", "成長｜每日停損（現行）", col))
    rows.append(paired("成長｜不停損", "成長｜每日停損（現行）", col))
    rows.append(paired("成長｜不停損", "成長｜月底檢查（check: monthly）", col))
    rows.append(paired("價值｜每日停損（現行）", "價值｜月底檢查（check: monthly）", col))
    rows.append(paired("價值｜每日停損（現行）", "價值｜不停損", col))
P = pd.DataFrame(rows)
f = P.copy()
for c in ("年化差", "月平均差", "前半年化差", "後半年化差"):
    f[c] = f[c].map(lambda v: f"{v:+.2%}")
f["t值"] = f["t值"].map(lambda v: f"{v:.2f}")
f["贏的月份"] = f["贏的月份"].map(lambda v: f"{v:.0%}")
print("==== 1. 配對比較（同月份報酬相減；ret_net＝整個組合扣費後，sleeve_tw／us＝台股／美股個股部位）====")
print(f.to_string(index=False))
P.to_csv(D / "paired.csv", index=False)

print("\n==== 逐年：成長版 整個組合（扣費後）====")
names = ["成長｜每日停損（現行）", "成長｜月底檢查（check: monthly）", "成長｜不停損"]
Y = pd.DataFrame({n.split("｜")[1]: (1 + runs[n]["monthly"]["ret_net"]).groupby(runs[n]["monthly"].index.year).prod() - 1 for n in names})
Y["月底−每日"] = Y.iloc[:, 1] - Y.iloc[:, 0]
print(Y.map(lambda v: f"{v:+.1%}").to_string())
print("\n==== 逐年：成長版 台股個股部位 ====")
Y = pd.DataFrame({n.split("｜")[1]: (1 + runs[n]["monthly"]["sleeve_tw"]).groupby(runs[n]["monthly"].index.year).prod() - 1 for n in names})
Y["月底−每日"] = Y.iloc[:, 1] - Y.iloc[:, 0]
print(Y.map(lambda v: f"{v:+.1%}").to_string())

# ---------------------------------------------------------------- 2. 每一次每日停損
S = pd.DataFrame(runs["成長｜每日停損（現行）"]["stops"])
S["date"] = pd.to_datetime(S["date"])
spells = pd.DataFrame(runs["成長｜每日停損（現行）"]["spells"])
hold = runs["成長｜每日停損（現行）"]["holdings"]
tk_of = {}
for h in hold:
    for w in h.get("weights", []):
        if w.get("id"):
            tk_of[(w["mkt"], w["id"])] = w["ticker"]
S["tk"] = [tk_of.get((m, i)) for m, i in zip(S["market"], S["id"])]
bench = {"tw": "0050.TW", "us": "SPY"}
lim = {"tw": 0.105, "us": 9.9}
out = []
for _, s in S.iterrows():
    tk = s["tk"]
    if tk not in daily or not s["daily"]:
        continue
    px = daily[tk].dropna()
    if s["date"] not in px.index:
        continue
    j = px.index.get_loc(s["date"])
    r5 = px.iloc[max(0, j - 6):j + 1].pct_change().dropna()
    bad = bool((r5.abs() > lim[s["market"]]).any()) or (tk in fixlog and any(abs((pd.Timestamp(x) - s["date"]).days) <= 10 for x in fixlog[tk]))
    b = daily[bench[s["market"]]].dropna()
    row = {"date": s["date"], "market": s["market"], "tk": tk, "從高點": s["ret_from_ref"], "疑似錯價": bad}
    for n, k in ((21, "1個月"), (63, "3個月"), (126, "6個月")):
        if j + n < len(px):
            d1 = px.index[j + n]
            row[k] = px.iloc[j + n] / px.iloc[j] - 1
            b0, b1 = b.loc[:s["date"]], b.loc[:d1]
            row[k + "大盤"] = b1.iloc[-1] / b0.iloc[-1] - 1 if len(b0) and len(b1) else np.nan
    out.append(row)
E = pd.DataFrame(out)
E.to_csv(D / "daily_stops.csv", index=False)
print(f"\n==== 2. 成長版每日停損 {len(S)} 次（有日線可分析 {len(E)} 次）====")
print(f"觸發前後疑似錯價（台股單日超過 ±10.5%，或在修錯價清單 10 天內）：{int(E['疑似錯價'].sum())} 次")
for mkt, g in E.groupby("market"):
    print(f"\n{'台股' if mkt == 'tw' else '美股'}：{len(g)} 次，賣出時平均已從高點 {g['從高點'].mean():+.1%}")
    for k in ("1個月", "3個月", "6個月"):
        x = g.dropna(subset=[k, k + "大盤"])
        ex = x[k] - x[k + "大盤"]
        print(f"  賣出後 {k}：那檔股票平均 {x[k].mean():+.1%}（中位數 {x[k].median():+.1%}）、同期大盤 {x[k + '大盤'].mean():+.1%}、"
              f"相對大盤 {ex.mean():+.1%}（t {ex.mean() / (ex.std() / np.sqrt(len(ex))):.1f}；{(x[k] > 0).mean():.0%} 上漲、{(ex > 0).mean():.0%} 贏大盤；n={len(x)}）")

# ---------------------------------------------------------------- 3. 停損集中在哪些月份
E["ym"] = E["date"].dt.to_period("M")
top = E.groupby("ym").size().sort_values(ascending=False).head(12)
tot = len(E)
print(f"\n==== 3. 停損最多的 12 個月（占全部 {top.sum() / tot:.0%}）====")
tw = daily["0050.TW"].dropna()
for ym, n in top.items():
    g = E[E["ym"] == ym]
    seg = tw.loc[str(ym)]
    m3 = g["3個月"].mean() if "3個月" in g else np.nan
    print(f"{ym}：{n} 次；當月 0050 {seg.iloc[-1] / tw.loc[:seg.index[0]].iloc[-2] - 1:+.1%}；這些股票賣出後 3 個月平均 {m3:+.1%}")
# 把停損最集中的月份拿掉，其他時候的停損有沒有用
rest = E[~E["ym"].isin(top.index)]
x = rest.dropna(subset=["3個月", "3個月大盤"])
print(f"\n其他月份的停損 {len(rest)} 次：賣出後 3 個月平均 {x['3個月'].mean():+.1%}、相對大盤 {(x['3個月'] - x['3個月大盤']).mean():+.1%}")
