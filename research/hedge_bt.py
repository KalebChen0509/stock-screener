"""【2026/11/06】避險回測（事先定好的規則，不調參）：0050（含息）＋各種「降低風險」訊號，2009～2026。

訊號成立時，台股曝險的一部分用兩種方式降低：
  期貨：放空台指期近月（連續合約報酬，換月不跳空），成本每次進出 0.01%（期交稅＋手續費＋滑價，約略）
  現金：賣掉股票放現金，成本賣 0.4425%（含證交稅 0.3%）＋買回 0.1425%
訊號都在收盤後判斷、隔天開始生效（避免偷看當天結果）。
"""
import os  # noqa: F401
import sys  # noqa: F401
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _paths import CACHE, OUT, ROOT  # noqa: E402,F401
import sys

import numpy as np
import pandas as pd

from screener import derivs  # noqa: E402

C = CACHE
s = pd.read_csv(C + "/stress_daily.csv.gz", parse_dates=["date"])
px = s.pivot_table(index="date", columns="ticker", values="adj_close" if "adj_close" in s else "close").sort_index()
e = px["0050.TW"].dropna()
r_e = e.pct_change().dropna()
# Yahoo 的 0050 日線有錯價（2014-01-02 −75%：分割還原套錯日期；2009-02-19 +16.5% 超過漲跌幅）→ 那幾天改用加權指數報酬
r_tw = px["^TWII"].pct_change().reindex(r_e.index)
bad = r_e.abs() > 0.11
print("修正錯價天數", int(bad.sum()), list(r_e[bad].index.date))
r_e = r_e.mask(bad, r_tw)
# 2009 以前沒有 0050 日線：用加權指數報酬＋每年約 3% 股息代替（為了納入 2008 金融海嘯）
tw_all = px["^TWII"].dropna().pct_change().dropna()
pre = tw_all.loc["2007-01-01":r_e.index[0] - pd.Timedelta(days=1)] + 0.03 / 252
r_e = pd.concat([pre, r_e]).sort_index()
fut = pd.read_csv(C + "/derivs/fut_daily.csv.gz", parse_dates=["date"], dtype={"expiry": str})
r_f = derivs.front_returns(fut, "TX")
tx = derivs.front_settle(fut, "TX")
vol = pd.read_csv(C + "/derivs/us_vol.csv.gz", parse_dates=["date"]).pivot_table(index="date", columns="ticker", values="close")
ins = pd.read_csv(C + "/derivs/insti_fut.csv.gz", parse_dates=["date"])
fx = derivs.foreign_net_tx(ins)

idx = r_e.index.intersection(r_f.index)
r_e, r_f = r_e.loc[idx], r_f.loc[idx]
twii = px["^TWII"].reindex(idx).ffill()

# ---- 訊號（True＝降低風險）；全部用「當天收盤」判斷，隔天生效 ----
sig = {}
ma200 = twii.rolling(200).mean()
sig["趨勢：加權指數跌破 200 日均線"] = twii < ma200
me = twii.resample("ME").last()
ma10 = me.rolling(10).mean()
m_sig = (me < ma10).reindex(idx, method="ffill")            # 月底判斷，整個下個月維持
sig["趨勢（月）：月底跌破 10 個月均線"] = m_sig.fillna(False)
# 時差：台股 t 日收盤時，美股 t 日還沒開盤 → 只能用美國 t−1 日（含）以前的收盤
vr_us = vol["^VIX"] / vol["^VIX3M"]
vr_us.index = vr_us.index + pd.Timedelta(days=1)
vr = vr_us.reindex(idx, method="ffill")
sig["恐慌：美國 VIX 倒掛（VIX ÷ VIX3M > 1）"] = (vr > 1).fillna(False)
dd = twii / twii.rolling(60).max() - 1
sig["跌深：距 60 日高點 −10% 以上"] = dd <= -0.10
# 外資台指期淨空單：低於過去一年的第 20 百分位（2018/6 起才有資料）
fxr = fx.reindex(idx, method="ffill")
pct = fxr.rolling(250, min_periods=120).apply(lambda w: (w <= w[-1]).mean(), raw=True)
sig["外資：期貨淨空單在一年最多的 20%（2019 起）"] = (pct <= 0.2).fillna(False)


def run(signal: pd.Series, ratio: float, how: str):
    on = signal.shift(1).fillna(False).astype(float) * ratio       # 隔天生效
    turn = on.diff().abs().fillna(on.iloc[0])
    if how == "期貨":
        r = r_e - on * r_f - turn * 0.0001
    else:
        cost = np.where(on.diff().fillna(0) > 0, 0.004425, 0.001425)   # 降低曝險＝賣股；恢復＝買回
        r = (1 - on) * r_e - turn * cost
    return r, int((on.diff().fillna(0) != 0).sum())


def st(r, start=None):
    r = r.loc[start:] if start else r
    nav = (1 + r).cumprod()
    yrs = (r.index[-1] - r.index[0]).days / 365.25
    cagr = nav.iloc[-1] ** (1 / yrs) - 1
    vol_ = r.std() * np.sqrt(252)
    return cagr, float((nav / nav.cummax() - 1).min()), cagr / vol_ if vol_ else np.nan


EP = {"2008 金融海嘯": ("2008-05-20", "2009-03-02"), "2011 歐債": ("2011-05-01", "2011-12-31"), "2015 中國股災": ("2015-04-27", "2015-09-30"),
      "2018Q4": ("2018-09-28", "2019-01-04"), "2020 新冠": ("2020-01-14", "2020-04-30"),
      "2022 升息": ("2022-01-05", "2022-10-31"), "2024/8": ("2024-07-11", "2024-09-06"),
      "2025/4 關稅": ("2025-03-01", "2025-05-30"), "2026/10～11": ("2026-10-06", "2026-11-06")}


def ep_ret(r):
    return {k: float((1 + r.loc[a:b]).prod() - 1) for k, (a, b) in EP.items()}


rows = []
base = r_e.copy()
rows.append(("0050 持有不避險", "—", 0, *st(base), *st(base, "2019-01-01"), ep_ret(base)))
for name, sgl in sig.items():
    for how in ("期貨", "現金"):
        for ratio in (0.5, 1.0):
            r, n = run(sgl, ratio, how)
            rows.append((name, f"{how} {int(ratio*100)}%", n, *st(r), *st(r, "2019-01-01"), ep_ret(r)))
print(f"期間 {idx[0].date()} ～ {idx[-1].date()}（{len(idx)} 個交易日）；0050 含息、台指期近月連續")
print(f"{'訊號':<34}{'做法':<9}{'切換次數':>6}{'年化':>8}{'最大跌':>8}{'報酬/波動':>8}{'2019起年化':>10}{'2019起最大跌':>12}")
for n, h, k, c, m, sh, c2, m2, sh2, ep in rows:
    print(f"{n:<34}{h:<9}{k:>6}{c:>8.1%}{m:>8.1%}{sh:>8.2f}{c2:>10.1%}{m2:>12.1%}")
print("\n各次急跌期間的報酬（期貨 100% 版本）：")
hdr = list(EP)
print(f"{'':<34}" + "".join(f"{h:>11}" for h in hdr))
for n, h, k, c, m, sh, c2, m2, sh2, ep in rows:
    if h in ("—", "期貨 100%"):
        print(f"{n:<34}" + "".join(f"{ep[x]:>11.1%}" for x in hdr))
# 目前（最新一天收盤）各訊號狀態
print("\n最新一天（%s）訊號：" % idx[-1].date(), {k: bool(v.iloc[-1]) for k, v in sig.items()})
