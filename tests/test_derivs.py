"""衍生性商品資料庫：期交所 CSV 解析、近月連續報酬（換月、到期日結算價 0）、回補進度、網站摘要。

執行：python -m tests.test_derivs（不需網路、不需 pytest）
"""
import json
import os
import sys
import tempfile
from pathlib import Path

os.environ["SCREENER_CACHE"] = tempfile.mkdtemp()

import pandas as pd  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from screener import derivs as D  # noqa: E402

FUT_HEAD = "交易日期,契約,到期月份(週別),開盤價,最高價,最低價,收盤價,漲跌價,漲跌%,成交量,結算價,未沖銷契約數,最後最佳買價,最後最佳賣價,歷史最高價,歷史最低價,是否因訊息面暫停交易,交易時段,價差對單式委託成交量\n"


def fut_row(d, exp, settle, session="一般", close=None):
    c = close if close is not None else settle
    return f"{d},TX,{exp}  ,{c},{c},{c},{c},0,0%,100,{settle},1000,{c},{c},{c},{c},,{session},,\n"


FUT = FUT_HEAD + "".join([
    fut_row("2026/10/20", "202610", 100), fut_row("2026/10/20", "202611", 101),
    fut_row("2026/10/20", "202610", 999, session="盤後"),                       # 盤後：不要
    "2026/10/20,TX,202610/202611,1,1,1,1,0,0%,5,-,0,-,-,-,-,,一般,,\n",       # 價差單：不要
    fut_row("2026/10/21", "202610", 0, close=104), fut_row("2026/10/21", "202611", 105),   # 到期日：結算價欄是 0
    fut_row("2026/10/22", "202611", 110), fut_row("2026/10/22", "202612", 111),
])

OPT_HEAD = "交易日期,契約,到期月份(週別),履約價,買賣權,開盤價,最高價,最低價,收盤價,成交量,結算價,未沖銷契約數,最後最佳買價,最後最佳賣價,歷史最高價,歷史最低價,是否因訊息面暫停交易,交易時段,漲跌價,漲跌%,契約到期日,\n"


def opt_row(d, exp, k, cp, px=10, session="一般"):
    return f"{d},TXO,{exp},{k}.0000,{cp},{px},{px},{px},{px},5,{px},50,{px},{px},-,-,,{session},0,0%,20261021\n"


OPT = OPT_HEAD + "".join([
    opt_row("2026/10/20", "202610", 100, "買權"), opt_row("2026/10/20", "202610", 95, "賣權"),
    opt_row("2026/10/20", "202610", 130, "買權"),                          # 超出 ±10%：不要
    opt_row("2026/10/20", "202611", 100, "賣權"), opt_row("2026/10/20", "202612", 100, "賣權"),   # 第 3 個到期月：不要
    opt_row("2026/10/20", "202610W4", 100, "買權"), opt_row("2026/10/20", "202610F4", 100, "賣權"),  # 週選：不要
    opt_row("2026/10/20", "202610", 100, "賣權", session="盤後"),          # 盤後：不要
])

PCR = "日期,賣權成交量,買權成交量,買賣權成交量比率%,賣權未平倉量,買權未平倉量,買賣權未平倉量比率%\n" \
      "2026/10/21,200,100,200.00,300,400,75.00,\n2026/10/20,100,100,100.00,400,400,100.00,\n"

INSTI = "日期,商品名稱,身份別,多方交易口數,多方交易契約金額(千元),空方交易口數,空方交易契約金額(千元),多空交易口數淨額,多空交易契約金額淨額(千元),多方未平倉口數,多方未平倉契約金額(千元),空方未平倉口數,空方未平倉契約金額(千元),多空未平倉口數淨額,多空未平倉契約金額淨額(千元)\n" \
        "2026/10/20,臺股期貨,外資,1,1,1,1,0,0,1000,1,3000,1,-2000,-1\n" \
        "2026/10/20,小型臺指期貨,外資,1,1,1,1,0,0,400,1,0,1,400,1\n" \
        "2026/10/20,微型臺指期貨,外資,1,1,1,1,0,0,0,1,200,1,-200,-1\n" \
        "2026/10/20,臺股期貨,投信,1,1,1,1,0,0,10,1,0,1,10,1\n" \
        "2026/10/20,電子期貨,外資,1,1,1,1,0,0,10,1,0,1,10,1\n"


def test_parse_fut_filters_and_zero_settle():
    f = D.parse_fut(FUT)
    assert len(f) == 6 and set(f["expiry"]) == {"202610", "202611", "202612"}
    exp_day = f[(f["date"] == "2026-10-21") & (f["expiry"] == "202610")].iloc[0]
    assert pd.isna(exp_day["settle"]) and exp_day["close"] == 104       # 0 → 沒有資料
    u = D.front_settle(f)
    assert list(u.values) == [100, 105, 110]                             # 到期日當天近月改成下個月


