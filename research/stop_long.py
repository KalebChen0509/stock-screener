"""【2026/11/14】停損規則的長歷史檢驗（2006～2026，含 2008 金融海嘯、2011、2015、2022），獨立重寫、不共用回測引擎。

為什麼要做：正式回測只有 2016-10～2026-09，這段期間的大跌都是 V 型（很快彈回）。「每日停損比月底檢查差」會不會只是因為沒遇到長空頭？
做法（代理組合，不是正式的成長版：沒有 2016 年以前的財報，只能用股價）：
  可選股＝每月底近 60 日平均成交值前 300 名、股價 ≥10 元、上市滿一年、排除金融
  選股＝12-1 動能前 N 檔（預設 20，跟成長版 max_holdings 一樣），等權重，每月底調整；已持有的掉出前 2N 名才換（跟正式設定的緩衝一樣）
  停損＝跟正式設定一樣的「移動停損依波動」：幅度＝近 12 個月月報酬標準差 ×2.5（下限 8%、上限 35%），從買進後最高價算
        每日檢查：收盤跌破 → 隔一個交易日收盤賣出，當月剩下的日子放現金；冷卻 3 個月
        月底檢查：只在月底看（最高價也只用月底價）；冷卻 3 個月
        不停損
  成本：買 0.1425%、賣 0.4425%（含證交稅）。現金 0%。
限制（都寫在輸出裡）：
  - **2016 年以前沒有下市公司**（只有現在還在的公司＋2016 年後下市的）→ 倖存者偏差：絕對報酬偏高，而且對停損不利
    （會一路跌到下市的股票正是停損最有用的地方，但它們不在資料裡）。
  - Yahoo 台股早期的還原價／成交量品質較差；超過漲跌幅的錯價已用 prices.repair_tw_jumps 修掉。
    SCREENER_CACHE=<data 分支>/cache .venv/bin/python research/stop_long.py
"""
import os
import pickle
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _paths import OUT  # noqa: E402

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from screener import prices  # noqa: E402

BUY, SELL = 0.001425, 0.004425
VOL_MULT, VOL_MIN, VOL_MAX, COOL = 2.5, 0.08, 0.35, 3
UNIVERSE_N = 300
D = OUT / "stop_long"
D.mkdir(exist_ok=True)


def load():
    g = pickle.load(open(OUT / "tw_daily_long.pkl", "rb"))
    ok = [t for t, s in g["close"].items() if len(s)]

    def frame(k):
        d = pd.DataFrame({t: g[k][t] for t in ok})
        d.index = pd.DatetimeIndex(d.index).tz_localize(None)
        return d.sort_index()
    close, adj, vol = frame("close"), frame("adj"), frame("vol")
    adj, fixed = prices.repair_tw_jumps(adj)
    print(f"日線：{adj.shape[1]} 檔、{adj.index[0].date()}～{adj.index[-1].date()}；修錯價 {len(fixed)} 檔 {sum(len(v) for v in fixed.values())} 天")
    return close, adj, vol


