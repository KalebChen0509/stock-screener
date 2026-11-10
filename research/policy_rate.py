"""【2026/11/10】政策利率（升息／降息循環）對台股、美股有沒有預測力？（使用者：課堂上學到隔夜拆款利率；顯示＋通知＋回測＋當閒置資金報酬）

規則在看結果前寫死（2026-11-10）：
    狀態：screener.macro_ind.states("policy")：政策利率 10 日中位數跟 6 個月前比，≥+0.10 升息循環、≤−0.10 降息循環、變動 ≤0.03 持平。
          台灣＝央行重貼現率（2000-12 起）；美國＝聯準會目標利率（區間上限，2000-07 起；紐約聯準銀行）。
    A 升息時避開：月底是「升息循環」→ 下個月放現金，否則持有指數。
    B 降息時避開：月底是「降息循環」→ 下個月放現金。
    C 趨勢或利率：大盤站上 10 個月均線，或利率狀態不是升息循環 → 持有（只有兩個都不好才放現金）。
    事件研究：每次剛轉成升息／降息／持平之後 6、12、24 個月的指數報酬 vs 任意月份。
    現金報酬＝隔夜利率（台灣金融業隔夜拆款、美國 EFFR）；指數不含息；扣 ETF 進出成本。
組合：台灣利率→加權指數、美國利率→加權指數（聯準會對台股的影響）、美國利率→S&P 500。
執行：.venv/bin/python research/policy_rate.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _paths import OUT  # noqa: E402

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from macro_signals import load_index, stats  # noqa: E402
from screener import macro_ind as M  # noqa: E402

COST_IN, COST_OUT = 0.001425, 0.002425


def month_state(s: pd.Series) -> pd.Series:
    st = M.states("policy", s).dropna()
    m = st.groupby(st.index.to_period("M")).last()
    m.index = m.index.to_timestamp("M")
    return m


def cash_monthly(daily_rate: pd.Series) -> pd.Series:
    """隔夜利率（年 %）→ 每月現金報酬。"""
    m = daily_rate.groupby(daily_rate.index.to_period("M")).mean()
    m.index = m.index.to_timestamp("M")
    return m / 100 / 12


def strat(on: pd.Series, r: pd.Series, cash: pd.Series) -> pd.Series:
    pos = on.shift(1).fillna(1.0)
    d = pos.diff().fillna(0)
    return pos * r + (1 - pos) * cash.reindex(r.index).ffill().fillna(0) - np.where(d > 0, d * COST_IN, -d * COST_OUT)


def main():
    tw_pol, us_pol = M.fetch_cbc_policy(), M.fetch_nyfed("unsecured/effr#target")
    tw_on, us_on = M.fetch_cbc_overnight(), M.fetch_nyfed("unsecured/effr")
    px = load_index(["^TWII", "^GSPC"])
    combos = [("台灣政策利率", tw_pol, tw_on, "^TWII"), ("美國政策利率", us_pol, us_on, "^TWII"), ("美國政策利率", us_pol, us_on, "^GSPC")]
    rows, ev = [], []
    for name, pol, on, tk in combos:
        st = month_state(pol)
        p = px[tk].dropna()
        idx = p.index[(p.index >= st.index.min()) & (p.index >= p.index[10])]
        r = p.pct_change().reindex(idx).fillna(0)
        cash = cash_monthly(on)
        s_ = st.reindex(idx).ffill()
        trend = (p > p.rolling(10).mean()).reindex(idx).astype(float)
        plans = {"永遠持有": pd.Series(1.0, index=idx), "只看趨勢": trend,
                 "A 升息時避開": (s_ != "升息循環").astype(float), "B 降息時避開": (s_ != "降息循環").astype(float),
                 "C 趨勢或非升息": ((trend > 0) | (s_ != "升息循環")).astype(float)}
        h = len(idx) // 2
        for pn, on_ in plans.items():
            ret = strat(on_, r, cash)
            s0 = stats(ret)
            rows.append({"利率": name, "市場": tk, "期間": f"{idx[0]:%Y-%m}～{idx[-1]:%Y-%m}", "方案": pn,
                         "年化": s0["年化"], "最大跌幅": s0["最大跌幅"], "持有時間": float(on_.shift(1).fillna(1).mean()),
                         "前半": stats(ret.iloc[:h])["年化"], "後半": stats(ret.iloc[h:])["年化"]})
        # 事件研究
        first = s_[s_ != s_.shift(1)].iloc[1:]
        for months in (6, 12, 24):
            fwd = p.shift(-months) / p - 1
            base = fwd.reindex(idx).dropna().mean()
            for lab in ("升息循環", "降息循環", "持平"):
                ds = first[first == lab].index
                v = fwd.reindex(ds).dropna()
                ev.append({"利率": name, "市場": tk, "轉成": lab, "之後": f"{months} 個月", "次數": len(v),
                           "平均": v.mean() if len(v) else np.nan, "上漲比例": (v > 0).mean() if len(v) else np.nan,
                           "任意月份平均": base, "各次": "、".join(f"{d:%Y-%m}:{x:+.0%}" for d, x in v.items())})
    R = pd.DataFrame(rows)
    E = pd.DataFrame(ev)
    f = R.copy()
    for c in ("年化", "最大跌幅", "前半", "後半"):
        f[c] = f[c].map(lambda v: f"{v:+.1%}")
    f["持有時間"] = f["持有時間"].map(lambda v: f"{v:.0%}")
    pd.set_option("display.width", 250)
    pd.set_option("display.max_colwidth", 200)
    for (n, tk), g in f.groupby(["利率", "市場"], sort=False):
        print(f"\n==== {n} → {tk}（{g['期間'].iloc[0]}；現金＝隔夜利率）====")
        print(g[["方案", "年化", "最大跌幅", "持有時間", "前半", "後半"]].to_string(index=False))
    e = E.copy()
    for c in ("平均", "任意月份平均"):
        e[c] = e[c].map(lambda v: "" if pd.isna(v) else f"{v:+.1%}")
    e["上漲比例"] = e["上漲比例"].map(lambda v: "" if pd.isna(v) else f"{v:.0%}")
    print("\n==== 事件研究：剛轉成某狀態之後的指數報酬 ====")
    print(e[e["之後"] == "12 個月"][["利率", "市場", "轉成", "次數", "平均", "上漲比例", "任意月份平均", "各次"]].to_string(index=False))
    print(e[e["之後"] != "12 個月"][["利率", "市場", "轉成", "之後", "次數", "平均", "上漲比例", "任意月份平均"]].to_string(index=False))
    R.to_csv(OUT / "policy_rate_timing.csv", index=False)
    E.to_csv(OUT / "policy_rate_events.csv", index=False)


if __name__ == "__main__":
    main()
