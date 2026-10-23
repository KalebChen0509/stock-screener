"""市場 × 產業規則、向量化估值、長期指標的檢查（不需網路）。執行：python -m tests.test_industry"""
import time

import numpy as np
import pandas as pd

from screener import valuation as V
from screener.common import apply_patch, load_strategy
from screener.engine import screen_market
from screener.industries import assign, normalize_industry, template_of
from screener.metrics import fundamental_panel, history_metrics
from tests.test_synthetic import synth_fund


def old_rim(b0, roe0, r, retention, years=10):
    roe0 = float(np.clip(roe0, -0.10, 0.40))
    v, b = b0, b0
    for t in range(1, years + 1):
        roe_t = roe0 + (r - roe0) * t / years
        v += (roe_t - r) * b / (1 + r) ** t
        b *= 1 + roe_t * retention
    return v


def old_dcf(fcf0, g1, gT, r, years1=5, years2=5):
    v, cf = 0.0, fcf0
    n = years1 + years2
    for t in range(1, n + 1):
        g = g1 if t <= years1 else g1 + (gT - g1) * (t - years1) / years2
        cf *= 1 + g
        v += cf / (1 + r) ** t
    return v + cf * (1 + gT) / (r - gT) / (1 + r) ** n


def main():
    # ---------- 產業模板 ----------
    assert normalize_industry("電子工業", "tw") == "其他電子業"
    assert normalize_industry("金融業", "tw") == "金融保險"
    assert template_of("tw", "半導體業", "2330") == "cyclical"
    assert template_of("tw", "通信網路業", "2412") == "utility"
    assert template_of("tw", "生技醫療業", "4000", profitable=True) == "defensive"
    assert template_of("tw", "生技醫療業", "4001", profitable=False) == "biotech_research"
    assert template_of("tw", "創新板股票", "1") == "exclude"
    assert template_of("us", "Software & IT Services", "X", sic="7372") == "growth_watch"
    assert template_of("us", "Software & IT Services", "X", sic="7373") == "general"
    assert template_of("us", "Electronics & Semis", "X", sic="3674") == "cyclical"
    assert template_of("us", "Electronics & Semis", "X", sic="3663") == "general"
    assert template_of("us", "Holding & Investment", "X", sic="6770") == "exclude"
    assert template_of("us", "Holding & Investment", "X", sic="6798") == "utility"
    assert template_of("us", "Banks", "X", sic="6022") == "financial"
    d = pd.DataFrame({"id": [str(i) for i in range(40)],
                      "industry": ["半導體業"] * 20 + ["水泥工業"] * 5 + ["鋼鐵工業"] * 12 + ["食品工業"] * 3})
    a = assign(d, "tw", 15)
    assert (a.loc[a.industry == "半導體業", "peer_group"] == "半導體業").all()
    assert (a.loc[a.industry == "水泥工業", "peer_group"] == "模板：循環").all()      # 5 家 → 跟循環模板比
    assert (a.loc[a.industry == "食品工業", "peer_group"] == "全市場").all()          # 防禦模板也只有 3 家
    print("industries ok")

    # ---------- 向量化估值 = 原本單檔公式 ----------
    for b0, roe0, r, ret in [(100, 0.15, 0.09, 0.5), (50, 0.05, 0.11, 0.3), (10, 0.35, 0.08, 0.9)]:
        assert abs(V.rim_value(b0, roe0, r, ret) - old_rim(b0, roe0, r, ret)) < 1e-9
    for f0, g1, r in [(100, 0.05, 0.09), (30, -0.02, 0.12), (5, 0.12, 0.07)]:
        assert abs(V.dcf_value(f0, g1, 0.02, r) - old_dcf(f0, g1, 0.02, r)) < 1e-6
    tgt = old_dcf(100, 0.07, 0.02, 0.09)
    assert abs(V.implied_growth(tgt, 100, 0.02, 0.09) - 0.07) < 1e-4
    assert np.isnan(V.rim_value(-5, 0.1, 0.09, 0.5))
    print("valuation formulas ok")

    # ---------- 合成資料：篩選＋估值 ----------
    rng = np.random.default_rng(1)
    quarters = pd.date_range("2012-03-31", "2026-06-30", freq="QE")
    tw_inds = ["半導體業"] * 20 + ["建材營造"] * 16 + ["金融保險"] * 16 + ["食品工業"] * 16 + ["生技醫療業"] * 6 + ["電子工業"] * 16
    tw_ids = [f"{2000 + i}" for i in range(len(tw_inds))]
    us_inds = ["Software & IT Services"] * 20 + ["Banks"] * 16 + ["Electronics & Semis"] * 20 + ["Retail"] * 20
    us_sic = ["7372"] * 10 + ["7373"] * 10 + ["6022"] * 16 + ["3674"] * 20 + ["5311"] * 20
    us_ids = [f"U{i}" for i in range(len(us_inds))]
    fund = pd.concat([synth_fund("tw", tw_ids, quarters), synth_fund("us", us_ids, quarters)])
    fund.loc[fund["market"] == "us", "sbc"] = fund.loc[fund["market"] == "us", "revenue"] * 0.02
    infos = {
        "tw": pd.DataFrame({"name": tw_ids, "industry": tw_inds, "is_financial": [x == "金融保險" for x in tw_inds],
                            "yahoo": [s + ".TW" for s in tw_ids]}, index=tw_ids),
        "us": pd.DataFrame({"name": us_ids, "industry": us_inds, "is_financial": [x == "Banks" for x in us_inds],
                            "yahoo": us_ids, "sic": us_sic}, index=us_ids),
    }
    ymap_all = {**dict(zip(tw_ids, infos["tw"]["yahoo"])), **dict(zip(us_ids, us_ids))}
    panel = fundamental_panel(fund, None, ymap_all)
    assert "fcf_3y_avg_adj" in panel and panel.loc[panel.market == "us", "sbc"].notna().any()
    assert panel.loc[panel.market == "tw", "sbc"].isna().all()
    months = pd.date_range("2011-01-31", "2026-09-30", freq="ME")
    cols = {}
    for t in list(ymap_all.values()) + ["^TWII", "^GSPC"]:
        cols[t] = 50 * np.exp(np.cumsum(rng.normal(0.006, 0.06, len(months))))
    close = pd.DataFrame(cols, index=months)
    adj = close * np.exp(np.arange(len(months)) * 0.003)[:, None]
    date = pd.Timestamp("2026-08-31")
    h = history_metrics(panel[panel.market == "tw"], date, close, ymap_all, None)
    assert h["roe_avg5y"].notna().all() and h["pb_hist_median"].notna().all(), h.head()
    assert (h["roe_years"] == 10).all()

    cfg = load_strategy()
    cfg_new = apply_patch(cfg, {"industry_rules": {"enabled": True}})
    out = {}
    for mkt, ids in (("tw", tw_ids), ("us", us_ids)):
        ym = dict(zip(ids, infos[mkt]["yahoo"]))
        px = pd.DataFrame({"close": [close.at[date, ym[i]] for i in ids], "avg_traded_value": 1e9}, index=ids)
        px["close"] *= 5
        px.index.name = "id"
        ctx = {"adj": adj, "close": close, "ymap": ym, "index": "^TWII" if mkt == "tw" else "^GSPC", "rf": None,
               "implied": True}
        t0 = time.time()
        r_new = screen_market(panel, infos[mkt], px, pd.Series(dtype=float), date, cfg_new, mkt, ctx=ctx)
        r_old = screen_market(panel, infos[mkt], px, pd.Series(dtype=float), date, cfg, mkt,
                              ctx={**ctx, "valuation": True})
        dt = time.time() - t0
        a_ = r_new["all"]
        for c_ in ("template", "peer_group", "mos", "roe_norm", "pb_hist_pct", "peer_value_rel", "val_models", "fair_base"):
            assert c_ in a_, c_
        assert r_old["all"]["fair_base"].notna().any(), "old rules should still show valuation"
        fin = a_["template"] == "financial"
        assert not a_.loc[fin, "pass_all"].any(), "financials should be blocked when financials=false"
        assert not a_.loc[a_["template"] == "growth_watch", "pass_all"].any()
        gw = a_["template"] == "growth_watch"
        assert not gw.any() or a_.loc[gw, "fair_base"].notna().any(), "growth stocks should still be valued"
        assert a_.loc[a_["template"] == "financial", "val_dcf"].isna().all(), "no DCF for financials"
        cyc = a_["template"] == "cyclical"
        assert not cyc.any() or (a_.loc[cyc, "val_pbh"].notna().any() and a_.loc[cyc, "val_dcf"].isna().all())
        out[mkt] = (int(a_["pass_all"].sum()), int(r_old["all"]["pass_all"].sum()), round(dt, 2))
        print(mkt, "templates", a_["template"].value_counts().to_dict(), "passed new/old", out[mkt][:2],
              f"{dt:.2f}s")
    # 金融股開關
    cfg_fin = apply_patch(cfg, {"industry_rules": {"enabled": True, "financials": True}})
    ym = dict(zip(tw_ids, infos["tw"]["yahoo"]))
    px = pd.DataFrame({"close": [close.at[date, ym[i]] * 5 for i in tw_ids], "avg_traded_value": 1e9}, index=tw_ids)
    px.index.name = "id"
    r_fin = screen_market(panel, infos["tw"], px, pd.Series(dtype=float), date, cfg_fin, "tw",
                          ctx={"adj": adj, "close": close, "ymap": ym, "index": "^TWII"})
    af = r_fin["all"]
    assert af.loc[af["template"] == "financial", "f_fin_allowed"].all()
    print("industry rules screen ok", out)

    # ---------- 回測：產業規則版本跑得動、選股快取與舊規則分開 ----------
    from screener.backtest import run_backtest, screen_key
    from screener.common import load_etf_universe
    from screener.etf import static_frame
    etfs = load_etf_universe()
    rows = []
    for t in list(ymap_all.values()) + ["^TWII", "^GSPC"]:
        rows.append(pd.DataFrame({"ticker": t, "month": months, "close": close[t].values, "adj_close": adj[t].values,
                                  "volume": 5e6, "split": 0.0}))
    for t in [e["ticker"] for e in etfs] + ["TWD=X"]:
        p_ = 30 + np.cumsum(rng.normal(0, 0.3, len(months))) if t == "TWD=X" else 50 * np.exp(np.cumsum(rng.normal(0.006, 0.05, len(months))))
        rows.append(pd.DataFrame({"ticker": t, "month": months, "close": p_, "adj_close": p_, "volume": 5e6, "split": 0.0}))
    prices = pd.concat(rows)
    panels = {"tw": panel[panel.market == "tw"], "us": panel[panel.market == "us"]}
    assert screen_key(cfg) != screen_key(cfg_new)
    cache = {}
    t0 = time.time()
    o_new = run_backtest(cfg_new, panels, infos, prices, static_frame(etfs, 30.0), start="2023-01-01",
                         screen_cache=cache, macro={"dgs10": pd.Series([4.0, 4.5], index=pd.to_datetime(["2020-01-31", "2024-01-31"]))})
    o_old = run_backtest(cfg, panels, infos, prices, static_frame(etfs, 30.0), start="2023-01-01", screen_cache=cache)
    m_new, m_old = o_new["monthly"], o_old["monthly"]
    assert len(m_new) == len(m_old) > 0 and m_new["ret_net"].notna().all()
    print(f"backtest ok ({time.time() - t0:.1f}s): new avg tw {m_new['n_tw'].mean():.1f} us {m_new['n_us'].mean():.1f}; "
          f"old avg tw {m_old['n_tw'].mean():.1f} us {m_old['n_us'].mean():.1f}")


if __name__ == "__main__":
    main()
