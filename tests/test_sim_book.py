"""模擬投資帳本：手續費（最低收費）、成交時點（記錄之後第一個收盤，不能挑過去的價格）、現金不足、系統線的決策時點、報酬計算。

執行：python -m tests.test_sim_book（不需網路）
"""
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("SCREENER_CACHE", tempfile.mkdtemp())
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from screener import sim_book as S  # noqa: E402

DAYS = pd.bdate_range("2026-10-26", "2026-12-31")


def prices(drift=None):
    drift = drift or {"2330.TW": 0.01, "0050.TW": 0.002, "SPY": 0.001, "VT": 0.001, "AAA.TW": -0.01}
    close = pd.DataFrame({t: 100 * (1 + g) ** np.arange(len(DAYS)) for t, g in drift.items()}, index=DAYS)
    adj = close.copy()
    adj["0050.TW"] = close["0050.TW"] * 0.98            # 還原價基準不同，比例不變
    return S.Prices(close, adj, pd.Series(32.0, index=DAYS))


def test_fees():
    assert S.fee_buy("2330.TW", 300, 1.0) == 1                       # 0.1425%×2 折 ≈ 0.09 元 → 最低 1 元
    assert S.fee_buy("2330.TW", 100000, 1.0) == 28
    assert S.fee_sell("2330.TW", 10000, 1.0) == 3 + 30               # 手續費 3＋證交稅 0.3%
    assert S.fee_sell("0050.TW", 10000, 1.0) == 3 + 10               # ETF 證交稅 0.1%
    assert abs(S.fee_sell("SPY", 3200, 32.0) - (96 + 1.6)) < 1e-6    # 0.18%＝5.76 元 < 最低 3 美元＝96 元
    assert abs(S.fee_buy("SPY", 3200, 32.0) - (2.56 + 1.6)) < 1e-6


def test_fill_after_record_time():
    px = prices()
    # 台北 11/17 10:00 記錄 → 當天 13:30 收盤成交
    d, c, _ = px.fill("2330.TW", pd.Timestamp("2026-11-17 10:00", tz="Asia/Taipei").tz_convert("UTC"))
    assert d == pd.Timestamp("2026-11-17") and c == px.close.at[d, "2330.TW"]
    # 收盤後 14:00 記錄 → 下一個交易日
    d, _, _ = px.fill("2330.TW", pd.Timestamp("2026-11-17 14:00", tz="Asia/Taipei").tz_convert("UTC"))
    assert d == pd.Timestamp("2026-11-18")
    # 美股：台北 11/17 22:00（紐約 9:00）記錄 → 紐約 11/17 收盤
    d, _, _ = px.fill("SPY", pd.Timestamp("2026-11-17 22:00", tz="Asia/Taipei").tz_convert("UTC"))
    assert d == pd.Timestamp("2026-11-17")
    # 台北 11/18 06:00（紐約 11/17 17:00，已收盤）→ 紐約 11/18
    d, _, _ = px.fill("SPY", pd.Timestamp("2026-11-18 06:00", tz="Asia/Taipei").tz_convert("UTC"))
    assert d == pd.Timestamp("2026-11-18")
    # 還沒有之後的收盤 → None（待成交）
    assert px.fill("2330.TW", pd.Timestamp("2026-12-31 14:00", tz="Asia/Taipei").tz_convert("UTC")) is None


