"""中文名稱與美股股數後備的檢查（不需網路）。執行：python -m tests.test_names_shares"""
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd

from screener import common, names
from screener import data_us as U
from screener import earnings as E
from screener.metrics import fundamental_panel


def check_names():
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        pd.DataFrame({"cik": [1, 2, 3], "ticker": ["CAT", "AADX", "SPY"],
                      "name": ["CATERPILLAR INC", "Applied Aerospace & Defense, Inc.", "SPDR S&P 500 ETF TRUST"],
                      "industry": ["Machinery & Computers", "Transportation Equipment", "Holding & Investment"]}
                     ).to_csv(d / "us_universe.csv", index=False)
        pd.DataFrame({"stock_id": ["2330", "6199"], "stock_name": ["台積電", "天品"],
                      "industry_category": ["半導體業", "其他"]}).to_csv(d / "tw_universe.csv", index=False)
        pd.DataFrame({"stock_id": ["1234"], "stock_name": ["已下市"], "industry_category": ["食品工業"]}
                     ).to_csv(d / "tw_delisted.csv", index=False)
        old = common.CACHE_DIR
        common.CACHE_DIR = d
        names.universe.cache_clear()
        try:
            assert names.tag("us", "CAT", "CATERPILLAR INC") == "CAT 開拓重工｜工程機械（挖土機、發電機）"
            assert names.tag("us", "AADX") == "AADX Applied Aerospace & Defense｜運輸設備（汽車、航太）"
            assert names.tag("tw", "2330", "台積電") == "2330 台積電｜晶圓代工龍頭"
            assert names.tag("tw", "2330.TW") == "2330 台積電｜晶圓代工龍頭"
            assert names.tag("tw", "6199") == "6199 天品", "「其他」不當說明"
            assert names.tag("tw", "1234") == "1234 已下市｜食品工業", "下市公司也有名稱"
            assert names.tag("etf", "VT").startswith("VT Vanguard Total World Stock｜")
            assert names.tag("us", "SPY").startswith("SPY SPDR S&P 500｜"), "ETF 清單優先於 SEC 名稱"
            assert names.tag("tw", "0050.TW").startswith("0050 元大台灣50｜")
            assert names.tag("us", "ZZZZ", "ACME WIDGETS INC") == "ZZZZ Acme Widgets"
            assert names.tag("us", "BRK-B", "Berkshire Hathaway B").startswith("BRK-B 波克夏")
            t = names.site_table()
            assert t["us"]["CAT"][0] == "開拓重工" and t["tw"]["2330"] == ["台積電", "晶圓代工龍頭"] and "VT" in t["etf"]
            assert t["us"]["META"][0] == "Meta", "手動整理的公司即使不在清單也有"
        finally:
            common.CACHE_DIR = old
            names.universe.cache_clear()
    for a, b in [("MICROSOFT CORP", "Microsoft"), ("AGILENT TECHNOLOGIES, INC.", "Agilent Technologies"),
                 ("HORTON D R INC /DE/", "Horton D R"), ("AT&T INC.", "AT&T"), ("3M CO", "3M"),
                 ("AMAZON COM INC", "Amazon"), ("US BANCORP \\DE\\", "US Bancorp")]:
        assert names.short_en(a) == b, (a, names.short_en(a))
    # 財報快報：第一行代號＋中文名＋說明，第二行數字
    ln = E._line({"market": "us", "id": "CAT", "name": "CATERPILLAR INC", "type": "earnings", "date": "2026-10-29",
                  "timing": "before", "reaction": {"ret": -0.093, "bench": -0.004, "excess": -0.089},
                  "numbers": {"rev_yoy": 0.05}})
    assert ln.startswith("・CAT 開拓重工｜工程機械") and "\n    10/29 盤前公布；財報後 -9.3%" in ln and "營收年增 +5.0%" in ln, ln
    print("names ok\n" + ln)


