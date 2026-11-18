"""美股多類別股票公司（波克夏、Visa…）股數補值：SEC 沒有總股數 → 前一季往下補，或用 Yahoo 市值÷股價。

執行：python -m tests.test_us_shares（不需網路，用假的 Yahoo）
"""
import os
import sys
import tempfile
from pathlib import Path

os.environ["SCREENER_CACHE"] = tempfile.mkdtemp()
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from screener import data_us as U  # noqa: E402

TODAY = pd.Timestamp("2026-11-18")


def frame():
    pe = pd.to_datetime(["2024-09-30", "2025-09-30", "2025-12-31", "2026-03-31", "2026-06-30", "2026-09-30"])
    av = pe + pd.Timedelta(days=40)
    rows = []
    for tk, sh in [("BRK-B", [np.nan] * 6), ("AAA", [100, 100, 110, 110, 120, np.nan]), ("OLD", [5] + [np.nan] * 5)]:
        for p, a, s in zip(pe, av, sh):
            rows.append({"market": "us", "id": tk, "period_end": p, "available_date": a, "shares": s})
    return pd.DataFrame(rows)


def test_fill():
    calls = []
    fake = lambda tks: (calls.append(list(tks)), {"BRK-B": 2.1e9})[1]   # noqa: E731
    out = U.fill_missing_shares(frame(), fetcher=fake, today=TODAY).set_index(["id", "period_end"])["shares"]
    assert calls == [["BRK-B", "OLD"]]                                  # 近一年完全沒股數的才問 Yahoo
    assert out[("BRK-B", pd.Timestamp("2026-09-30"))] == 2.1e9
    assert np.isnan(out[("BRK-B", pd.Timestamp("2024-09-30"))])         # 更早的回測期間不補（跟以前一樣）
    assert out[("AAA", pd.Timestamp("2026-09-30"))] == 120              # 只是最新一季空白 → 用前一季
    assert out[("OLD", pd.Timestamp("2024-09-30"))] == 5 and np.isnan(out[("OLD", pd.Timestamp("2026-09-30"))])  # Yahoo 也沒有 → 維持空白
    # 6 天內再跑：用快取，不再問 Yahoo
    U.fill_missing_shares(frame(), fetcher=fake, today=TODAY + pd.Timedelta(days=2))
    assert calls[-1] == ["OLD"]


if __name__ == "__main__":
    test_fill()
    print("✔ test_fill\n全部通過：1 項")
