"""下載台股日線長歷史（2005 起；收盤、含息還原價、成交量）給 stop_long.py 用。存在 research/out/tw_daily_long.pkl（不上傳）。

只有「現在還上市櫃」的公司＋2016 年以後下市的（快取裡有）→ 2005～2015 沒有下市公司（倖存者偏差，報告裡要註明）。
    SCREENER_CACHE=<data 分支>/cache .venv/bin/python research/tw_daily_long.py
"""
import os
import pickle
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _paths import CACHE, OUT  # noqa: E402

import pandas as pd  # noqa: E402
import yfinance as yf  # noqa: E402

u = pd.read_csv(os.path.join(CACHE, "tw_universe.csv"), dtype=str)
u = u[u["is_financial"].astype(str) != "True"]
tks = sorted(set(u["yahoo"].dropna()) | {"0050.TW", "^TWII"})
out = OUT / "tw_daily_long.pkl"
got = pickle.load(open(out, "rb")) if out.exists() else {"close": {}, "adj": {}, "vol": {}}
todo = [t for t in tks if t not in got["close"]]
print(f"{len(tks)} 檔，還要抓 {len(todo)} 檔", flush=True)
for i in range(0, len(todo), 80):
    grp = todo[i:i + 80]
    for attempt in range(3):
        try:
            d = yf.download(grp, start="2005-01-01", interval="1d", auto_adjust=False, progress=False, threads=8, group_by="ticker")
            break
        except Exception as e:  # noqa
            print("retry", e, flush=True)
            time.sleep(20)
    else:
        continue
    for t in grp:
        try:
            x = d[t].dropna(subset=["Close"])
        except Exception:  # noqa
            continue
        if len(x) < 250:
            got["close"][t] = pd.Series(dtype=float)       # 記下來，不再重抓
            continue
        got["close"][t], got["adj"][t], got["vol"][t] = x["Close"], x["Adj Close"], x["Volume"]
    pickle.dump(got, open(out, "wb"))
    print(f"{min(i + 80, len(todo))}/{len(todo)}", flush=True)
    time.sleep(2)
ok = [t for t, s in got["close"].items() if len(s)]
print(f"完成：有資料 {len(ok)} 檔", flush=True)
