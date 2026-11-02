"""台股籌碼、盤中提醒、資料品質、分析師預估、Google 新聞、52/53 週會計年度。

執行：python -m tests.test_extras（不需網路、不需 pytest）
"""
import importlib.util
import json
import math
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("SCREENER_CACHE", tempfile.mkdtemp())

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from screener import alerts as A  # noqa: E402
from screener import chips, estimates, quality  # noqa: E402


def approx(a, b, abs=1e-6):  # noqa: A002
    return math.isclose(a, b, rel_tol=1e-6, abs_tol=abs)


class MonkeyPatch:
    def __init__(self):
        self._undo = []

    def setattr(self, obj, name, val):
        self._undo.append((obj, name, getattr(obj, name)))
        setattr(obj, name, val)

    def undo(self):
        for obj, name, val in reversed(self._undo):
            setattr(obj, name, val)


# ---------------------------------------------------------------- 籌碼
T86 = {"stat": "OK", "fields": ["證券代號", "證券名稱", "外陸資買進股數(不含外資自營商)", "外陸資賣出股數(不含外資自營商)",
                                "外陸資買賣超股數(不含外資自營商)", "外資自營商買進股數", "外資自營商賣出股數", "外資自營商買賣超股數",
                                "投信買進股數", "投信賣出股數", "投信買賣超股數", "自營商買賣超股數", "自營商買進股數(自行買賣)",
                                "自營商賣出股數(自行買賣)", "自營商買賣超股數(自行買賣)", "自營商買進股數(避險)", "自營商賣出股數(避險)",
                                "自營商買賣超股數(避險)", "三大法人買賣超股數"],
       "data": [["2330", "台積電", "30,000,000", "20,000,000", "10,000,000", "0", "0", "0", "2,000,000", "1,000,000",
                 "1,000,000", "-500,000", "0", "0", "-200,000", "0", "0", "-300,000", "10,500,000"],
                ["00878", "國泰永續高股息", "1", "0", "1", "0", "0", "0", "0", "0", "0", "0", "0", "0", "0", "0", "0", "0", "1"]]}
MARGIN = {"stat": "OK", "tables": [
    {"fields": ["項目", "買進", "賣出", "現金(券)償還", "前日餘額", "今日餘額"], "data": [["融資(交易單位)", "1", "2", "3", "4", "5"]]},
    {"fields": ["代號", "名稱", "買進", "賣出", "現金償還", "前日餘額", "今日餘額", "次一營業日限額", "買進", "賣出", "現券償還",
                "前日餘額", "今日餘額", "次一營業日限額", "資券互抵", "註記"],
     "data": [["2330", "台積電", "100", "50", "0", "20,000", "20,050", "999", "5", "1", "0", "400", "404", "999", "0", ""]]}]}


def test_parse_insti_twse_columns():
    d = chips.parse_insti(T86).set_index("id")
    assert list(d.index) == ["2330"]                  # 5 碼 ETF 不收
    r = d.loc["2330"]
    assert r["foreign"] == 10_000_000                 # 外陸資，不是「外資自營商」
    assert r["trust"] == 1_000_000
    assert r["dealer"] == -500_000                    # 自營商合計，不是自行買賣或避險
    assert r["total"] == 10_500_000


def test_parse_margin_twse_table():
    d = chips.parse_margin(MARGIN).set_index("id")
    assert d.loc["2330", "margin"] == 20_050 and d.loc["2330", "short"] == 404


def test_parse_tpex_openapi_list():
    js = [{"SecuritiesCompanyCode": "6488", "CompanyName": "環球晶", "Foreign Investors include Mainland Area Investors-Difference": "1000",
           "SecuritiesInvestmentTrustCompanies-Difference": "-2000", "Dealers-Difference": "0", "TotalDifference": "-1000"}]
    # OpenAPI 欄位是英文：找不到中文欄位時應該回傳空表，而不是亂對欄位
    d = chips.parse_insti(js)
    assert d.empty or set(d.columns) >= {"id", "foreign"}


