"""股價歷史斷層修補的檢查（不需網路）。執行：python -m tests.test_prices_repair"""
import os
import sys
import tempfile

_TMP = tempfile.mkdtemp()
os.environ["SCREENER_CACHE"] = _TMP          # 必須在載入 screener 之前設定

import pandas as pd  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from screener import prices  # noqa: E402
import scripts.fetch_prices as F  # noqa: E402


def rows(t, months, px=10.0):
    return pd.DataFrame({"ticker": t, "close": px, "adj_close": px, "volume": 1e6, "split": 0.0, "month": months})


def main():
    today = pd.Timestamp.today().normalize()
    allm = pd.date_range("2011-01-31", today + pd.offsets.MonthEnd(0), freq="ME")
    recent = allm[-4:]
    old = pd.concat([rows("OLD", allm[:-1]), rows("AAPL", allm[-5:-1]), rows("IPO", allm[-3:-1]),
                     rows("GONE", allm[-8:-4]), rows("FLAKY", allm[-6:-1])])
    old.to_csv(prices.MONTHLY_PATH, index=False, compression="gzip")
    F.NEW_MISS.write_text("NEWCO\nAAPL")             # AAPL 當初暫時失敗被誤記成抓不到
    calls = []

    def fake_fetch(tks, start="2011-01-01", save=False, **kw):
        calls.append((tuple(sorted(tks)), start))
        if start != "2011-01-01":   # 最近幾個月
            return pd.concat([rows(t, recent if t != "IPO" else allm[-3:]) for t in tks
                              if t in ("OLD", "AAPL", "IPO", "FLAKY", "NEWCO")])
        out = []
        for t in tks:
            if t == "AAPL":
                out.append(rows(t, allm, 20.0))       # 補回完整歷史
            elif t == "IPO":
                out.append(rows(t, allm[-3:]))         # 真的是新上市：一樣短
            elif t == "NEWCO":
                out.append(rows(t, allm[-30:]))
            # FLAKY：補抓完整歷史時暫時失敗 → 什麼都沒回
        return pd.concat(out) if out else pd.DataFrame(columns=["ticker", "close", "adj_close", "volume", "split", "month"])

    F.prices.fetch_monthly = fake_fetch
    F.all_tickers = lambda: ["OLD", "AAPL", "IPO", "GONE", "FLAKY", "NEWCO"]
    F.tw_alt_tickers = lambda have: ([], [])
    F.key_tickers = lambda: []
    F.main()
    res = prices.load_monthly()
    first = res.groupby("ticker")["month"].min()
    n = res.groupby("ticker").size()
    assert first["AAPL"] == allm[0] and n["AAPL"] == len(allm), "AAPL 補回完整歷史"
    assert n["IPO"] == 3, "新上市：保留原本資料＋最新月份"
    assert "GONE" in n.index and n["GONE"] == 4, "沒有最近股價的舊資料不能被刪掉"
    assert n["FLAKY"] == 6, "補抓失敗時保留原本資料"
    assert n["OLD"] == len(allm)
    assert first["NEWCO"] == allm[-30], "被誤記成抓不到、但有最近股價的代號會重新抓完整歷史"
    done = set(F.GAP_DONE.read_text().split())
    assert {"AAPL", "IPO"} <= done and "FLAKY" not in done, done
    assert "AAPL" not in F.NEW_MISS.read_text().split() and "NEWCO" not in F.NEW_MISS.read_text().split()
    full_calls = [c for c in calls if c[1] == "2011-01-01"]
    assert full_calls and set(full_calls[0][0]) == {"AAPL", "IPO", "FLAKY", "NEWCO"}, full_calls
    # 第二天：AAPL、IPO 已補過不再重抓；FLAKY 再試一次
    calls.clear()
    F.main()
    full_calls = [c for c in calls if c[1] == "2011-01-01"]
    assert full_calls and set(full_calls[0][0]) == {"FLAKY"}, full_calls
    print("price history repair ok")
    test_repair_tw_jumps()


def test_repair_tw_jumps():
    """台股日線錯價（超過漲跌幅）改用加權指數當天報酬；最新價不變；美股不動；缺資料的間隔放寬。"""
    from screener.prices import repair_tw_jumps
    idx = pd.bdate_range("2014-01-01", periods=6)
    w = pd.DataFrame({"^TWII": [100, 101, 102, 101, 103, 104.0],
                      "0050.TW": [40, 40.4, 10.2, 10.1, 10.3, 10.4],        # 第 3 天 −75%（分割套錯日期）
                      "6488.TWO": [100, None, None, 120, 121, 122.0],         # 隔 3 天 +20%：在放寬範圍內，不改
                      "SPY": [100, 50, 100, 100, 100, 100.0]})                # 美股不檢查
    fx, fixed = repair_tw_jumps(w.set_index(idx))
    assert list(fixed) == ["0050.TW"] and fixed["0050.TW"] == ["2014-01-03"], fixed
    r = fx["0050.TW"].pct_change()
    assert abs(r.iloc[2] - (102 / 101 - 1)) < 1e-12 and fx["0050.TW"].iloc[-1] == 10.4
    assert fx["SPY"].equals(w.set_index(idx)["SPY"]) and fx["6488.TWO"].iloc[3] == 120
    print("tw jump repair ok")


if __name__ == "__main__":
    main()