def facts(dei=None, wavg=None, bs=None, n=6):
    """最小 companyfacts：n 季（2025Q1 起）營收／淨利／總資產＋指定的股數資料。"""
    ends = pd.date_range("2025-03-31", periods=n, freq="QE")
    filed = [e + pd.Timedelta(days=35) for e in ends]

    def dur(vals):
        return [{"start": str((e - pd.Timedelta(days=89)).date()), "end": str(e.date()), "val": v, "filed": str(f.date()),
                 "form": "10-Q"} for e, v, f in zip(ends, vals, filed)]
    g = {"Revenues": {"units": {"USD": dur([100.0] * n)}}, "NetIncomeLoss": {"units": {"USD": dur([10.0] * n)}},
         "Assets": {"units": {"USD": [{"end": str(e.date()), "val": 1000.0, "filed": str(f.date()), "form": "10-Q"}
                                      for e, f in zip(ends, filed)]}}}
    if wavg is not None:
        g["WeightedAverageNumberOfDilutedSharesOutstanding"] = {"units": {"shares": wavg(ends, filed)}}
    if bs is not None:
        g["CommonStockSharesOutstanding"] = {"units": {"shares": bs(ends, filed)}}
    out = {"facts": {"us-gaap": g}}
    if dei is not None:
        out["facts"]["dei"] = {"EntityCommonStockSharesOutstanding": {"units": {"shares": dei(ends, filed)}}}
    return out, ends


def check_shares():
    # JPM 型：封面股數日期＝季底 → 要採用
    cf, ends = facts(dei=lambda es, fs: [{"end": str(e.date()), "val": 2.7e9 - i * 1e7, "filed": str(f.date()), "form": "10-Q"}
                                         for i, (e, f) in enumerate(zip(es, fs))])
    q = U.company_quarters(cf, 19617)
    assert q["shares"].notna().all() and abs(q["shares"].iloc[-1] - (2.7e9 - 5e7)) < 1, q[["period_end", "shares"]]
    # META 型：沒有（非分類別的）封面股數 → 用稀釋加權平均股數；第 4 季只有全年數字時用全年
    def wavg(es, fs):
        rows = [{"start": str((e - pd.Timedelta(days=89)).date()), "end": str(e.date()), "val": 2.6e9 - i * 5e6,
                 "filed": str(f.date()), "form": "10-Q"} for i, (e, f) in enumerate(zip(es, fs)) if e.month != 12]
        rows.append({"start": "2025-01-01", "end": "2025-12-31", "val": 2.59e9, "filed": "2026-02-01", "form": "10-K"})
        rows.append({"start": "2025-01-01", "end": "2025-06-30", "val": 9.9e9, "filed": "2025-08-04", "form": "10-Q"})  # 半年，不該用
        return rows
    cf, ends = facts(wavg=wavg)
    q = U.company_quarters(cf, 1326801).set_index("period_end")
    assert q["shares"].notna().all(), q["shares"]
    assert q.at[pd.Timestamp("2025-06-30"), "shares"] == 2.6e9 - 5e6, "單季優先於半年"
    assert q.at[pd.Timestamp("2025-12-31"), "shares"] == 2.59e9, "第 4 季用全年"
    # 封面（季底之後的日期）仍然優先
    cf, ends = facts(dei=lambda es, fs: [{"end": str((e + pd.Timedelta(days=20)).date()), "val": 1e9, "filed": str(f.date()),
                                          "form": "10-Q"} for e, f in zip(es, fs)], wavg=wavg)
    q = U.company_quarters(cf, 1)
    assert (q["shares"] == 1e9).all() and (q["shares_date"] > q["period_end"]).all()
    print("SEC shares fallbacks ok")

    # 面板：股數缺漏時沿用 4 季內的舊股數，股數膨脹率不算；超過 4 季不沿用
    pe = pd.date_range("2022-03-31", periods=12, freq="QE")
    sh = [1e9] * 6 + [np.nan] * 6
    f = pd.DataFrame({"market": "us", "id": "X", "period_end": pe, "available_date": pe + pd.Timedelta(days=40),
                      "shares": sh, "shares_date": pe + pd.Timedelta(days=30)})
    for c in ("revenue", "gross_profit", "op_income", "nonop", "pretax", "net_income", "rd", "cfo", "capex", "cfi", "cff",
              "cash", "st_inv", "receivables", "inventory", "current_assets", "total_assets", "current_liab", "total_liab",
              "equity", "goodwill", "intangibles", "lt_inv", "debt"):
        f[c] = 100.0
    p = fundamental_panel(f).set_index("period_end")
    assert p["adj_shares"].iloc[:10].notna().all() and p["adj_shares"].iloc[10:].isna().all(), p["adj_shares"]
    assert p["share_growth"].iloc[6:10].isna().all() and p["share_growth"].iloc[4:6].notna().all()
    print("panel share forward-fill ok")


def main():
    check_names()
    check_shares()


if __name__ == "__main__":
    main()