def _fake_http(by_url):
    class R:
        def __init__(self, js, code=200):
            self._js, self.status_code = js, code

        def json(self):
            if isinstance(self._js, Exception):
                raise self._js
            return self._js

    class H:
        calls = []

        def get(self, url, timeout=None, headers=None):
            self.calls.append(url)
            for k, v in by_url.items():
                if k in url:
                    return R(v)
            return R(None, 404)
    return H()


def test_fetch_day_and_holiday_vs_network(monkeypatch, tmp_path):
    monkeypatch.setattr(chips, "PATH", tmp_path / "c.csv.gz")
    monkeypatch.setattr(chips, "STATE", tmp_path / "s.json")
    day = pd.Timestamp("2026-10-30")
    x = chips.fetch_day(day, _fake_http({"T86": T86, "MI_MARGN": MARGIN}), sleep=0)
    assert x is not None and not isinstance(x, str)
    r = x.set_index("id").loc["2330"]
    assert r["foreign"] == 10_000_000 and r["margin"] == 20_050
    assert chips.fetch_day(day, _fake_http({"T86": {"stat": "很抱歉，沒有符合條件的資料!"}}), sleep=0) == chips.NO_DATA
    assert chips.fetch_day(day, _fake_http({}), sleep=0) is None
    # 連不上的日子不能被記成假日（之後要再試）
    out = chips.update(today="2026-11-02", http=_fake_http({}), sleep=0)
    assert out["days_total"] == 0
    assert json.loads((tmp_path / "s.json").read_text())["holiday"] == []
    # 證交所明確說沒資料、而且是三天前 → 記成假日
    chips.update(today="2026-11-02", http=_fake_http({"T86": {"stat": "沒有符合條件的資料"}}), sleep=0, backfill=3)
    hol = json.loads((tmp_path / "s.json").read_text())["holiday"]
    assert hol == ["2026-10-29"]                      # 11/02、10/30 還在三天內，可能只是還沒公布


def test_summary_and_series():
    days = pd.bdate_range("2026-09-01", periods=25)
    rows = []
    for i, d in enumerate(days):
        rows.append({"date": d, "id": "2330", "foreign": 1000 * (i + 1), "trust": -1000, "dealer": 0, "total": 1000 * i,
                     "margin": 1000 + 10 * i, "short": 50})
    df = pd.DataFrame(rows)
    s = chips.summary(df)
    r = s["rows"]["2330"]
    assert s["as_of"] == str(days[-1].date()) and s["days"] == 25
    assert r["foreign5"] == sum(range(21, 26))          # 張（股數 / 1000）
    assert r["trust20"] == -20
    assert r["margin"] == 1240 and r["short"] == 50
    assert approx(r["margin_chg20"], 1240 / 1050 - 1, abs=1e-4)
    assert approx(r["short_ratio"], 50 / 1240, abs=1e-4)
    se = chips.series(df, days=10)["2330"]
    assert len(se["t"]) == 10 and se["f"][-1] == 25 and se["mb"][-1] == 1240
    assert se["t"][-1] == (days[-1] - pd.Timestamp("1970-01-01")).days


# ---------------------------------------------------------------- 盤中提醒
def _bars(closes, highs=None, lows=None):
    c = np.asarray(closes, float)
    return pd.DataFrame({"open": c, "high": highs if highs is not None else c * 1.01, "low": lows if lows is not None else c * 0.99, "close": c})


def test_market_open():
    assert A.market_open("tw", pd.Timestamp("2026-11-02 02:00", tz="UTC"))       # 台北 10:00 週一
    assert not A.market_open("tw", pd.Timestamp("2026-11-02 06:00", tz="UTC"))   # 台北 14:00
    assert A.market_open("us", pd.Timestamp("2026-11-02 15:00", tz="UTC"))       # 紐約 10:00（冬令）
    assert not A.market_open("us", pd.Timestamp("2026-11-07 15:00", tz="UTC"))   # 週六


