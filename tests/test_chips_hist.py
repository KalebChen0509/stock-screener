"""籌碼歷史回補：每月彙總、由新往舊、連不上就停、櫃買 2018 年以前的欄位格式。

執行：python -m tests.test_chips_hist（不需網路、不需 pytest）
"""
import os
import sys
import tempfile
from pathlib import Path

os.environ["SCREENER_CACHE"] = tempfile.mkdtemp()

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from screener import chips, chips_hist as H  # noqa: E402


def fake_day(day):
    if day.day == 15:
        return chips.NO_DATA                                       # 假日
    f = 1000.0 if day.day % 2 else -400.0
    return pd.DataFrame({"date": [day, day], "id": ["2330", "6488"], "foreign": [f, 10.0], "trust": [50.0, np.nan],
                         "dealer": [-5.0, 0.0], "total": [f + 45, 10.0], "margin": [100 + day.day, np.nan], "short": [3.0, 1.0]})


def test_summarize_month():
    m = pd.Timestamp("2026-09-01")
    days = [x for x in (fake_day(d) for d in pd.bdate_range(m, "2026-09-30")) if isinstance(x, pd.DataFrame)]
    s = H.summarize(days, m).set_index("id")
    tsmc = s.loc["2330"]
    odd = sum(1 for d in pd.bdate_range(m, "2026-09-30") if d.day != 15 and d.day % 2)
    even = len(days) - odd
    assert tsmc["foreign"] == 1000 * odd - 400 * even and tsmc["foreign_days"] == odd and tsmc["n_days"] == len(days)
    assert tsmc["margin"] == 100 + max(d.day for d in pd.bdate_range(m, "2026-09-30") if d.day != 15)   # 月底餘額
    assert s.loc["2330", "month"] == "2026-09-30" and pd.isna(s.loc["6488", "trust"])


def test_update_newest_first_and_stop_on_failure():
    calls = []

    def fetch(day):
        calls.append(day)
        return None if day.strftime("%Y-%m") == "2026-08" else fake_day(day)   # 8 月連不上
    r = H.update(max_minutes=60, sleep=0, today="2026-11-07", fetch=fetch, start="2026-07")
    assert r["done"] == ["2026-10", "2026-09"] and r["left"] == 2               # 由新往舊；8 月失敗就停，7 月沒動
    assert H._state()["done"] == ["2026-10", "2026-09"]
    r2 = H.update(max_minutes=60, sleep=0, today="2026-11-07", fetch=fake_day, start="2026-07")
    assert r2["done"] == ["2026-08", "2026-07"] and r2["left"] == 0
    m = H.load()
    assert sorted(m["month"].dt.strftime("%Y-%m").unique()) == ["2026-07", "2026-08", "2026-09", "2026-10"]
    assert len(m) == len(m.drop_duplicates(["month", "id"]))


def test_tpex_pre2018_format():
    """櫃買 2018 年以前：欄位叫「外資及陸資淨買股數」「投信淨買股數」「自營淨買股數」。"""
    js = {"stat": "ok", "tables": [{"fields": [], "data": []}, {"fields": [
        "代號", "名稱", "外資及陸資買股數", "外資及陸資賣股數", "外資及陸資淨買股數", "投信買進股數", "投信賣股數", "投信淨買股數",
        "自營淨買股數", "自營商(自行買賣)買股數", "自營商(自行買賣)賣股數", "自營商(自行買賣)淨買股數",
        "自營商(避險)買股數", "自營商(避險)賣股數", "自營商(避險)淨買股數", "三大法人買賣超股數"],
        "data": [["6488", "環球晶", "50,000", "10,000", "40,000", "0", "0", "0", "-6,000", "0", "0", "0", "0", "6,000", "-6,000", "34,000"]]}]}
    r = chips.parse_insti(js).set_index("id").loc["6488"]
    assert (r.foreign, r.trust, r.dealer, r.total) == (40000, 0, -6000, 34000)


if __name__ == "__main__":
    ok = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            ok += 1
            print("✔", name)
    print(f"全部通過：{ok} 項")
