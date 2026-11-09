"""【2026/11/09】「平常分批投入、上櫃融資維持率跌破門檻才加速投入」有沒有比單純分批好？

規則在看結果前寫死（2026-11-09），不調參數：
測試一：每月定期投入（使用者每月約 1 萬元）
    基準 A：每月第一個交易日開盤，把當月 1 萬元全部買 0050。
    加碼 B：每月只買 5,000 元，另外 5,000 元存進「預備金」（閒置資金＝定存利率）；
            任何一天收盤上櫃維持率 < 150 → 隔天開盤把預備金全部買進。
    加碼 C：同 B，門檻 145。
    比較：XIRR（每筆投入的實際報酬率）、期末資產、預備金平均放了多少、最長放多久。
    為什麼留一半：全部留著等就變回原本的擇時策略（已證明會錯過大漲）；留一半是「一般人做得到」的折衷，事先定好不調。
測試二：一筆錢分 6 個月等額投入（使用者的真錢投入標準第 5 條）
    基準 E：100 萬元分 6 個月，每月第一個交易日買 1/6，還沒買的放定存。
    加速 F：同 E，但 6 個月內任何一天上櫃維持率 < 150 → 隔天開盤把剩下的全部買進。
    加速 G：同 F，門檻 145。
    每個月都當一次起點（2009-01～），比較第 24、60 個月時的資產：F／G 贏 E 的比例、平均差多少。
共同：手續費 0.1425%（最低 20 元）、證交稅不適用（不賣）、股利除息日入帳、隔天開盤再投入、二代健保 ≥2 萬扣 2.11%。
執行：.venv/bin/python research/margin_dca.py → research/out/margin_dca/
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _paths import OUT as OUT_ROOT  # noqa: E402

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

import margin_timing as T  # noqa: E402

MONTHLY = 10_000
RESERVE_SHARE = 0.5
LUMP = 1_000_000
TRANCHES = 6
OUT = OUT_ROOT / "margin_dca"


def monthly_plan(px, ratio, idle, thr=None):
    """thr=None：基準 A；否則 B／C。回傳 nav、現金流、預備金序列。"""
    firsts = set(px.groupby(px.index.to_period("M")).head(1).index)
    b = T.Book(cash=0.0)
    reserve, flows, nav, res_hist = 0.0, [], [], []
    deploy, reinvest = False, False
    for i, d in enumerate(px.index):
        row = px.loc[d]
        if i > 0:
            reserve *= float(idle.loc[d])
        if row["split"] != 1 and b.shares > 0:
            b.shares *= row["split"]
        if row["div"] > 0 and b.shares > 0:
            g = b.shares * row["div"]
            b.cash += g - (g * T.NHI_RATE if g >= T.NHI_MIN else 0.0)
            reinvest = True
        if d in firsts:
            flows.append((d, -MONTHLY))
            if thr is None:
                b.cash += MONTHLY
            else:
                b.cash += MONTHLY * (1 - RESERVE_SHARE)
                reserve += MONTHLY * RESERVE_SHARE
        if deploy and reserve > 0:
            b.cash += reserve
            reserve, deploy = 0.0, False
        if b.cash > T.FEE_MIN * 2 and (d in firsts or reinvest or b.cash > 1000):
            T.buy(b, b.cash, row["open"], d, {})
            reinvest = False
        r = ratio.asof(d)
        if thr is not None and not np.isnan(r) and r < thr and reserve > 0:
            deploy = True
        nav.append(b.cash + b.shares * row["close"] + reserve)
        res_hist.append(reserve)
    s = pd.Series(nav, index=px.index)
    flows.append((px.index[-1], s.iloc[-1]))
    return {"nav": s, "flows": flows, "xirr": T.xirr(flows), "reserve": pd.Series(res_hist, index=px.index),
            "invested": MONTHLY * len(firsts), "fees": b.fees}


def lump_plan(px, ratio, idle, start, thr=None, horizon_m=60):
    """從 start（某月第一個交易日）開始分 6 個月投入；回傳每天資產（到 horizon_m 個月）。"""
    end = start + pd.DateOffset(months=horizon_m)
    p = px.loc[start:end]
    months = list(p.groupby(p.index.to_period("M")).head(1).index)[:TRANCHES]
    b = T.Book(cash=0.0)
    pending = float(LUMP)                       # 還沒投入（放定存）
    each = LUMP / TRANCHES
    deploy, reinvest, nav = False, False, []
    for i, d in enumerate(p.index):
        row = p.loc[d]
        if i > 0:
            pending *= float(idle.loc[d])
        if row["split"] != 1 and b.shares > 0:
            b.shares *= row["split"]
        if row["div"] > 0 and b.shares > 0:
            g = b.shares * row["div"]
            b.cash += g - (g * T.NHI_RATE if g >= T.NHI_MIN else 0.0)
            reinvest = True
        amt = 0.0
        if deploy and pending > 0:
            amt, pending, deploy = pending, 0.0, False
        elif d in months and pending > 0:
            k = TRANCHES - months.index(d)      # 剩下幾期：平均分
            amt = pending / k
            pending -= amt
        if amt > 0 or (reinvest and b.cash > 1000):
            b.cash += amt
            T.buy(b, b.cash, row["open"], d, {})
            reinvest = False
        r = ratio.asof(d)
        if thr is not None and pending > 0 and not np.isnan(r) and r < thr:
            deploy = True
        nav.append(b.cash + b.shares * row["close"] + pending)
    return pd.Series(nav, index=p.index)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    tpex, rate = T.load_ratio("tpex"), T.load_rate()
    px = T.load_etf("0050")
    px = px.loc[tpex.index.min():]
    idle = T.idle_factor(px.index, rate)
    lines = ["# 加碼用法回測（自動產生的表格）", ""]

    # ---- 測試一
    rows = []
    res = {}
    for name, thr in (("A 每月全部買（基準）", None), ("B 留一半，<150 才買", 150), ("C 留一半，<145 才買", 145)):
        r = monthly_plan(px, tpex, idle, thr)
        res[name] = r
        rs = r["reserve"]
        longest, cur = 0, 0
        for v in (rs > 0).values:
            cur = cur + 1 if v else 0
            longest = max(longest, cur)
        rows.append({"方案": name, "XIRR": T.pct(r["xirr"]), "總投入": f"{r['invested']:,.0f}", "期末資產": f"{r['nav'].iloc[-1]:,.0f}",
                     "預備金平均": f"{rs.mean():,.0f}", "預備金最多": f"{rs.max():,.0f}", "預備金最長放幾個交易日": longest,
                     "手續費合計": f"{r['fees']:,.0f}"})
    t1 = pd.DataFrame(rows)
    # 分段：前後半
    half = px.index[len(px) // 2]
    seg = []
    for name, thr in (("A", None), ("B <150", 150), ("C <145", 145)):
        for lab, a, b_ in (("前半", None, half), ("後半", half, None)):
            p2 = px.loc[a:b_]
            r = monthly_plan(p2, tpex, T.idle_factor(p2.index, rate), thr)
            seg.append({"方案": name, "期間": f"{p2.index[0]:%Y-%m}～{p2.index[-1]:%Y-%m}", "XIRR": T.pct(r["xirr"])})
    t1s = pd.DataFrame(seg)
    lines += ["## 測試一：每月 1 萬元（2009-01～2026-11）", "", T.md_table(t1), "", "### 前後半", "", T.md_table(t1s), ""]

    # ---- 測試二：每個月當起點
    starts = list(px.groupby(px.index.to_period("M")).head(1).index)
    rec = []
    for s in starts:
        for h in (24, 60):
            if s + pd.DateOffset(months=h) > px.index[-1]:
                continue
            e = T.lump_plan if False else None  # noqa
            base = lump_plan(px, tpex, idle, s, None, h).iloc[-1]
            f150 = lump_plan(px, tpex, idle, s, 150, h).iloc[-1]
            f145 = lump_plan(px, tpex, idle, s, 145, h).iloc[-1]
            rec.append({"start": s, "h": h, "E": base, "F": f150, "G": f145})
    R = pd.DataFrame(rec)
    t2 = []
    for h in (24, 60):
        g = R[R["h"] == h]
        for col, lab in (("F", "F <150 加速"), ("G", "G <145 加速")):
            diff = g[col] / g["E"] - 1
            trig = diff.abs() > 1e-9
            t2.append({"比較": f"{lab} vs E 分 6 個月", "看第幾個月": h, "起點數": len(g),
                       "有觸發加速的起點": int(trig.sum()), "觸發時贏的比例": f"{(diff[trig] > 0).mean():.0%}" if trig.any() else "",
                       "觸發時平均差": T.pct(diff[trig].mean(), 2) if trig.any() else "", "觸發時最好": T.pct(diff[trig].max(), 1) if trig.any() else "",
                       "觸發時最差": T.pct(diff[trig].min(), 1) if trig.any() else "", "全部起點平均差": T.pct(diff.mean(), 2)})
    t2d = pd.DataFrame(t2)
    trig_list = R[(R["h"] == 24) & ((R["F"] / R["E"] - 1).abs() > 1e-9)].copy()
    trig_list["F 差"] = (trig_list["F"] / trig_list["E"] - 1).map(lambda v: T.pct(v, 1))
    trig_list["start"] = trig_list["start"].dt.strftime("%Y-%m")
    lines += ["## 測試二：100 萬分 6 個月（每個月當一次起點）", "", T.md_table(t2d), "",
              "### 有觸發 <150 加速的起點（第 24 個月）", "", T.md_table(trig_list[["start", "F 差"]]), ""]
    # 參考（不是事先定的）：預備金比例 30%／70%
    ref = []
    global RESERVE_SHARE
    for share in (0.3, 0.7):
        RESERVE_SHARE = share
        r = monthly_plan(px, tpex, idle, 150)
        ref.append({"預備金比例（參考，非事先定）": f"{share:.0%}", "XIRR": T.pct(r["xirr"])})
    RESERVE_SHARE = 0.5
    lines += ["## 參考：預備金比例敏感度（<150）", "", T.md_table(pd.DataFrame(ref)), ""]
    (OUT / "results.md").write_text("\n".join(lines))
    R.to_csv(OUT / "lump_starts.csv", index=False)
    print("\n".join(lines))


if __name__ == "__main__":
    main()