def test_front_returns_roll_without_gap():
    r = D.front_returns(D.parse_fut(FUT))
    # 10/21：近月 10 月到期沒有結算價 → 用 11 月合約 101→105；10/22：11 月 105→110
    assert abs(r["2026-10-21"] - (105 / 101 - 1)) < 1e-12
    assert abs(r["2026-10-22"] - (110 / 105 - 1)) < 1e-12
    assert len(r) == 2


def test_parse_opt_keeps_near_monthly_atm():
    under = pd.Series({pd.Timestamp("2026-10-20"): 100.0})
    o = D.parse_opt(OPT, under)
    assert len(o) == 3, o
    assert set(o["expiry"]) == {"202610", "202611"} and set(o["cp"]) == {"C", "P"}
    assert o["strike"].max() <= 110


def test_parse_pcr_and_insti():
    p = D.parse_pcr(PCR).set_index("date")
    assert p.loc["2026-10-21", "pc_oi"] == 0.75 and p.loc["2026-10-20", "pc_vol"] == 1.0
    i = D.parse_insti_taifex(INSTI)
    assert len(i) == 4                                                     # 電子期貨不要
    net = D.foreign_net_tx(i)
    assert net.iloc[0] == -2000 + 400 * 0.25 - 200 * 0.05                  # 大台＋小台/4＋微台/20
    fm = D.parse_insti_finmind({"status": 200, "data": [
        {"futures_id": "TX", "date": "2019-01-02", "institutional_investors": "外資及陸資",
         "long_open_interest_balance_volume": 50, "short_open_interest_balance_volume": 80},
        {"futures_id": "XYZ", "date": "2019-01-02", "institutional_investors": "外資",
         "long_open_interest_balance_volume": 1, "short_open_interest_balance_volume": 0}]})
    assert len(fm) == 1 and fm.iloc[0]["net_oi"] == -30 and fm.iloc[0]["who"] == "foreign"


def test_html_error_page_is_empty():
    assert D.parse_fut("<!DOCTYPE HTML><html>查詢日期超過範圍</html>").empty
    assert D.parse_pcr("").empty


class FakeHTTP:
    def __init__(self):
        self.posts = []

    def post(self, url, data=None, headers=None, timeout=None):
        self.posts.append((url, dict(data)))

        class R:
            status_code = 200
            content = {D.FUT_URL: FUT, D.OPT_URL: OPT, D.PCR_URL: PCR, D.INSTI_URL: INSTI}[url].encode("big5", errors="replace")
        return R()

    def get(self, url, params=None, timeout=None, headers=None):
        class R:
            status_code = 200

            def json(self):
                return {"status": 200, "data": []}
        return R()


def test_update_budget_progress_and_resume(monkeypatch=None):
    import screener.prices as P
    orig = (P._download, P._to_long)
    P._download = lambda *a, **k: pd.DataFrame()
    P._to_long = lambda *a, **k: pd.DataFrame()
    try:
        h = FakeHTTP()
        res = D.update(max_requests=6, today="2026-11-06", http=h, sleep=0)
        # 第 1 次請求給美國 VIX（放最前面，回補期間才不會被期交所用完額度），其餘 5 次給期貨
        assert len(h.posts) == 5 and res["requests_left"] == 0
        st = D.load_state()
        assert st["fut_TX"] == ["2007-01", "2007-02", "2007-03", "2007-04", "2007-05"]
        assert not res["progress"]["complete"]
        # 第二次接著抓，不會重抓已完成的月份
        h2 = FakeHTTP()
        D.update(max_requests=3, today="2026-11-06", http=h2, sleep=0)
        assert [d["queryStartDate"] for _, d in h2.posts] == ["2007/06/01", "2007/07/01"]
        # 期貨資料有存、重複抓同一天不會變兩筆
        f = D._load("fut_daily")
        assert len(f) == len(f.drop_duplicates(["date", "contract", "expiry"]))
        s = D.summary()
        assert any(x["key"] == "tx_front" for x in s["signals"])
        json.dumps(s)                                                      # 網站要能存成 JSON
    finally:
        P._download, P._to_long = orig


def test_progress_ignores_recent_two_months():
    st = {k: [p.strftime("%Y-%m") for p in pd.period_range(s, "2026-09", freq="M")]
          for k, s in [("fut_TX", "2007-01"), ("fut_MTX", "2007-01"), ("fut_TMF", "2024-07"), ("opt_TXO", "2007-01"),
                       ("pcr", "2007-01"), ("insti", "2018-06")]}
    assert D.progress(st, "2026-11-06")["complete"]


if __name__ == "__main__":
    ok = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            ok += 1
            print("✔", name)
    print(f"全部通過：{ok} 項")
