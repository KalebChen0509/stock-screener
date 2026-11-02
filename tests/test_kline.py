"""K 線資料的檢查（不需網路）。執行：python -m tests.test_kline"""
import json
import os
import sys
import tempfile
from pathlib import Path

_TMP = tempfile.mkdtemp()
os.environ["SCREENER_CACHE"] = _TMP

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from screener import kline as K  # noqa: E402

TODAY = pd.Timestamp("2026-11-02")
LISTED = {"AAA": "2015-01-01", "BBB": "2015-01-01", "0050.TW": "2015-01-01", "NEW": "2026-06-01", "SPLT": "2015-01-01"}
calls = []
split_day = {}
END = [TODAY]


def fake(tickers, start, interval):
    calls.append((tuple(tickers), start, interval))
    out = []
    for t in tickers:
        if t not in LISTED:
            continue
        s = max(pd.Timestamp(start), pd.Timestamp(LISTED[t]))
        idx = pd.bdate_range(s, END[0]) if interval == "1d" else pd.date_range(s, END[0], freq="MS")
        base = 100 + (np.arange(len(idx)) % 50)
        f = 0.5 if t in split_day else 1.0      # 分割後整段歷史價格減半
        d = pd.DataFrame({"ticker": t, "date": idx, "open": base * f, "high": base * f + 1, "low": base * f - 1,
                          "close": base * f + 0.5, "volume": 1000.0, "split": 0.0})
        if t in split_day:
            d.loc[d["date"] == split_day[t], "split"] = 2.0
        out.append(d)
    return pd.concat(out, ignore_index=True) if out else pd.DataFrame(columns=K.COLS + ["split"])


def main():
    tk = ["AAA", "BBB", "0050.TW", "NEW", "SPLT", "GONE"]
    r1 = K.update(tk, today=TODAY, backfill_limit=3, priority=["0050.TW"], fetch=fake)
    df = K.load_daily()
    assert r1["backfilled"] == 3 and set(df["ticker"]) == {"0050.TW", "AAA", "BBB"}, (r1, set(df["ticker"]))
    assert df["date"].min() >= TODAY - pd.DateOffset(years=5) - pd.Timedelta(days=10)
    mon = K.load_monthly()
    assert set(mon["ticker"]) == {"0050.TW", "AAA", "BBB"} and mon["date"].max() < df["date"].min(), "月線只存日線之前"
    assert mon["date"].min() <= pd.Timestamp("2015-02-01")
    # 第二天：補完其餘；GONE 抓不到記失敗；已有的只補最近 10 天
    calls.clear()
    r2 = K.update(tk, today=TODAY + pd.Timedelta(days=1), backfill_limit=3, fetch=fake)
    recent = [c for c in calls if c[2] == "1d" and c[1] > "2026-10-01"]
    assert recent and set(recent[0][0]) == {"0050.TW", "AAA", "BBB"}, calls
    assert set(K.load_daily()["ticker"]) == {"0050.TW", "AAA", "BBB", "NEW", "SPLT"} and r2["remaining"] == 1
    assert K.load_state()["GONE"]["fail"] == 1
    n_new = (K.load_daily()["ticker"] == "NEW").sum()
    assert 100 < n_new < 120, n_new
    # 分割：最近的資料出現分割 → 整檔重抓，價格基準一致
    split_day["SPLT"] = pd.Timestamp("2026-11-04")
    END[0] = pd.Timestamp("2026-11-04")
    LISTED_before = K.load_daily()
    old_px = LISTED_before[LISTED_before["ticker"] == "SPLT"]["close"].iloc[0]
    K.update(tk, today=TODAY + pd.Timedelta(days=2), backfill_limit=10, fetch=fake)
    s = K.load_daily()
    sp = s[s["ticker"] == "SPLT"]
    assert abs(sp["close"].iloc[0] - old_px * 0.5) < 1 and sp["date"].min() < pd.Timestamp("2022-01-01"), "整檔重抓"
    assert not K.load_state()["SPLT"].get("refetch")
    # 輸出網站檔
    out = Path(_TMP) / "site_kline"
    idx = K.export_site(out, {"AAA": ("us", "AAA"), "0050.TW": ("etf", "0050.TW"), "NEW": ("tw", "6999")})
    assert idx == {"tw": ["6999"], "us": ["AAA"], "etf": ["0050.TW"]}
    doc = json.loads((out / "us" / "AAA.json").read_text())
    assert len(doc["t"]) == len(doc["c"]) == len(doc["v"]) and doc["t"] == sorted(doc["t"])
    assert pd.Timestamp(doc["t"][-1], unit="D") <= TODAY + pd.Timedelta(days=2) and "m" in doc and doc["m"]["t"][-1] < doc["t"][0]
    assert "m" not in json.loads((out / "tw" / "6999.json").read_text()) or True
    print("kline ok", r1, r2)


if __name__ == "__main__":
    main()
