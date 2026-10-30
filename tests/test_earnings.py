"""財報快報的檢查（不需網路）。執行：python -m tests.test_earnings"""
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd

from screener import earnings as E
from screener.common import load_strategy


class FakeSec:
    def __init__(self, data):
        self.data, self.calls = data, 0

    def get_json(self, url):
        self.calls += 1
        for k in sorted(self.data, key=len, reverse=True):     # 最長的關鍵字先比對
            if k in url:
                return self.data[k]
        return None


def subs(rows):
    keys = ["form", "filingDate", "acceptanceDateTime", "items", "accessionNumber", "primaryDocument"]
    return {"filings": {"recent": {k: [r[i] for r in rows] for i, k in enumerate(keys)}}}


def facts(qs):
    """qs：[(季底, 營收, 淨利, 申報日)] → 最小的 companyfacts。"""
    def dur(vals):
        return [{"start": str((pd.Timestamp(e) - pd.Timedelta(days=90)).date()), "end": e, "val": v, "filed": f,
                 "form": "10-Q"} for e, v, f in vals]
    return {"facts": {"us-gaap": {
        "Revenues": {"units": {"USD": dur([(e, r, f) for e, r, n, f in qs])}},
        "NetIncomeLoss": {"units": {"USD": dur([(e, n, f) for e, r, n, f in qs])}},
        "Assets": {"units": {"USD": [{"end": e, "val": 1e12, "filed": f, "form": "10-Q"} for e, r, n, f in qs]}},
        "StockholdersEquity": {"units": {"USD": [{"end": e, "val": 5e11, "filed": f, "form": "10-Q"} for e, r, n, f in qs]}},
    }, "dei": {"EntityCommonStockSharesOutstanding": {"units": {"shares": [
        {"end": str((pd.Timestamp(f) - pd.Timedelta(days=5)).date()), "val": 7.4e9, "filed": f, "form": "10-Q"} for e, r, n, f in qs]}}}}}


