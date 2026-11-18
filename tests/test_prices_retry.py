"""股價下載：Yahoo 擋掉一整批時，等一下再補抓一次（不需網路，用假的下載函式）。

執行：python -m tests.test_prices_retry
"""
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("SCREENER_CACHE", tempfile.mkdtemp())
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd  # noqa: E402

from screener import prices as P  # noqa: E402


def test_retry_recovers_blocked_batch():
    calls = []

    def fake(part):
        calls.append(list(part))
        ok = [t for t in part if not t.startswith("DEAD") and (len(calls) > 2 or not t.startswith("B"))]
        return pd.DataFrame({"ticker": ok, "close": 1.0})

    tickers = [f"A{i}" for i in range(5)] + [f"B{i}" for i in range(5)] + ["DEAD1"]
    frames = [fake(tickers[:6]), fake(tickers[6:])]
    P._retry_missing(frames, tickers, fake, chunk=6, wait=0)
    got = set().union(*(set(f["ticker"]) for f in frames))
    assert got == set(tickers) - {"DEAD1"}, got                 # 被擋的 B 批補回來；真的下市的還是沒有
    assert sum("DEAD1" in c for c in calls) == 2                # 下市股只多試一次


if __name__ == "__main__":
    test_retry_recovers_blocked_batch()
    print("✔ test_retry_recovers_blocked_batch\n全部通過：1 項")
