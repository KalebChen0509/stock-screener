"""【2026/11/06】主要結論用這支。穩定版估計：每月把「可選股」依 12-1 動能（加權後的百分位）分組，比較「只現存」vs「現存＋下市（抽樣加權）」的等權平均報酬。
偏差_t = 加入後 − 只現存；整體偏差＝月平均 × 12，t 值看顯不顯著。"""
import os  # noqa: F401
import sys  # noqa: F401
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _paths import CACHE, OUT, ROOT  # noqa: E402,F401
import sys
import numpy as np
import pandas as pd
src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "us_surv.py")).read().split("months = [t for t")[0]
exec(src)
months = [t for t in idx if START <= t < END]
B = {"全部可選股": 1.0, "動能前 20%": 0.2, "動能前 10%": 0.1, "動能前 5%": 0.05}
out = {k: [] for k in B}
share = {k: [] for k in B}
nd = {k: [] for k in B}
for t in months:
    i = idx.get_loc(t); t1 = idx[i + 1]
    ok = (cls.loc[t] >= 5) & (tv.iloc[max(0, i - 2):i + 1].mean() / 21 >= 2e6) & adj.loc[t].notna()
    ids = ok[ok].index
    mom = (adj.iloc[i - 1] / adj.iloc[i - 12] - 1)[ids].dropna()
    r = fwd_returns(t, t1, 1.0).fillna(0.0)
    w = weight[mom.index]
    order = mom.sort_values(ascending=False)
    cw = w[order.index].cumsum() / w.sum()                      # 加權後的累計占比（下市股一檔代表 w 檔）
    for k, q in B.items():
        sel = order.index[cw.values <= q + 1e-12] if q < 1 else order.index
        if len(sel) == 0:
            sel = order.index[:1]
        alive = [x for x in sel if x not in dead]; dd = [x for x in sel if x in dead]
        mu_a = r[alive].mean()
        mu_w = (r[alive].sum() + (r[dd] * weight[dd]).sum()) / (len(alive) + weight[dd].sum())
        out[k].append(mu_w - mu_a); share[k].append(weight[dd].sum() / (len(alive) + weight[dd].sum())); nd[k].append(len(dd))
print(f"每檔下市抽樣代表 {w_dead:.1f} 檔；期間 {months[0].date()}～{months[-1].date()}（{len(months)} 個月）")
print(f"{'群組':<10}{'現存檔數/月':>10}{'下市占比':>9}{'下市樣本/月':>11}{'偏差（年化）':>12}{'t 值':>7}{'前5年':>9}{'後5年':>9}")
h = len(months) // 2
for k in B:
    x = pd.Series(out[k]); n_alive = None
    print(f"{k:<10}{'':>10}{np.mean(share[k]):>9.1%}{np.mean(nd[k]):>11.1f}{-x.mean() * 12:>+12.2%}{-x.mean() / (x.std() / np.sqrt(len(x))):>7.2f}{-x[:h].mean() * 12:>+9.2%}{-x[h:].mean() * 12:>+9.2%}")
print("（偏差＝只用現存股票時，回測每年高估多少；負數＝反而低估）")