def main():
    # ---------- 盤前盤後 ----------
    assert E._timing("2026-10-28T16:05:32.000Z") == "after"
    assert E._timing("2026-10-28T07:01:00.000Z") == "before"
    assert E._timing("2026-10-28T12:00:00.000Z") == "during"
    # ---------- 股價反應 ----------
    days = pd.bdate_range("2026-10-20", "2026-10-30")
    daily = pd.DataFrame({"MSFT": np.linspace(500, 520, len(days)), "SPY": 600.0, "0050.TW": 100.0}, index=days)
    daily.loc["2026-10-29", "MSFT"] = 540.0
    r = E.reaction(daily, "MSFT", "SPY", "2026-10-28", "after")
    assert r["from"] == "2026-10-28" and r["to"] == "2026-10-29" and abs(r["ret"] - (540 / daily.at[pd.Timestamp("2026-10-28"), "MSFT"] - 1)) < 1e-12
    assert r["bench"] == 0 and r["excess"] == r["ret"]
    rb = E.reaction(daily, "MSFT", "SPY", "2026-10-28", "before")
    assert rb["from"] == "2026-10-27" and rb["to"] == "2026-10-28"
    assert E.reaction(daily, "MSFT", "SPY", "2026-10-30", "after") is None, "next close not yet available"
    print("timing / reaction ok")

    # ---------- 美股 8-K 2.02 ----------
    sec = FakeSec({
        "CIK0000789019": subs([("8-K", "2026-10-28", "2026-10-28T16:05:32.000Z", "2.02,9.01", "0000950170-26-100001", "msft-8k.htm"),
                               ("10-Q", "2026-10-28", "2026-10-28T16:10:00.000Z", "", "0000950170-26-100002", "msft-10q.htm"),
                               ("8-K", "2026-10-01", "2026-10-01T09:00:00.000Z", "5.02", "0000950170-26-090000", "x.htm")]),
        "CIK0001652044": subs([("8-K", "2026-10-28", "2026-10-28T16:01:00.000Z", "2.02", "0001652044-26-000100", "goog-8k.htm"),
                               ("8-K", "2026-07-20", "2026-07-20T16:01:00.000Z", "2.02", "0001652044-26-000050", "old.htm")]),
        "companyfacts/CIK0000789019": facts([("2025-09-30", 6.5e10, 2.7e10, "2025-10-29"), ("2025-12-31", 7.0e10, 2.8e10, "2026-01-28"),
                                             ("2026-03-31", 7.2e10, 2.9e10, "2026-04-29"), ("2026-06-30", 7.5e10, 3.0e10, "2026-07-29"),
                                             ("2026-09-30", 7.8e10, 3.24e10, "2026-10-28")]),
    })
    ev = E.us_events(sec, {"MSFT": 789019, "GOOG": 1652044, "GOOGL": 1652044}, pd.Timestamp("2026-10-19"))
    assert sorted(e["id"] for e in ev) == ["GOOG", "MSFT"] and all(e["timing"] == "after" for e in ev), ev
    assert ev[0]["url"].startswith("https://www.sec.gov/Archives/edgar/data/")
    n = E.us_numbers(sec, 789019, "2026-10-28")
    assert n and n["period"] == "2026-09-30" and abs(n["rev_yoy"] - (7.8 / 6.5 - 1)) < 1e-9 and abs(n["ni_yoy"] - 0.2) < 1e-9
    assert abs(n["eps"] - 3.24e10 / 7.4e9) < 1e-9
    assert E.us_numbers(sec, 789019, "2026-12-01") is None, "10-Q for that report not filed yet"
    print("SEC events / numbers ok")

    # ---------- 台股季報、月營收 ----------
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        fin = d / "tw_fin"
        fin.mkdir()
        q = pd.DataFrame({"market": "tw", "id": "2330", "period_end": pd.date_range("2025-03-31", "2026-06-30", freq="QE"),
                          "revenue": [8.4e11, 9.3e11, 9.9e11, 1.0e12, 1.05e12, 1.1e12], "net_income": [3.6e11, 3.9e11, 4.5e11, 4.7e11, 4.9e11, 5.2e11],
                          "op_income": [4.0e11, 4.3e11, 5.0e11, 5.2e11, 5.4e11, 5.7e11], "shares": 2.59e10})
        q.to_csv(fin / "2330.csv.gz", index=False, compression="gzip")
        seen = {}
        assert E.tw_quarterly({"2330", "9999"}, seen, pd.Timestamp("2026-10-30"), fin) == [] and seen == {"2330": "2026-06-30"}
        q2 = pd.concat([q, pd.DataFrame({"market": "tw", "id": "2330", "period_end": [pd.Timestamp("2026-09-30")], "revenue": [1.2e12],
                                         "net_income": [5.6e11], "op_income": [6.1e11], "shares": [2.59e10]})])
        q2.to_csv(fin / "2330.csv.gz", index=False, compression="gzip")
        ev_q = E.tw_quarterly({"2330"}, seen, pd.Timestamp("2026-11-13"), fin)
        assert len(ev_q) == 1 and ev_q[0]["period"] == "2026-09-30" and ev_q[0]["date"] == "2026-11-12"
        assert abs(ev_q[0]["numbers"]["rev_yoy"] - (1.2 / 0.99 - 1)) < 1e-9 and abs(ev_q[0]["numbers"]["eps"] - 5.6e11 / 2.59e10) < 1e-9
        assert E.tw_quarterly({"2330"}, seen, pd.Timestamp("2026-11-14"), fin) == [], "same quarter only once"
        per = pd.date_range("2025-01-31", "2026-09-30", freq="ME")
        wide = pd.DataFrame({"2330": np.arange(len(per)) * 1e9 + 2e11}, index=per)
        sm = {}
        assert E.tw_monthly({"2330"}, wide.iloc[:-1], sm) == [] and sm["2330"] == "2026-08-31"
        ev_m = E.tw_monthly({"2330"}, wide, sm)
        assert len(ev_m) == 1 and abs(ev_m[0]["numbers"]["rev_yoy"] - (wide.iloc[-1, 0] / wide.iloc[-13, 0] - 1)) < 1e-12
        print("TW quarterly / monthly ok")

        # ---------- 整體流程：第一次台股只記錄，美股通知；不重複通知 ----------
        olds = (E.STATE_PATH, E.WATCH_PATH)
        E.STATE_PATH, E.WATCH_PATH = d / "earnings_state.json", d / "earnings_watch.json"
        try:
            cfg = load_strategy()
            q.to_csv(fin / "2330.csv.gz", index=False, compression="gzip")
            all_tw = pd.DataFrame({"id": ["2330", "2317"], "market_cap": [3e13, 2e12]})
            all_us = pd.DataFrame({"id": ["MSFT", "GOOG"], "market_cap": [3.8e12, 3e12]})
            infos = {"tw": pd.DataFrame({"name": ["台積電", "鴻海"], "yahoo": ["2330.TW", "2317.TW"]}, index=["2330", "2317"]),
                     "us": pd.DataFrame({"name": ["MICROSOFT", "ALPHABET"], "yahoo": ["MSFT", "GOOG"], "cik": [789019, 1652044]}, index=["MSFT", "GOOG"])}
            dl = daily.copy()
            dl["GOOG"] = 250.0
            dl.loc["2026-10-29", "GOOG"] = 220.0
            dl["2330.TW"] = 1000.0
            up = lambda t: {"AAPL": "2026-11-02", "MSFT": "2027-01-27"}  # noqa: E731
            kw = dict(results={"tw": (all_tw, None), "us": (all_us, None)}, infos=infos, held={"tw": ["2317"], "us": []},
                      positions={"us": {"BRK-B": {"name": "Berkshire Hathaway B"}}}, watch={"tw": [], "us": []},
                      sec=sec, daily_loader=lambda t, s: dl, upcoming_fn=up, fin_dir=fin)
            sec.data["company_tickers"] = {"0": {"ticker": "BRK-B", "cik_str": 1067983}}
            o1 = E.update(cfg, month_rev=wide.iloc[:-1], today="2026-10-30", **kw)
            assert "MSFT" in o1["digest"] and "GOOG" in o1["digest"] and "台股" not in o1["digest"], o1["digest"]
            assert "營收年增 +20.0%" in o1["digest"] and "相對" in o1["digest"]
            assert o1["site"]["coverage"] == {"tw": 2, "us": 3}
            assert set(__import__("json").loads(E.WATCH_PATH.read_text())["tw"]) == {"2330", "2317"}
            # 寄信失敗（沒有 mark_notified）：下次會再寄同樣的內容
            o1b = E.update(cfg, month_rev=wide.iloc[:-1], today="2026-10-30", **kw)
            assert "MSFT" in o1b["digest"] and "GOOG" in o1b["digest"], "unsent → resend"
            E.mark_notified(o1b["keys"])
            o2 = E.update(cfg, month_rev=wide.iloc[:-1], today="2026-10-31", **kw)
            assert o2["digest"] == "", "no duplicate notifications after a successful send"
            q2.to_csv(fin / "2330.csv.gz", index=False, compression="gzip")
            o3 = E.update(cfg, month_rev=wide, today="2026-11-03", **kw)
            E.mark_notified(o3["keys"])
            assert "【剛公布，下次回報漲跌】" in o3["digest"] and "2330 台積電" in o3["digest"] and "【台股月營收】" in o3["digest"], o3["digest"]
            dl.loc[pd.Timestamp("2026-11-03")] = dl.iloc[-1]
            dl.loc[pd.Timestamp("2026-11-03"), "2330.TW"] = 1050.0
            o4 = E.update(cfg, month_rev=wide, today="2026-11-04", **kw)
            assert "【台股季報：財報後漲跌】" in o4["digest"] and "+5.0%" in o4["digest"] and "2026Q3 季報（11/03 抓到）" in o4["digest"], o4["digest"]
            assert "AAPL" not in o4["digest"], "upcoming within 7 days only"
            assert any(e["id"] == "2330" and e.get("reaction") for e in o4["site"]["recent"])
            print("update flow ok\n" + o1["digest"] + "\n---\n" + o4["digest"])
        finally:
            E.STATE_PATH, E.WATCH_PATH = olds


if __name__ == "__main__":
    main()
