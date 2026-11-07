"""總經指標：國發會 ZIP 解析、PMI 新聞標題、轉換、轉折狀態、事件產生（第一次只建基準、有新資料且狀態改變才通知）。

執行：python -m tests.test_macro_ind（不需網路、不需 pytest）
"""
import io
import json
import os
import sys
import tempfile
import zipfile
from pathlib import Path

os.environ["SCREENER_CACHE"] = tempfile.mkdtemp()

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from screener import macro_ind as M  # noqa: E402

NOW = pd.Timestamp("2026-11-07 09:00", tz="Asia/Taipei")


def _zip():
    head = '﻿"Date","領先指標綜合指數","領先指標不含趨勢指數","同時指標綜合指數","同時指標不含趨勢指數","落後指標綜合指數","落後指標不含趨勢指數","景氣對策信號綜合分數","景氣對策信號"\n'
    rows = "198201,12.2,100.3,13.1,107.1,13.0,106.9,-,-\n202608,143.0,104.5,143.8,105.1,144.8,105.8,38,黃紅\n202609,143.2,104.6,143.9,105.2,144.9,105.9,41,紅\n"
    comp = '﻿"Date","電力(企業)總用電量(十億度)","工業及服務業受僱員工淨進入率(%)","外銷訂單動向指數(以家數計)"\n202608,20.1,0.05,51.2\n202609,,,48.7\n'
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        z.writestr("景氣指標與燈號.csv", head + rows)
        z.writestr("schema-景氣指標與燈號.csv", '"name","title"\n')
        z.writestr("領先指標構成項目.csv", comp)
    return b.getvalue()


def test_parse_ndc_zip_columns_and_light():
    d = M.parse_ndc_zip(_zip())
    assert d.index[-1] == pd.Timestamp("2026-09-01")
    assert d.loc["2026-09-01", "領先指標不含趨勢指數"] == 104.6
    assert d.loc["2026-09-01", "燈號"] == "紅" and pd.isna(d.loc["1982-01-01", "景氣對策信號綜合分數"])
    assert "電力(企業)總用電量" in d.columns and "工業及服務業受僱員工淨進入率" in d.columns   # 只去掉最後面的單位括號
    assert d.loc["2026-09-01", "外銷訂單動向指數"] == 48.7


def test_pmi_titles():
    f = M.pmi_from_title
    assert f("cn", "中國國家統計局10月製造業PMI降至49.3，非製造業PMI回落至49.7") == 49.3
    assert f("cn", "中國10月製造與服務業PMI雙陷收縮，綜合PMI跌至49.7") is None          # 綜合 PMI 不算
    assert f("cn", "財新製造業PMI 50.6") is None                                        # 財新不是官方
    assert f("tw", "10月PMI 62.5%擴張趨緩中經院：AI熱潮非一帆風順") == 62.5
    assert f("tw", "PMI連13月擴張卻降溫！連賢明") is None                               # 沒有 xx.x
    assert f("tw", "中國製造業PMI 49.3") is None                                        # 別的國家
    assert f("us", "US Manufacturing PMI at 55.5%; October 2026 ISM Manufacturing PMI Report") == 55.5
    assert f("us", "S&P Global US Manufacturing PMI 54.9") is None
    assert f("jp", "S&P Global Japan Manufacturing PMI slows to 7-month low of 53.3") == 53.3
    assert f("jp", "Japan services activity expands, PMI 52.1") is None


def test_news_pmi_vote():
    items = [{"date": "2026-11-01 01:00", "title": "中國10月製造業PMI降至49.3", "url": "a", "source": "x"},
             {"date": "2026-11-01 02:00", "title": "大陸製造業PMI降至49.3 連4個月3度萎縮", "url": "b", "source": "y"},
             {"date": "2026-11-01 03:00", "title": "中國製造業PMI 49.8 讀錯的一則", "url": "c", "source": "z"},
             {"date": "2026-10-01 03:00", "title": "中國9月製造業PMI 50.1", "url": "d", "source": "z"}]
    got = M.news_pmi(NOW, lookback_days=60, fetch=lambda q, lang, since, limit: items if "統計局" in q else [])
    assert got["cn"]["value"] == 49.3 and got["cn"]["month"] == "2026-10-01" and got["cn"]["n"] == 2
    assert "tw" not in got


def test_transform():
    s = pd.Series(np.arange(100, 124, dtype=float), index=pd.date_range("2024-01-01", periods=24, freq="MS"))
    y = M.transform(s, "yoy")
    assert len(y) == 12 and abs(y.iloc[0] - 12.0) < 1e-9
    w = pd.Series([200e3, 210e3, 220e3, 230e3], index=pd.date_range("2026-10-03", periods=4, freq="W-SAT"))
    assert M.transform(w, "ma4").iloc[-1] == 215.0


def test_states_rules():
    idx = pd.date_range("2025-01-01", periods=10, freq="MS")
    cli = pd.Series([100.5, 100.8, 101.0, 100.9, 101.1, 100.9, 100.7, 100.4, 99.8, 99.5], index=idx)
    st = M.states("cli", cli)
    assert st.iloc[3] == "擴張"                       # 只降一個月不算轉向
    assert st.iloc[6] == "趨緩" and st.iloc[-1] == "衰退"
    d = pd.date_range("2026-01-01", periods=40, freq="B")
    curve = pd.Series([0.5] * 20 + [-0.3] * 20, index=d)
    sc = M.states("curve", curve)
    assert sc.iloc[19] == "正常" and sc.iloc[-1] == "倒掛"
    sahm = pd.Series([0.1, 0.4, 0.55, 0.45, 0.35, 0.2], index=idx[:6])
    assert list(M.states("sahm", sahm)) == ["正常", "正常", "觸發", "觸發", "觸發", "正常"]   # 遲滯：0.3 以下才解除
    pmi = pd.Series([51.0, 50.2, 49.6, 50.1], index=idx[:4])
    assert list(M.states("level50", pmi)) == ["擴張", "擴張", "收縮", "擴張"]
    wk = pd.date_range("2025-01-04", periods=60, freq="W-SAT")
    claims = pd.Series([200.0] * 50 + [210, 220, 230, 240, 245, 250, 250, 250, 250, 250], index=wk)
    sc = M.states("claims", claims)
    assert sc.iloc[49] == "正常" and sc.iloc[-1] == "上升警訊"