def test_my_line_no_backdating_and_cash():
    px = prices()
    T = lambda s: pd.Timestamp(s, tz="Asia/Taipei").tz_convert("UTC").isoformat()  # noqa: E731
    trades = [{"id": "a", "created": T("2026-11-03 10:00"), "ticker": "2330.TW", "side": "buy", "amount": 6000},
              {"id": "b", "created": T("2026-11-03 11:00"), "ticker": "AAA.TW", "side": "buy", "amount": 6000},   # 只剩約 4 千
              {"id": "c", "created": T("2026-11-20 09:00"), "ticker": "2330.TW", "side": "sell", "frac": 0.5},
              {"id": "d", "created": T("2026-12-31 15:00"), "ticker": "2330.TW", "side": "buy", "amount": 100}]
    b, st = S.run_my_line(trades, px, DAYS, "2026-11-01", 10000)
    assert st["a"]["status"] == "已成交" and st["a"]["date"] == "2026-11-03"
    assert st["b"]["status"] == "現金不足" and "可用現金" in st["b"]["msg"]
    assert st["c"]["status"] == "已成交" and st["c"]["date"] == "2026-11-20"
    assert st["d"]["status"] == "待成交"
    assert b.invested == 20000                                      # 11 月、12 月各入金 1 萬
    buy_px = px.close.at[pd.Timestamp("2026-11-03"), "2330.TW"]
    assert b.log[0]["price"] == buy_px                              # 用記錄之後那個收盤價，不是更早、更便宜的價格
    s = S.summarize(b, px)
    assert s["value"] > 0 and s["invested"] == 20000 and abs(s["value"] - (s["cash"] + sum(h["value"] for h in s["holdings"]))) <= 2


def test_target_line_timing_and_rebalance():
    px = prices()
    dec = {"2026-10-31": {"2330.TW": 0.5, "AAA.TW": 0.5}, "2026-11-30": {"2330.TW": 0.5, "0050.TW": 0.5}}
    b = S.run_target_line("系統線", dec, px, DAYS, "2026-11-01", 10000)
    first = [x for x in b.log if x["date"] == "2026-11-02"]
    assert {x["ticker"] for x in first} == {"2330.TW", "AAA.TW"}      # 10/31 決策 → 11/02（第一個交易日）收盤成交
    dec_days = sorted({x["date"] for x in b.log})
    assert "2026-12-01" in dec_days                                   # 11/30 決策 → 12/01
    sold = [x for x in b.log if x["side"] == "賣"]
    assert [x["ticker"] for x in sold] == ["AAA.TW"] and sold[0]["date"] == "2026-12-01"
    assert "AAA.TW" not in b.units and "0050.TW" in b.units
    etf = S.run_target_line("全買 0050", {"2026-10-31": {"0050.TW": 1.0}}, px, DAYS, "2026-11-01", 10000)
    s = S.summarize(etf, px)
    # 0050 每天 +0.2%：時間加權報酬約等於期間漲幅（扣一點手續費）
    exp = px.close.at[DAYS[-1], "0050.TW"] / px.close.at[pd.Timestamp("2026-11-02"), "0050.TW"] - 1
    assert abs(s["twr"] - exp) < 0.01, (s["twr"], exp)


def test_check_trades():
    px = prices()
    T = "2026-11-03T02:00:00Z"
    st, good = S.check_trades([{"id": "a", "created": T, "ticker": "2330.TW", "side": "buy", "amount": 1000},
                               {"id": "b", "created": T, "ticker": "9999.TW", "side": "buy", "amount": 1000},
                               {"id": "c", "created": T, "ticker": "2330.TW", "side": "buy", "amount": "abc"},
                               {"id": "d", "ticker": "2330.TW", "side": "sell", "frac": 1}], px)
    assert [t["id"] for t in good] == ["a"]
    assert st["b"]["status"] == "代號錯誤" and st["c"]["status"] == "格式錯誤" and st["d"]["status"] == "格式錯誤"


def test_xirr():
    flows = [(pd.Timestamp("2026-01-01"), -100.0), (pd.Timestamp("2027-01-01"), 110.0)]
    assert abs(S.xirr(flows) - 0.0997) < 0.002
    assert S.xirr([(pd.Timestamp("2026-01-01"), -100.0), (pd.Timestamp("2026-01-10"), 101.0)]) is None   # 不到一個月不年化


if __name__ == "__main__":
    ok = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            ok += 1
            print("✔", name)
    print(f"全部通過：{ok} 項")
