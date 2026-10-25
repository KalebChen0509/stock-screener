"""景氣階段模組檢查（不需網路）。執行：python -m tests.test_cycle"""
from pathlib import Path

import numpy as np
import pandas as pd

from screener import cycle as C
from screener.common import apply_patch, load_strategy

FIX = Path(__file__).parent / "fixtures"


def load_fixture(name, k):
    rows = [x.split(":") for x in (FIX / name).read_text().split()]
    idx = pd.to_datetime([r[0] for r in rows], format="%Y%m") + pd.offsets.MonthEnd(0)
    return [pd.Series([float(r[i]) for r in rows], index=idx) for i in range(1, k)]


def main():
    # ---------- 國發會 CSV 格式 ----------
    txt = ('"Date","領先指標綜合指數","領先指標不含趨勢指數","同時指標綜合指數","同時指標不含趨勢指數",'
           '"落後指標綜合指數","落後指標不含趨勢指數","景氣對策信號綜合分數","景氣對策信號"\n'
           "198201,12.28,100.34,13.11,107.10,13.08,106.92,-,-\n"
           "202607,137.96,103.8779136,140.90,106.08,138.63,104.38,41,紅\n"
           "202608,140.58,104.4086599,143.36,106.47,142.33,105.70,41,紅\n")
    d = C.parse_ndc_csv(txt)
    assert list(d.index.strftime("%Y-%m")) == ["1982-01", "2026-07", "2026-08"]
    assert abs(d["lead"].iloc[-1] - 104.4086599) < 1e-9 and d["score"].iloc[-1] == 41 and d["light"].iloc[-1] == "紅"
    import io
    import zipfile
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("manifest.csv", "name\n")
        z.writestr("景氣指標與燈號.csv", txt.encode("utf-8"))
    assert len(C.parse_ndc_zip(buf.getvalue())) == 3
    print("ndc parse ok")

    # ---------- 階段判斷 ----------
    assert C.raw_phase(101, 0.2) == "expansion" and C.raw_phase(101, -0.1) == "slowdown"
    assert C.raw_phase(99, 0.1) == "recovery" and C.raw_phase(99, -0.1) == "recession"
    tw, = load_fixture("tw_lead_2014.txt", 2)[:1]
    us, curve = load_fixture("us_cli_2014.txt", 3)
    cfg = load_strategy()
    tb = C.tables({"tw": {"lead": tw}, "us": {"lead": us, "curve": curve, "spread": pd.Series(dtype=float)}}, cfg)
    p = lambda m, t: (C.phase_at(tb[m], pd.Timestamp(t)) or {}).get("phase")  # noqa: E731
    assert p("tw", "2026-09-30") == "expansion"          # 2026/8 領先指標 104.4 且往上
    assert p("tw", "2020-06-30") == "recession"          # 疫情：低於 100 往下
    assert p("tw", "2023-01-31") == "recession"
    assert p("tw", "2014-03-31") is None or p("tw", "2014-03-31") in C.PHASES
    # 延後 1 個月：2026-08 的數值在 2026-09-30 才用
    assert C.phase_at(tb["tw"], pd.Timestamp("2026-09-30"))["data_month"] == "2026-08"
    assert C.phase_at(tb["tw"], pd.Timestamp("2026-09-29"))["data_month"] == "2026-07"
    # 連續 2 個月才切換：單月的訊號不會改變階段
    lead = pd.Series([101, 101.2, 101.4, 101.6, 101.8, 101.5, 101.9, 102.3, 102.6],
                     index=pd.date_range("2020-01-31", periods=9, freq="ME"))
    t1 = C.phase_table(lead, {"lag_months": 0, "confirm_months": 2})
    assert list(t1["raw"].iloc[3:]) == ["expansion", "expansion", "expansion", "expansion", "expansion", "expansion"]
    lead2 = pd.Series([101, 101.2, 101.4, 101.6, 101.0, 101.7, 101.0, 100.8, 100.6],
                      index=pd.date_range("2020-01-31", periods=9, freq="ME"))
    t2 = C.phase_table(lead2, {"lag_months": 0, "confirm_months": 2})
    assert t2["raw"].iloc[4] == "slowdown" and t2["phase"].iloc[4] == "expansion"     # 一個月：不切換
    assert t2["phase"].iloc[-1] == "slowdown"                                         # 連續兩個月：切換
    # 美股：殖利率倒掛把擴張改判趨緩；信用利差急升往後推一個階段
    lead3 = pd.Series(np.linspace(100.5, 101.5, 12), index=pd.date_range("2020-01-31", periods=12, freq="ME"))
    cv = pd.Series(-0.3, index=lead3.index)
    t3 = C.phase_table(lead3, {"lag_months": 0, "confirm_months": 1, "us_overlays": {"curve_inversion": True}}, cv)
    assert (t3["phase"].dropna() == "slowdown").all() and "倒掛" in t3["signal"].iloc[-1]
    sp = pd.Series([2.0] * 8 + [2.8] * 4, index=lead3.index)
    t4 = C.phase_table(lead3, {"lag_months": 0, "confirm_months": 1, "us_overlays": {"credit_jump_pp": 0.5}}, None, sp)
    assert t4["phase"].iloc[-2] == "slowdown" and t4["phase"].iloc[4] == "expansion"
    print("phase rules ok")

    # ---------- 依階段改寫設定 ----------
    on = apply_patch(cfg, {"cycle": {"enabled": True}})
    assert C.apply_phase(cfg, "recession") is cfg                                     # 未啟用：不改
    r = C.apply_phase(on, "recovery")["industry_rules"]
    assert r["enabled"] and r["financials"]
    assert not C.apply_phase(on, "expansion")["industry_rules"]["enabled"]
    s = C.apply_phase(on, "slowdown")["industry_rules"]
    assert s["enabled"] and not s["financials"]
    print("apply_phase ok")

    # ---------- 回測：階段切換跑得動，且每月記錄階段 ----------
    import tests.test_industry as TI  # noqa
    from screener.backtest import run_backtest
    from screener.common import load_etf_universe
    from screener.etf import static_frame
    from screener.metrics import fundamental_panel
    from tests.test_synthetic import synth_fund
    rng = np.random.default_rng(3)
    quarters = pd.date_range("2012-03-31", "2026-06-30", freq="QE")
    inds = ["半導體業"] * 16 + ["建材營造"] * 16 + ["金融保險"] * 16 + ["食品工業"] * 16
    ids = [f"{3000 + i}" for i in range(len(inds))]
    us_ids = [f"V{i}" for i in range(32)]
    fund = pd.concat([synth_fund("tw", ids, quarters), synth_fund("us", us_ids, quarters)])
    infos = {"tw": pd.DataFrame({"name": ids, "industry": inds, "is_financial": [x == "金融保險" for x in inds],
                                 "yahoo": [i + ".TW" for i in ids]}, index=ids),
             "us": pd.DataFrame({"name": us_ids, "industry": ["Retail"] * 16 + ["Banks"] * 16,
                                 "is_financial": [False] * 16 + [True] * 16, "yahoo": us_ids,
                                 "sic": ["5311"] * 16 + ["6022"] * 16}, index=us_ids)}
    ymap = {**dict(zip(ids, infos["tw"]["yahoo"])), **dict(zip(us_ids, us_ids))}
    panel = fundamental_panel(fund, None, ymap)
    panels = {"tw": panel[panel.market == "tw"], "us": panel[panel.market == "us"]}
    months = pd.date_range("2011-01-31", "2026-09-30", freq="ME")
    etfs = load_etf_universe()
    rows = []
    for t in list(ymap.values()) + ["^TWII", "^GSPC"] + [e["ticker"] for e in etfs] + ["TWD=X"]:
        px = 30 + np.cumsum(rng.normal(0, 0.3, len(months))) if t == "TWD=X" else \
            50 * np.exp(np.cumsum(rng.normal(0.006, 0.05, len(months))))
        rows.append(pd.DataFrame({"ticker": t, "month": months, "close": px, "adj_close": px, "volume": 5e6, "split": 0.0}))
    prices = pd.concat(rows)
    macro = {"cycle_inputs": {"tw": {"lead": tw}, "us": {"lead": us, "curve": curve, "spread": pd.Series(dtype=float)}}}
    out = run_backtest(on, panels, infos, prices, static_frame(etfs, 30.0), start="2018-01-01", macro=macro)
    m = out["monthly"]
    assert m["phase_tw"].notna().all() and set(m["phase_tw"]) <= set(C.PHASES)
    print("backtest ok: TW phases", m["phase_tw"].value_counts().to_dict(), "US phases", m["phase_us"].value_counts().to_dict())


if __name__ == "__main__":
    main()
