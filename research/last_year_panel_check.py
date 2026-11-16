"""截斷測試的第二層（last_year.py 的補充）：財報指標本身有沒有用到「之後才公布」的季度。

last_year.py 是「先用全部財報算好指標，再只留公布日 ≤ T 的列」。如果某一列的指標（近四季合計、三年平均、財報驚喜…）
在計算時用到了更晚的季度，那個測試看不出來。這裡改成「先把原始財報砍到公布日 ≤ T，再重新算指標」，逐格比對兩邊是否相同。
    SCREENER_CACHE=<data 分支>/cache .venv/bin/python research/last_year_panel_check.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _paths import OUT  # noqa: E402,F401

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from screener import data_tw, data_us, prices  # noqa: E402
from screener.loaders import load_infos  # noqa: E402
from screener.metrics import fundamental_panel  # noqa: E402

monthly = prices.load_monthly(include_delisted=True)
infos = load_infos(include_delisted=True)
for mkt, loader in (("tw", data_tw.load_fundamentals), ("us", data_us.load_fundamentals)):
    f = loader()
    f = f[f["id"].isin(infos[mkt].index)]
    ymap = dict(zip(infos[mkt].index, infos[mkt]["yahoo"]))
    full = fundamental_panel(f, prices.split_factor_table(monthly), ymap)
    for T in ("2025-10-31", "2026-04-30", "2026-10-31"):
        T = pd.Timestamp(T)
        pT = fundamental_panel(f[f["available_date"] <= T], prices.split_factor_table(monthly[monthly["month"] <= T]), ymap)
        a = full[full["available_date"] <= T].set_index(["id", "period_end"]).sort_index()
        b = pT.set_index(["id", "period_end"]).sort_index()
        # 只比「T 當天選股會用到的列」：每家公司公布日 ≤ T 的最新一季（往前 8 季也一起比）
        common = a.index.intersection(b.index)
        num = [c for c in a.columns if pd.api.types.is_numeric_dtype(a[c]) and c in b.columns]
        A, B = a.loc[common, num].astype(float), b.loc[common, num].astype(float)
        diff = ~(np.isclose(A.values, B.values, rtol=1e-6, atol=1e-9, equal_nan=True))
        bad_cols = pd.Series(diff.sum(axis=0), index=num)
        bad_cols = bad_cols[bad_cols > 0].sort_values(ascending=False)
        # 最新一季（選股真的會用的）
        last = a.reset_index().sort_values("available_date").groupby("id").tail(1).set_index(["id", "period_end"]).index.intersection(common)
        dl = ~(np.isclose(A.loc[last].values, B.loc[last].values, rtol=1e-6, atol=1e-9, equal_nan=True))
        print(f"{'台股' if mkt == 'tw' else '美股'} T={T:%Y-%m-%d}：完整版 {len(a)} 列、截斷後重算 {len(b)} 列、可比對 {len(common)} 列；"
              f"不同的格子 {int(diff.sum())}／{diff.size}；各公司最新一季 {len(last)} 列裡不同的格子 {int(dl.sum())}", flush=True)
        if len(bad_cols):
            print("   不同的欄位：", bad_cols.head(8).to_dict())
            lc = pd.Series(dl.sum(axis=0), index=num)
            print("   最新一季不同的欄位：", lc[lc > 0].sort_values(ascending=False).head(8).to_dict())