def simulate(adj, elig, mom, mends, mode: str, n: int, mvol) -> pd.DataFrame:
    """mode：daily／monthly／none。回傳每月 (ret, n_stops, turnover)。"""
    held = {}                 # tk -> {"w", "ref", "pct"}
    cooldown = {}             # tk -> 最早可再買的調整序號
    rows = []
    for i, t in enumerate(mends[:-1]):
        t1 = mends[i + 1]
        px_t = adj.loc[:t].iloc[-1]
        n_stop = 0
        # ---- 月底檢查停損
        if mode == "monthly":
            for tk in list(held):
                p = px_t.get(tk, np.nan)
                h = held[tk]
                if np.isfinite(p) and p / h["ref"] - 1 <= -h["pct"]:
                    cooldown[tk] = i + COOL
                    h["stopped"] = True
                    n_stop += 1
        # ---- 選股（有緩衝）
        rk = mom.loc[t].where(elig.loc[t]).dropna().sort_values(ascending=False)
        blocked = {tk for tk, u in cooldown.items() if i < u}
        rk = rk[[tk for tk in rk.index if tk not in blocked]]
        rank = {tk: j + 1 for j, tk in enumerate(rk.index)}
        keep = [tk for tk, h in held.items() if not h.get("stopped") and rank.get(tk, 10 ** 9) <= 2 * n]
        new = keep + [tk for tk in rk.index if tk not in keep][:max(0, n - len(keep))]
        w_tgt = {tk: 1.0 / len(new) for tk in new} if new else {}
        # ---- 成本（權重變化）
        prev = {tk: h["w"] for tk, h in held.items()}
        cost = sum(max(w_tgt.get(tk, 0) - prev.get(tk, 0), 0) * BUY + max(prev.get(tk, 0) - w_tgt.get(tk, 0), 0) * SELL
                   for tk in set(prev) | set(w_tgt))
        turn = sum(abs(w_tgt.get(tk, 0) - prev.get(tk, 0)) for tk in set(prev) | set(w_tgt)) / 2
        nh = {}
        for tk in new:
            p = px_t.get(tk, np.nan)
            if tk in held and not held[tk].get("stopped"):
                h = held[tk]
                ref = max(h["ref"], p) if (mode == "monthly" and np.isfinite(p)) else h["ref"]
                nh[tk] = {"w": w_tgt[tk], "ref": ref, "pct": h["pct"]}
            else:
                sd = mvol.loc[t].get(tk, np.nan)
                pct = float(np.clip(VOL_MULT * sd, VOL_MIN, VOL_MAX)) if np.isfinite(sd) else 0.15
                nh[tk] = {"w": w_tgt[tk], "ref": p, "pct": pct}
        held = nh
        # ---- 持有一個月
        seg = adj.loc[(adj.index > t) & (adj.index <= t1)]
        port, nxt = 0.0, {}
        for tk, h in held.items():
            p0 = px_t.get(tk, np.nan)
            path = seg[tk].dropna() if tk in seg else pd.Series(dtype=float)
            if not np.isfinite(p0) or path.empty:
                nxt[tk] = dict(h)
                continue
            r, stopped = path.iloc[-1] / p0 - 1, False
            if mode == "daily":
                ref = h["ref"]
                for j, p in enumerate(path.values):
                    ref = max(ref, p)
                    if p / ref - 1 <= -h["pct"]:
                        full = adj[tk].loc[path.index[0]:].dropna()
                        p_exit = float(full.iloc[min(j + 1, len(full) - 1)])
                        r = (p_exit / p0) * (1 - SELL) - 1
                        stopped = True
                        cooldown[tk] = i + 1 + COOL
                        n_stop += 1
                        break
                h["ref"] = ref
            port += h["w"] * r
            if not stopped:
                nxt[tk] = {"w": h["w"] * (1 + r), "ref": h["ref"], "pct": h["pct"]}
        tot = 1 + port
        for h in nxt.values():
            h["w"] /= tot
        held = nxt
        rows.append({"month": t1, "ret": (1 - cost) * (1 + port) - 1, "stops": n_stop, "turnover": turn, "n": len(new)})
    return pd.DataFrame(rows).set_index("month")


def st(r):
    nav = (1 + r).cumprod()
    return {"年化": nav.iloc[-1] ** (12 / len(r)) - 1, "最大跌幅": float((nav / nav.cummax() - 1).min()),
            "最差單月": float(r.min())}