def test_check_rules():
    cfg = {"rules": {"stop": True, "levels": True, "technical": True, "fair_value": True}, "technical": {"ma": [20], "kd": [9, 3, 3]}}
    up = list(np.linspace(100, 90, 30)) + [95]          # 一路跌、今天反彈站上 20 日均線
    w = [{"mkt": "tw", "id": "2330", "yahoo": "2330.TW", "tag": "2330 台積電", "stop": 96, "above": 94, "below": None,
          "fair_low": 96, "fair_high": None}]
    hits = A.check(w, {"2330.TW": _bars(up)}, cfg)
    keys = {h["key"] for h in hits}
    assert "tw:2330:stop" in keys and "tw:2330:above:94" in keys and "tw:2330:fair_low" in keys
    assert "tw:2330:ma20:up" in keys
    assert all("2330 台積電 現價 95.00" in h["text"] for h in hits)
    # 沒有報價就略過
    assert A.check(w, {}, cfg) == []
    # 規則關掉就不提醒
    off = {"rules": {"stop": False, "levels": False, "technical": False, "fair_value": False}}
    assert A.check(w, {"2330.TW": _bars(up)}, off) == []


def test_kd_golden_cross_low():
    c = list(np.linspace(100, 60, 40)) + [60.5]          # 跌深後小反彈：K 由下往上穿過 D（K 仍在 30 以下）
    hits = A.check([{"mkt": "us", "id": "X", "yahoo": "X", "tag": "X"}], {"X": _bars(c)}, {"rules": {"technical": True, "stop": False, "levels": False, "fair_value": False}, "technical": {"ma": [], "kd": [9, 3, 3]}})
    assert any(h["key"] == "us:X:kd:golden" for h in hits)


def test_new_only_once_per_day():
    a = [{"key": "k1", "text": "x"}, {"key": "k2", "text": "y"}]
    f1, st = A.new_only(a, {}, "2026-11-02")
    f2, st = A.new_only(a + [{"key": "k3", "text": "z"}], st, "2026-11-02")
    f3, st = A.new_only(a, st, "2026-11-03")
    assert len(f1) == 2 and [x["key"] for x in f2] == ["k3"] and len(f3) == 2 and list(st) == ["2026-11-03"]


