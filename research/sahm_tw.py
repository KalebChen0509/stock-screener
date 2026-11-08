"""【2026/11/08】「大盤趨勢＋衰退指標」用在台股有沒有用？（美股結果：薩姆規則＋趨勢 10.4%／−25.6% vs 永遠持有 8.9%／−52.6%）

規則跟美股完全一樣、事先定好不調參數：
    持有 ＝ 大盤站上 10 個月均線  或  衰退指標「沒有」觸發
    放現金 ＝ 趨勢跌破  而且  衰退指標觸發
月底判斷、下個月生效；扣 ETF 進出成本；現金 0%。
衰退指標（全部來自 screener/macro_ind 的同一套狀態規則）：
    美國薩姆規則（FRED 即時版，沒有修正問題；公布時間差 1 個月）
    台灣薩姆規則（國發會失業率：3 個月平均比過去 12 個月最低的 3 個月平均高 ≥0.5 個百分點；時間差 1 個月；失業率不太修正）
    美國 CFNAI、美國初領失業金
兩種台股資料：
    ^TWII 1997～2026（價格指數、不含息；有 2000、2008 兩次長空頭）
    0050 含息 2004～2026（yfinance 還原股價；較短但貼近實際能買的東西）
另外做穩健性：前後兩半、逐年差異、拿掉 2008 年後的結果。
執行：.venv/bin/python research/sahm_tw.py [--us 本機 macro_series.csv.gz]
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _paths import OUT  # noqa: E402

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from macro_signals import load_indicators, monthly_good, run_strategy, stats, drawdowns  # noqa: E402
from screener import macro_ind as M  # noqa: E402


def tw_sahm(unemp: pd.Series) -> pd.Series:
    """台灣版薩姆規則數值（失業率月資料）。"""
    ma3 = unemp.rolling(3).mean()
    return (ma3 - ma3.shift(1).rolling(12).min()).dropna()


def load_px() -> pd.DataFrame:
    import yfinance as yf
    out = {}
    for t, adj in (("^TWII", True), ("0050.TW", True)):
        d = yf.download(t, start="1997-01-01", interval="1mo", progress=False, auto_adjust=adj)["Close"]
        s = d.iloc[:, 0] if isinstance(d, pd.DataFrame) else d
        s.index = s.index.to_period("M").to_timestamp("M")
        out[t] = s.groupby(level=0).last()
    px = pd.DataFrame(out)
    # Yahoo 0050 有錯價（2014-01 −75% 又彈回）：月報酬跟加權指數差超過 25 個百分點的月份，改用加權指數的報酬
    r50, rtw = px["0050.TW"].pct_change(), px["^TWII"].pct_change()
    bad = (r50 - rtw).abs() > 0.25
    if bad.any():
        print("0050 錯價月份改用加權指數報酬：", ", ".join(f"{d:%Y-%m}" for d in px.index[bad]))
        r50[bad] = rtw[bad]
        first = px["0050.TW"].first_valid_index()
        px["0050.TW"] = (1 + r50.loc[first:].fillna(0)).cumprod() * px["0050.TW"].loc[first]
    return px[px.index < pd.Timestamp.today().to_period("M").to_timestamp("M")]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--us")
    a = ap.parse_args()
    raw, extra = load_indicators(a.us)
    raw["tw_sahm"] = tw_sahm(raw["tw_unemp"])
    M.BY_KEY["tw_sahm"] = {**M.BY_KEY["us_sahm"], "key": "tw_sahm", "country": "tw", "name": "台灣薩姆規則"}
    px = load_px()
    sigs = [("美國薩姆規則", "us_sahm", 1), ("台灣薩姆規則", "tw_sahm", 1), ("美國 CFNAI", "us_cfnai", 1), ("美國初領失業金", "us_claims", 0)]
    rows, yearly = [], {}
    for tk in ("^TWII", "0050.TW"):
        p = px[tk].dropna()
        r = p.pct_change()
        trend = (p > p.rolling(10).mean()).astype(float)
        good_all = {name: monthly_good(key, raw, extra, lag, p.index) for name, key, lag in sigs if key in raw}
        start = max([p.index[10]] + [g.first_valid_index() for g in good_all.values()])
        idx = p.index[p.index >= start]
        rr = r.reindex(idx)
        h = len(idx) // 2
        plans = {"永遠持有": pd.Series(1.0, index=idx), "只看趨勢": trend.reindex(idx)}
        for name, g in good_all.items():
            plans[f"趨勢或{name}"] = ((trend.reindex(idx) + g.reindex(idx).fillna(1)) > 0).astype(float)
        dd = drawdowns(p.reindex(idx))
        for name, on in plans.items():
            ret = run_strategy(on, rr)
            s = stats(ret, on)
            post = ret[ret.index >= "2009-07-01"]
            rows.append({"市場": tk, "期間": f"{idx[0]:%Y-%m}～{idx[-1]:%Y-%m}", "方案": name, **s,
                         "前半": stats(ret.iloc[:h])["年化"], "後半": stats(ret.iloc[h:])["年化"],
                         "2009-07 起": stats(post)["年化"], "2009-07 起最大跌": stats(post)["最大跌幅"]})
            yearly[(tk, name)] = (1 + ret).groupby(ret.index.year).prod() - 1
        print(f"{tk} 空頭（跌 20% 起、反彈 20% 止）：" + "、".join(f"{a:%Y-%m}→{b:%Y-%m} {d:.0%}" for a, b, d in dd))
    res = pd.DataFrame(rows)
    f = res.copy()
    for c in ("年化", "最大跌幅", "前半", "後半", "2009-07 起", "2009-07 起最大跌"):
        f[c] = f[c].map(lambda v: f"{v:+.1%}")
    f["持有時間"] = f["持有時間"].map(lambda v: f"{v:.0%}")
    pd.set_option("display.width", 250)
    for tk, g in f.groupby("市場", sort=False):
        print(f"\n==== {tk}（{g['期間'].iloc[0]}；月底判斷、下個月生效；現金 0%）====")
        print(g.drop(columns=["市場", "期間"]).to_string(index=False))
    # 逐年：跟「只看趨勢」比，差在哪幾年
    print("\n==== 逐年差異（趨勢或美國薩姆 − 只看趨勢；只列差 ≥3 個百分點的年份）====")
    for tk in ("^TWII", "0050.TW"):
        if (tk, "趨勢或美國薩姆規則") not in yearly:
            continue
        d = yearly[(tk, "趨勢或美國薩姆規則")] - yearly[(tk, "只看趨勢")]
        big = d[d.abs() >= 0.03]
        print(tk, "、".join(f"{y}:{v:+.0%}" for y, v in big.items()))
        d2 = yearly[(tk, "趨勢或美國薩姆規則")] - yearly[(tk, "永遠持有")]
        big2 = d2[d2.abs() >= 0.03]
        print(f"{tk}（比永遠持有）", "、".join(f"{y}:{v:+.0%}" for y, v in big2.items()))
    res.to_csv(OUT / "sahm_tw.csv", index=False)


if __name__ == "__main__":
    main()
