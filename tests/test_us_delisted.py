"""美股下市抽樣（Tiingo）：候選篩選、固定抽樣、限流時停下、接續抓。

執行：python -m tests.test_us_delisted（不需網路、不需 pytest、不需 Key）
"""
import io
import os
import sys
import tempfile
import zipfile
from pathlib import Path

os.environ["SCREENER_CACHE"] = tempfile.mkdtemp()

import pandas as pd  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from screener import us_delisted as U  # noqa: E402

LISTING = pd.DataFrame([
    # ticker, exchange, assetType, priceCurrency, startDate, endDate
    ("DEAD", "NYSE", "Stock", "USD", "2010-01-04", "2020-05-01"),       # ✔
    ("GONE", "NASDAQ", "Stock", "USD", "2015-01-02", "2023-03-10"),     # ✔
    ("ETFX", "NYSE ARCA", "ETF", "USD", "2010-01-04", "2020-05-01"),    # ETF：不要
    ("OTCX", "PINK", "Stock", "USD", "2010-01-04", "2020-05-01"),       # 店頭：不要
    ("OLD", "NYSE", "Stock", "USD", "2001-01-02", "2014-01-02"),        # 回測前就下市：不要
    ("LIVE", "NYSE", "Stock", "USD", "2001-01-02", "2026-11-05"),       # 還在交易：不要
    ("BBBY", "NASDAQ", "Stock", "USD", "1992-06-05", "2023-05-02"),     # 代號被新公司沿用：不要
    ("BBBY", "NYSE", "Stock", "USD", "2026-07-28", "2026-11-05"),
    ("AAPL", "NASDAQ", "Stock", "USD", "1990-01-02", "2019-01-01"),     # 現在宇宙有這個代號：不要
    ("SHRT", "NYSE", "Stock", "USD", "2019-06-01", "2020-01-02"),       # 交易不到一年：不要
    ("XY-P", "NYSE", "Stock", "USD", "2010-01-04", "2020-05-01"),       # 特別股：不要
], columns=["ticker", "exchange", "assetType", "priceCurrency", "startDate", "endDate"])


def test_candidates_filter():
    c = U.candidates(LISTING, active={"AAPL"})
    assert sorted(c["ticker"]) == ["DEAD", "GONE"], c


def test_sample_is_deterministic_and_weighted():
    cand = pd.DataFrame({"ticker": [f"T{i}" for i in range(100)], "exchange": "NYSE", "startDate": "2010-01-01", "endDate": "2020-01-01"})
    a, b = U.draw_sample(cand, n=10), U.draw_sample(cand, n=10)
    assert list(a["ticker"]) == list(b["ticker"]) and len(a) == 10
    assert (a["weight"] == 10).all()                                     # 每一檔代表 10 檔


class FakeHTTP:
    def __init__(self, limit_after=None):
        self.calls, self.limit_after = [], limit_after

    def get(self, url, params=None, headers=None, timeout=None):
        class R:
            pass
        r = R()
        if url == U.LIST_URL:
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w") as z:
                z.writestr("supported_tickers.csv", LISTING.to_csv(index=False))
            r.status_code, r.content = 200, buf.getvalue()
            r.raise_for_status = lambda: None
            return r
        self.calls.append(url)
        assert headers["Authorization"] == "Token test-key"
        if self.limit_after is not None and len(self.calls) > self.limit_after:
            r.status_code = 429
            return r
        t = url.split("/daily/")[1].split("/")[0]
        r.status_code = 200
        r.json = lambda: [{"date": "2019-01-31T00:00:00.000Z", "close": 10, "adjClose": 9.5, "volume": 1000},
                          {"date": "2019-02-28T00:00:00.000Z", "close": 11, "adjClose": 10.5, "volume": 1200}] if t == "DEAD" else []
        return r


def test_fetch_rate_limit_then_resume():
    h = FakeHTTP(limit_after=1)
    U.prepare(h, active={"AAPL"})
    res = U.fetch(max_requests=5, sleep=0, http=h, token="test-key")
    assert res["requests"] == 2 and res["left"] == 1                    # 第 2 次被限流 → 先停
    h2 = FakeHTTP()
    res = U.fetch(max_requests=5, sleep=0, http=h2, token="test-key")
    assert res["left"] == 0 and len(h2.calls) == 1                       # 只抓剩下的那一檔
    d = U.load_daily()
    assert set(d["ticker"]) == {"DEAD"} and d["adj_close"].tolist() == [9.5, 10.5]
    assert U._state()["miss"] == ["GONE"]                                # 沒資料的記成 miss，不再重抓


if __name__ == "__main__":
    ok = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            ok += 1
            print("✔", name)
    print(f"全部通過：{ok} 項")