def _load_script():
    spec = importlib.util.spec_from_file_location("alerts_script", ROOT / "scripts" / "alerts.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_build_watch_and_levels(tmp_path, monkeypatch):
    S = _load_script()
    port = {"stop_loss": {"pct": 0.15}, "holdings": [{"kind": "stock", "market": "tw", "id": "2330", "name": "台積電"},
                                                      {"kind": "etf", "market": "etf", "id": "0050.TW"}]}
    state = {"entry": {"tw": {"2330": {"px": 1000}}}}
    stocks = {"tw": {"2330": {"id": "2330", "name": "TSMC", "fair_low": 900, "fair_high": 1300}}, "us": {}}
    positions = {"tw": {}, "us": {"BRK-B": {"cost": 400, "name": "Berkshire"}}}
    cfg = {"levels": {"tw": {"6488": {"below": 300}}, "us": {}, "etf": {"VT": {"above": 150}, "0050.TW": {"below": 50}}}}
    W = {(w["mkt"], w["id"]): w for w in S.build_watch(cfg, port, state, stocks, positions, {"tw": ["2454"], "us": ["NVDA"]})}
    assert approx(W[("tw", "2330")]["stop"], 850) and W[("tw", "2330")]["fair_low"] == 900
    assert approx(W[("us", "BRK-B")]["stop"], 340) and W[("us", "BRK-B")]["yahoo"] == "BRK-B"
    assert W[("tw", "6488")]["below"] == 300 and W[("tw", "6488")]["yahoo"] == "6488.TW"
    assert W[("etf", "VT")]["sess"] == "us" and W[("etf", "0050.TW")]["sess"] == "tw"
    assert ("tw", "2454") in W and ("us", "NVDA") in W
    # alert_levels.yaml（網站產生的 JSON 格式）和 alerts.yaml 的 levels 合併
    cfgdir = tmp_path / "config"
    cfgdir.mkdir()
    (cfgdir / "alert_levels.yaml").write_text('# 註解\n{\n  "tw": {"2330": {"above": 1500}},\n  "us": {},\n  "etf": {"VT": {"below": 100}}\n}\n', encoding="utf-8")
    monkeypatch.setattr(S, "ROOT", tmp_path)
    L = S.load_levels({"levels": {"tw": {"2317": {"below": 100}}, "us": {}}})
    assert L["tw"] == {"2317": {"below": 100}, "2330": {"above": 1500}} and L["etf"] == {"VT": {"below": 100}}
    # 格式壞掉時不要整個掛掉
    (cfgdir / "alert_levels.yaml").write_text("{tw: [", encoding="utf-8")
    assert S.load_levels({})["tw"] == {}


def test_repo_alert_levels_file_is_valid_json_yaml():
    import yaml
    txt = (ROOT / "config" / "alert_levels.yaml").read_text(encoding="utf-8")
    y = yaml.safe_load(txt)
    j = json.loads("\n".join(l for l in txt.splitlines() if not l.strip().startswith("#")))   # 網站用 JSON 讀
    assert y == j and set(y) == {"tw", "us", "etf"}


# ---------------------------------------------------------------- 資料品質
def test_quality_stock_issues():
    df = pd.DataFrame([
        {"id": "ADRX", "market_cap": 2e11, "net_income": -1e8, "revenue": 1e9, "share_growth": 0.01, "period_end": "2026-06-30"},
        {"id": "COST", "market_cap": 4e11, "net_income": 7e9, "revenue": np.nan, "share_growth": 0.0, "period_end": "2026-08-31"},
        {"id": "SPLT", "market_cap": 1e9, "net_income": 1e7, "revenue": 1e8, "share_growth": 3.5, "period_end": "2025-12-31"},
    ])
    idx = pd.date_range("2025-10-31", periods=13, freq="ME")
    close = pd.DataFrame({"ADRX": np.linspace(10, 12, 13), "COST": np.linspace(800, 900, 13),
                          "SPLT": [10] * 6 + [50] + [50] * 6}, index=idx)
    close.loc[idx[-2:], "COST"] = np.nan                          # 兩個月沒更新
    iss = quality.stock_issues(df, "us", close, {"ADRX": "ADRX", "COST": "COST", "SPLT": "SPLT"}, "2026-11-02")
    k = {(o["id"], o["kind"]) for o in iss}
    assert ("ADRX", "mcap_suspect") in k and ("COST", "mcap_suspect") not in k   # 有獲利的不算市值可疑
    assert ("COST", "rev_missing") in k and ("COST", "stale_px") in k
    assert ("SPLT", "shares_jump") in k and ("SPLT", "stale_fin") in k and ("SPLT", "px_spike") in k
    assert next(o for o in iss if o["kind"] == "mcap_suspect")["text"].startswith("算出的市值 2,000 億美元")


def test_quality_email_only_when_new_or_failed():
    rep = quality.report([{"market": "us", "id": "ADRX", "kind": "mcap_suspect", "label": "市值可能算錯", "severe": True, "text": ""}],
                         [{"key": "x", "ok": True, "text": "ok"}], "2026-11-02")
    assert quality.email_lines(rep, rep) == []                       # 昨天就有 → 不再提
    assert quality.email_lines(rep, {"issues": []})[1].startswith("  • 新出現 1 檔")
    rep2 = quality.report([], [{"key": "y", "ok": False, "text": "美股財報最後一筆申報日 2026-10-01（32 天前）"}], "2026-11-02")
    assert "32 天前" in "\n".join(quality.email_lines(rep2, None))
    assert quality.flags_by_stock(rep["issues"]) == {"us:ADRX": ["市值可能算錯"]}


# ---------------------------------------------------------------- 分析師預估
def test_estimates_update_and_site(tmp_path, monkeypatch):
    monkeypatch.setattr(estimates, "PATH", tmp_path / "e.json")
    fake = {"NVDA": {"target": {"mean": 250.0, "high": 300.0, "low": 150.0, "current": 200.0},
                     "rating": {"strongBuy": 10, "buy": 30, "hold": 5, "sell": 1, "strongSell": 0},
                     "eps": {"0q": {"avg": 1.2, "numberOfAnalysts": 40}, "0y": {"avg": 4.5}, "+1y": {"avg": 6.0, "growth": 0.33}},
                     "last": {"date": "2026-08-27", "est": 1.0, "actual": 1.08, "surprise": 0.08}}}
    # 週一整批更新
    r = estimates.update(["NVDA", "AAPL"], today="2026-11-02", fetch=lambda s: fake.get(s), sleep=0)
    assert r["full"] and r["updated"] == 1
    # 週二只更新最近公布財報的和還沒有資料的
    seen = []
    estimates.update(["NVDA", "AAPL"], today="2026-11-03", recent=["NVDA"], fetch=lambda s: seen.append(s) or fake.get(s), sleep=0)
    assert seen == ["NVDA", "AAPL"]
    t = estimates.site_table()["NVDA"]
    assert t["target"] == 250 and approx(t["upside"], 0.25) and t["n"] == 40 and t["eps_ny_growth"] == 0.33
    db = estimates.load()
    assert estimates.surprise_text(db["NVDA"], "2026-08-27") == "EPS 1.08 vs 預估 1.00（超乎預期 +8.0%）"
    assert estimates.surprise_text(db["NVDA"], "2026-11-20") == ""           # 不是同一次財報


# ---------------------------------------------------------------- Google 新聞
def test_google_news_parse():
    from screener import events
    rss = b"""<?xml version="1.0"?><rss><channel>
      <item><title>NVIDIA beats estimates - Reuters</title><link>https://x/1</link><pubDate>Mon, 02 Nov 2026 01:00:00 GMT</pubDate><source url="r">Reuters</source></item>
      <item><title>Old news - AP</title><link>https://x/2</link><pubDate>Mon, 01 Jan 2024 01:00:00 GMT</pubDate><source url="a">AP</source></item>
    </channel></rss>"""

    class H:
        def get(self, url, timeout=None, headers=None):
            class R:
                status_code, content = 200, rss
            assert "news.google.com" in url
            return R()
    out = events.google_news("NVIDIA", since=pd.Timestamp("2026-10-30"), http=H())
    assert out == [{"date": "2026-11-02 01:00", "title": "NVIDIA beats estimates", "url": "https://x/1", "source": "Reuters"}]


# ---------------------------------------------------------------- 52/53 週會計年度（COST、PEP、AZO）
def test_52_53_week_fiscal_year_q4_derived():
    from screener.data_us import duration_to_quarters
    f = [  # Costco 類型：12 週＋12 週＋12 週＋16 週；全年 52 週（364 天）
        {"start": "2024-09-02", "end": "2024-11-24", "val": 60.0, "filed": "2024-12-20", "form": "10-Q", "fp": "Q1", "fy": 2025},
        {"start": "2024-09-02", "end": "2025-02-16", "val": 123.0, "filed": "2025-03-10", "form": "10-Q", "fp": "Q2", "fy": 2025},
        {"start": "2024-09-02", "end": "2025-05-11", "val": 185.0, "filed": "2025-06-01", "form": "10-Q", "fp": "Q3", "fy": 2025},
        {"start": "2024-09-02", "end": "2025-08-31", "val": 270.0, "filed": "2025-10-08", "form": "10-K", "fp": "FY", "fy": 2025},
    ]
    q = duration_to_quarters(f).set_index("end")
    assert pd.Timestamp("2025-08-31") in q.index
    assert approx(q.loc[pd.Timestamp("2025-08-31"), "val"], 85.0)     # 270 − 185（16 週的第四季）


if __name__ == "__main__":
    import inspect
    ok = 0
    for name, fn in list(globals().items()):
        if not name.startswith("test_") or not callable(fn):
            continue
        args, mp = {}, MonkeyPatch()
        params = inspect.signature(fn).parameters
        if "monkeypatch" in params:
            args["monkeypatch"] = mp
        if "tmp_path" in params:
            args["tmp_path"] = Path(tempfile.mkdtemp())
        try:
            fn(**args)
            ok += 1
            print("✔", name)
        finally:
            mp.undo()
    print(f"全部通過：{ok} 項")