def test_evaluate_baseline_then_turn_then_no_repeat():
    st = {}
    idx = pd.date_range("2026-07-01", periods=3, freq="MS")
    raw = {"tw_pmi": pd.Series([51.0, 50.6, 50.2], index=idx)}
    ev, rows = M.evaluate(raw, {}, {"tw_pmi"}, st, NOW)
    assert ev == []                                                         # 第一次：只建基準
    raw["tw_pmi"] = pd.Series([51.0, 50.6, 50.2, 48.9], index=pd.date_range("2026-07-01", periods=4, freq="MS"))
    ev, rows = M.evaluate(raw, {}, {"tw_pmi"}, st, NOW)
    turn = [e for e in ev if e["kind"] == "macro"]
    rel = [e for e in ev if e["kind"] == "macro_rel"]
    assert len(turn) == 1 and turn[0]["level"] == "重大" and "跌破 50" in turn[0]["title"] and "2026/10" in turn[0]["title"]
    assert len(rel) == 1 and rel[0]["level"] == "注意" and "前值 50.2" in rel[0]["title"]
    ev, _ = M.evaluate(raw, {}, {"tw_pmi"}, st, NOW)                        # 同一期再跑：不重發
    assert ev == []
    row = next(r for r in rows if r["key"] == "tw_pmi")
    assert row["state"] == "收縮" and row["turns"][-1] == {"date": "2026-10-01", "to": "收縮"}
    # 有版權的序列：網站不放數值
    raw2 = {"us_credit": pd.Series([1.5, 1.6], index=pd.to_datetime(["2026-11-05", "2026-11-06"]))}
    _, rows2 = M.evaluate(raw2, {}, set(), {}, NOW)
    r = next(x for x in rows2 if x["key"] == "us_credit")
    assert r["value"] is None and "spark" not in r


def test_light_change():
    st = {}
    idx = pd.date_range("2026-07-01", periods=3, freq="MS")
    raw = {"tw_light": pd.Series([38.0, 39.0, 41.0], index=idx)}
    extra = {"tw_light": pd.Series(["黃紅", "黃紅", "紅"], index=idx)}
    M.evaluate(raw, extra, set(), st, NOW)
    idx4 = pd.date_range("2026-07-01", periods=4, freq="MS")
    raw = {"tw_light": pd.Series([38.0, 39.0, 41.0, 36.0], index=idx4)}
    extra = {"tw_light": pd.Series(["黃紅", "黃紅", "紅", "黃紅"], index=idx4)}
    ev, _ = M.evaluate(raw, extra, {"tw_light"}, st, NOW)
    t = [e for e in ev if e["kind"] == "macro"]
    assert len(t) == 1 and "由紅燈轉為黃紅燈" in t[0]["title"] and "降溫" in t[0]["title"]


class FakeHTTP:
    def __init__(self):
        self.calls = []

    def get(self, url, **kw):
        self.calls.append(url)
        z = _zip()

        class R:
            status_code = 200
            content = z
            text = ""

            def raise_for_status(self):
                pass

            def json(self):
                return {"result": {"distribution": [{"resourceDownloadUrl": "https://x/ndc.zip"}]}}
        return R()


def test_check_writes_site_and_respects_refresh():
    store = Path(tempfile.mkdtemp())
    st, http = {}, FakeHTTP()
    ev, site = M.check(store, st, NOW, http=http, sources=("ndc",))
    assert ev == [] and (store / "macro.json").exists() and (store / "macro_series.csv.gz").exists()
    j = json.loads((store / "macro.json").read_text())
    row = next(r for r in j["rows"] if r["key"] == "tw_light")
    assert row["state"] == "紅燈" and row["value"] == 41.0
    n = len(http.calls)
    M.check(store, st, NOW + pd.Timedelta(hours=1), http=http, sources=("ndc",))   # 12 小時內、不在公布時段：不重抓
    assert len(http.calls) == n
    M.check(store, st, pd.Timestamp("2026-11-27 16:10", tz="Asia/Taipei"), http=http, sources=("ndc",))   # 月底 16 點：每次都抓
    assert len(http.calls) > n


def test_mac_fast_windows():
    sys.path.insert(0, str(ROOT / "scripts"))
    import macro_fast as F
    T = lambda s: pd.Timestamp(s, tz="Asia/Taipei")  # noqa: E731
    assert F.window(T("2026-11-06 21:30")) == ("news", "fred")       # 週五晚上：美國數據
    assert F.window(T("2026-11-02 09:10")) == ("news",)              # 週一早上：台灣／中國 PMI
    assert F.window(T("2026-11-27 16:05")) == ("news", "ndc")        # 月底下午：國發會燈號
    assert F.window(T("2026-11-06 12:00")) is None
    assert F.window(T("2026-11-07 21:30")) is None                   # 週六不檢查


if __name__ == "__main__":
    ok = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            ok += 1
            print("✔", name)
    print(f"全部通過：{ok} 項")