def main():
    close, adj, vol = load()
    idx_days = adj["^TWII"].dropna().index if "^TWII" in adj else adj.index
    mends = list(pd.Series(idx_days, index=idx_days).groupby(idx_days.to_period("M")).last())
    mends = [m for m in mends if m < pd.Timestamp.today().normalize() - pd.offsets.MonthEnd(1) + pd.Timedelta(days=1)]
    stocks = [c for c in adj.columns if c not in ("0050.TW", "^TWII")]
    tv = (close[stocks] * vol[stocks]).rolling(60, min_periods=40).mean()
    me = pd.DatetimeIndex(mends)
    tv_m, cl_m, adj_m = tv.reindex(me, method="ffill"), close[stocks].reindex(me, method="ffill"), adj[stocks].reindex(me, method="ffill")
    age = adj[stocks].notna().cumsum().reindex(me, method="ffill")
    live = adj[stocks].notna().rolling(10, min_periods=1).sum().reindex(me, method="ffill") >= 5      # 最近 10 天有交易
    elig = (tv_m.rank(axis=1, ascending=False) <= UNIVERSE_N) & (cl_m >= 10) & (age >= 250) & live
    mom = adj_m.shift(1) / adj_m.shift(12) - 1                       # 12-1 動能
    mvol = adj_m.pct_change(fill_method=None).rolling(12, min_periods=6).std()
    start = me[13]
    mends = [m for m in mends if m >= start]
    print(f"回測：{mends[0]:%Y-%m}～{mends[-1]:%Y-%m}；每月可選股中位數 {int(elig.loc[start:].sum(axis=1).median())} 檔")
    b0050 = adj["0050.TW"].reindex(me, method="ffill").pct_change(fill_method=None)
    twii = adj["^TWII"].reindex(me, method="ffill").pct_change(fill_method=None)
    res = {}
    for n in (20, 10):
        for mode, name in (("daily", "每日停損"), ("monthly", "月底檢查"), ("none", "不停損")):
            res[(n, name)] = simulate(adj, elig, mom, mends, mode, n, mvol)
            print(f"N={n} {name} 完成（停損 {int(res[(n, name)]['stops'].sum())} 次）", flush=True)
    pickle.dump(res, open(D / "runs.pkl", "wb"))
    spans = [("全部", mends[1], mends[-1]), ("2007～2015（正式回測之前）", mends[1], pd.Timestamp("2015-12-31")),
             ("2016～2026", pd.Timestamp("2016-01-01"), mends[-1])]
    rows = []
    for n in (20, 10):
        for sp, a, b in spans:
            for name in ("每日停損", "月底檢查", "不停損"):
                r = res[(n, name)]["ret"].loc[a:b]
                rows.append({"檔數": n, "期間": sp, "方案": name, **st(r), "停損次數": int(res[(n, name)]["stops"].loc[a:b].sum())})
            for bn, br in (("0050 含息", b0050), ("加權指數（不含息）", twii)):
                r = br.loc[a:b].dropna()
                if len(r) > 24:
                    rows.append({"檔數": n, "期間": sp, "方案": bn, **st(r), "停損次數": 0})
    R = pd.DataFrame(rows)
    f = R.copy()
    for c in ("年化", "最大跌幅", "最差單月"):
        f[c] = f[c].map(lambda v: f"{v:+.1%}")
    pd.set_option("display.width", 220)
    print("\n==== 動能代理組合：三種停損做法 ====")
    print(f.to_string(index=False))
    R.to_csv(D / "summary.csv", index=False)

    def ann(x):
        return (1 + x).prod() ** (12 / len(x)) - 1
    print("\n==== 配對比較（月報酬相減）====")
    for n in (20, 10):
        for sp, a, b in spans:
            for x, y in (("月底檢查", "每日停損"), ("不停損", "每日停損"), ("不停損", "月底檢查")):
                rx, ry = res[(n, x)]["ret"].loc[a:b], res[(n, y)]["ret"].loc[a:b]
                d = rx - ry
                print(f"N={n} {sp:<16} {x}−{y}：年化差 {ann(rx) - ann(ry):+.1%}、t {d.mean() / (d.std() / np.sqrt(len(d))):+.2f}")
    print("\n==== 逐年（N=20）====")
    Y = pd.DataFrame({k: (1 + res[(20, k)]["ret"]).groupby(res[(20, k)].index.year).prod() - 1 for k in ("每日停損", "月底檢查", "不停損")})
    Y["加權指數"] = (1 + twii.loc[mends[1]:]).groupby(twii.loc[mends[1]:].index.year).prod() - 1
    Y["月底−每日"] = Y["月底檢查"] - Y["每日停損"]
    print(Y.map(lambda v: f"{v:+.1%}").to_string())
    # 幾次大跌期間（加權指數月底高點→低點）
    print("\n==== 大跌期間各方案的報酬（N=20）====")
    for nm, a, b in (("2008 金融海嘯", "2007-10-31", "2008-11-30"), ("2011 歐債", "2011-01-31", "2011-12-31"),
                     ("2015 股災", "2015-04-30", "2015-08-31"), ("2018Q4", "2018-09-30", "2018-12-31"),
                     ("2020 疫情", "2019-12-31", "2020-03-31"), ("2022 升息", "2021-12-31", "2022-10-31"),
                     ("2025-04 關稅", "2025-02-28", "2025-04-30")):
        seg = lambda r: float((1 + r.loc[pd.Timestamp(a) + pd.Timedelta(days=1):pd.Timestamp(b) + pd.Timedelta(days=3)]).prod() - 1)  # noqa: E731
        after = lambda r: float((1 + r.loc[pd.Timestamp(b) + pd.Timedelta(days=4):pd.Timestamp(b) + pd.DateOffset(months=12)]).prod() - 1)  # noqa: E731
        print(f"{nm}：" + "、".join(f"{k} {seg(res[(20, k)]['ret']):+.0%}" for k in ("每日停損", "月底檢查", "不停損")) + f"、加權指數 {seg(twii):+.0%}"
              + "｜之後 12 個月：" + "、".join(f"{k} {after(res[(20, k)]['ret']):+.0%}" for k in ("每日停損", "月底檢查", "不停損")))


if __name__ == "__main__":
    main()
