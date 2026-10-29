"""成長／動能選股、持股比重、下市公司處理的檢查（不需網路）。執行：python -m tests.test_growth"""
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd

from screener import data_tw, loaders
from screener.backtest import run_backtest, stock_styles, weight_picks
from screener.common import apply_patch, load_etf_universe, load_strategy
from screener.engine import screen_growth
from screener.etf import static_frame
from screener.metrics import fundamental_panel
from tests.test_synthetic import synth_fund


class FakeFM:
    """假的 FinMind：依 dataset 回傳事先準備的資料；fail 裡的 (dataset, id) 模擬連線失敗。"""

    def __init__(self, data, fail=()):
        self.data, self.calls, self.max_calls, self.fail, self.last_ok = data, 0, 10 ** 6, set(fail), True

    def get(self, dataset, data_id=None, start="2010-01-01", end=None):
        self.calls += 1
        if (dataset, data_id) in self.fail:
            self.last_ok = False
            return pd.DataFrame()
        self.last_ok = True
        v = self.data.get((dataset, data_id), self.data.get(dataset, pd.DataFrame()))
        return v.copy()


def main():
    # ---------- 持股比重 ----------
    ids = ["A", "B", "C", "D"]
    eq = weight_picks(ids, {}, 1.0)
    assert all(abs(v - 0.25) < 1e-12 for v in eq.values())
    mc = {"A": 100, "B": 10, "C": 10, "D": 10}
    cap = weight_picks(ids, mc, 1.0, "cap")
    assert abs(cap["A"] - 100 / 130) < 1e-9
    cc = weight_picks(ids, mc, 1.0, "cap_capped", 0.40)
    assert abs(cc["A"] - 0.40) < 1e-9 and all(abs(cc[k] - 0.20) < 1e-9 for k in "BCD"), cc
    assert abs(sum(cc.values()) - 1.0) < 1e-9
    assert weight_picks(ids, mc, 1.0, "cap_capped", 0.10) == eq                # 上限 × 檔數 < 1 → 等權重
    assert weight_picks([], mc, 1.0) == {} and weight_picks(ids, mc, 0.0) == {}
    cfg = load_strategy()
    assert stock_styles(cfg) == [("value", 1.0)]
    assert stock_styles(apply_patch(cfg, {"portfolio": {"stock_style": "blend"}})) == [("value", 0.5), ("growth", 0.5)]
    print("weighting ok")

    # ---------- 新指標 ----------
    rng = np.random.default_rng(5)
    quarters = pd.date_range("2012-03-31", "2026-06-30", freq="QE")
    inds = ["半導體業"] * 20 + ["電子零組件業"] * 20 + ["金融保險"] * 10 + ["其他"] * 10
    tw_ids = [f"{3000 + i}" for i in range(len(inds))]
    fund = synth_fund("tw", tw_ids, quarters)
    # 一半公司營收每季成長 6%（年增約 26%）
    grow = set(tw_ids[::2])
    k = fund.groupby("id").cumcount()
    g = np.where(fund["id"].isin(grow), 1.06 ** k, 1.0)
    for c in ("revenue", "gross_profit", "op_income", "pretax", "net_income", "cfo", "nonop"):
        fund[c] = fund[c] * g
    info = pd.DataFrame({"name": tw_ids, "industry": inds, "is_financial": [x == "金融保險" for x in inds],
                         "yahoo": [s + ".TW" for s in tw_ids]}, index=tw_ids)
    info.index.name = "id"
    ymap = dict(zip(tw_ids, info["yahoo"]))
    panel = fundamental_panel(fund, None, ymap)
    last = panel.sort_values("period_end").groupby("id").tail(1).set_index("id")
    rg = last["rev_growth_1y"]
    assert rg[list(grow)].median() > 0.15 and abs(rg.drop(list(grow)).median()) < 0.10, rg.describe()
    assert last["debt_to_equity"].notna().all() and last["eps_growth_1y"].notna().any()
    print("growth metrics ok")

    # ---------- 成長選股 ----------
    months = pd.date_range("2011-01-31", "2026-09-30", freq="ME")
    close = pd.DataFrame({t: 50 * np.exp(np.cumsum(rng.normal(0.008 if t[:4] in grow else 0.002, 0.06, len(months))))
                          for t in list(ymap.values()) + ["^TWII", "^GSPC"]}, index=months)
    date = pd.Timestamp("2026-08-31")
    px = pd.DataFrame({"close": [close.at[date, ymap[i]] * 5 for i in tw_ids], "avg_traded_value": 1e9}, index=tw_ids)
    px.index.name = "id"
    r6 = close.iloc[-2] / close.iloc[-8] - 1
    r12 = close.iloc[-2] / close.iloc[-13] - 1
    res = screen_growth(panel, info, px, pd.Series(ymap).map(r6), date, cfg, "tw", ret12=pd.Series(ymap).map(r12))
    a = res["all"]
    assert not a.loc[a["template"] == "financial", "pass_all"].any(), "financials excluded"
    assert (a.loc[a["pass_all"], "rev_growth_1y"] >= 0.10).all(), "growth filter: revenue growth >= 10%"
    assert a.loc[a["pass_all"], "id"].isin(grow).mean() > 0.7
    assert "其他業" in set(a["industry_norm"]), "「其他」normalized to 其他業"
    rk = res["ranked"]
    assert len(rk) and rk["score"].is_monotonic_decreasing and "market_cap" in rk
    print("growth screen ok:", int(a["pass_all"].sum()), "passed; top", list(rk["id"].head(3)))

    # ---------- 回測：成長、各半、市值、核心＋衛星、下市 ----------
    etfs = load_etf_universe()
    # 3001：2020/6 之後停止交易，2020/9 下市（經營出問題 → 打折）；3003：交易到 2021/3 中旬下市（併購 → 不打折）
    dl = {"3001": ("2020-09-15", "2020-06-20"), "3003": ("2021-03-15", "2021-03-12")}
    rows = []
    for t_ in list(ymap.values()) + ["^TWII", "^GSPC"] + [e["ticker"] for e in etfs] + ["TWD=X"]:
        p_ = close[t_].values if t_ in close else (30 + np.cumsum(rng.normal(0, 0.3, len(months))) if t_ == "TWD=X"
                                                   else 50 * np.exp(np.cumsum(rng.normal(0.006, 0.05, len(months)))))
        df_ = pd.DataFrame({"ticker": t_, "month": months, "close": p_, "adj_close": p_, "volume": 5e6, "split": 0.0})
        sid = t_.split(".")[0]
        if sid in dl:
            df_ = df_[df_["month"] <= pd.Timestamp(dl[sid][1]) + pd.offsets.MonthEnd(0)]
        rows.append(df_)
    prices = pd.concat(rows)
    info_dl = info.copy()
    info_dl["delist_date"] = pd.to_datetime([dl.get(i, (None, None))[0] for i in tw_ids])
    info_dl["last_trade"] = pd.to_datetime([dl.get(i, (None, None))[1] for i in tw_ids])
    panels = {"tw": panel}
    infos = {"tw": info_dl}
    # 所有條件關掉、持股上限放大 → 每檔都會買到，用來確認下市的處理
    base = apply_patch(cfg, {"growth": {"filters": [], "max_holdings": 200},
                             "portfolio": {"stock_style": "growth", "weights": {"tw_stock": 0.5, "us_stock": 0.0, "etf": 0.0}},
                             "risk": {"stop_loss": {"enabled": False}}})
    cache = {}
    o = run_backtest(base, panels, infos, prices, static_frame(etfs, 30.0), start="2019-06-01", screen_cache=cache)
    ex = {x["id"]: x for x in o["delist_exits"]}
    assert set(ex) == {"3001", "3003"}, ex
    assert ex["3001"]["distressed"] and abs(ex["3001"]["ret_month"] + 0.5) < 1e-9, ex["3001"]
    assert not ex["3003"]["distressed"]
    sp = [x for x in o["spells"] if x["id"] in dl]
    assert all(x["reason"].startswith("下市") for x in sp) and len(sp) == 2, sp
    held_after = [h for h in o["holdings"] if h["month"] > pd.Timestamp("2021-04-30")]
    assert not any(s_ in h["tw"] for h in held_after for s_ in dl), "delisted never bought again"
    m = o["monthly"]
    assert m["ret_net"].notna().all() and (m["stock_share_tw"] > 0.99).all()
    print("delisting ok:", {k: round(v["ret_month"], 3) for k, v in ex.items()})

    cs = apply_patch(base, {"portfolio": {"core_satellite": {"tw": 0.5}}})
    m2 = run_backtest(cs, panels, infos, prices, static_frame(etfs, 30.0), start="2019-06-01", screen_cache=cache)["monthly"]
    assert (m2["stock_share_tw"].between(0.49, 0.51)).all(), m2["stock_share_tw"].describe()
    capw = apply_patch(base, {"portfolio": {"weighting": "cap_capped", "max_weight": 0.05}})
    o3 = run_backtest(capw, panels, infos, prices, static_frame(etfs, 30.0), start="2019-06-01", screen_cache=cache)
    assert abs(o3["monthly"]["ret_gross"].sum() - m["ret_gross"].sum()) > 1e-6, "cap weighting changes returns"
    real = apply_patch(cfg, {"portfolio": {"stock_style": "blend", "weights": {"tw_stock": 0.5, "us_stock": 0.0, "etf": 0.0}},
                             "risk": {"stop_loss": {"basis": "vol", "proceeds": "redistribute"}}})
    o4 = run_backtest(real, panels, infos, prices, static_frame(etfs, 30.0), start="2019-06-01", screen_cache=cache)
    m4 = o4["monthly"]
    assert m4["ret_net"].notna().all() and m4["stock_share_tw"].between(0, 1).all()
    print(f"blend backtest ok: avg stocks {m4['n_tw'].mean():.1f}, stock share {m4['stock_share_tw'].mean():.2f}")

    # ---------- 下市資料抓取（假的 FinMind）＋ 載入 ----------
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        old = (data_tw.DELISTED_PATH, data_tw.DELISTED_PX_PATH, data_tw.DELISTED_DAILY_PATH, loaders.CACHE_DIR)
        data_tw.DELISTED_PATH, data_tw.DELISTED_PX_PATH, data_tw.DELISTED_DAILY_PATH = \
            d / "tw_delisted.csv", d / "tw_delisted_prices.csv.gz", d / "tw_delisted_daily.csv.gz"
        try:
            days = pd.bdate_range("2019-01-02", "2020-06-20")
            fm = FakeFM({
                "TaiwanStockDelisting": pd.DataFrame({"date": ["2020-09-15", "2015-01-01", "2021-01-01", "2022-03-01"],
                                                      "stock_id": ["3001", "1101", "0050", "2330"],
                                                      "stock_name": ["甲", "舊", "ETF", "沿用代號"]}),
                ("TaiwanStockPrice", "3001"): pd.DataFrame({"date": days.strftime("%Y-%m-%d"), "close": 10.0,
                                                            "Trading_Volume": 1000}),
                # 2330：清單上有一筆 2022 年下市，但之後一直有成交 → 代號沿用／重新上市，不能當成下市
                ("TaiwanStockPrice", "2330"): pd.DataFrame({"date": ["2022-02-25", "2026-10-27"], "close": 500.0,
                                                            "Trading_Volume": 1000}),
            })
            info_raw = pd.DataFrame({"industry_category": ["半導體業", "半導體業"], "stock_id": ["3001", "2330"],
                                     "stock_name": ["甲", "台積電"], "type": ["tpex", "twse"],
                                     "date": ["2020-09-01", "2026-10-27"]})
            # 連線失敗：不記為完成，下次再抓；連續失敗 3 次才放棄
            fm.fail = {("TaiwanStockPrice", "3001")}
            o1 = data_tw.update_delisted(fm, info_raw)
            assert not o1["px_done"].iloc[0] and o1["px_fail"].iloc[0] == 1 and not data_tw.DELISTED_PX_PATH.exists()
            o2 = data_tw.update_delisted(fm, info_raw)
            assert data_tw.load_delisted()["px_fail"].iloc[0] == 2 and not o2["px_done"].iloc[0]
            fm.fail = set()
            out = data_tw.update_delisted(fm, info_raw)
            assert out["px_done"].iloc[0] and data_tw.DELISTED_PX_PATH.exists()
            assert list(out["stock_id"]) == ["3001"], out           # 2015 年前、ETF、代號沿用都排除
            assert list(data_tw.load_delisted()["stock_id"]) == ["3001"]
            assert set(data_tw.load_delisted(keep_reused=True)["stock_id"]) == {"3001", "2330"}
            assert "2330.TW" not in set(pd.read_csv(data_tw.DELISTED_PX_PATH)["ticker"])
            assert out["yahoo"].iloc[0] == "3001.TWO" and out["last_trade"].iloc[0] == "2020-06-19"
            n_calls = fm.calls
            data_tw.update_delisted(fm, info_raw)
            assert fm.calls == n_calls + 1, "prices fetched only once"
            mp = pd.read_csv(data_tw.DELISTED_PX_PATH)
            assert mp["ticker"].eq("3001.TWO").all() and len(mp) == 18
            # 載入：下市公司加進台股清單並標記下市日；改上市的公司換成另一個字尾
            pd.DataFrame({"stock_id": ["2330", "6446"], "stock_name": ["台積電", "藥華藥"],
                          "industry_category": ["半導體業", "生技醫療業"], "type": ["twse", "tpex"],
                          "is_financial": [False, False], "yahoo": ["2330.TW", "6446.TWO"]}).to_csv(d / "tw_universe.csv", index=False)
            loaders.CACHE_DIR = d
            inf = loaders.load_infos(include_delisted=True, tickers={"2330.TW", "6446.TW"})["tw"]
            assert set(inf.index) == {"2330", "6446", "3001"}
            assert inf.at["6446", "yahoo"] == "6446.TW" and inf.at["3001", "yahoo"] == "3001.TWO"
            assert pd.notna(inf.at["3001", "delist_date"]) and pd.isna(inf.at["2330", "delist_date"])
            assert not loaders.load_infos(tickers={"2330.TW"})["tw"].index.isin(["3001"]).any()
            # 股票清單：同一檔有多個產業別時取最新、明確的；「其他」（其他業）不再被當成非股票排除
            old_info = data_tw.INFO_PATH
            data_tw.INFO_PATH = d / "tw_stock_info.csv"
            try:
                fm2 = FakeFM({"TaiwanStockInfo": pd.DataFrame({
                    "industry_category": ["其他", "運動休閒", "其他", "ETF"], "stock_id": ["9921", "9921", "9910", "0050"],
                    "stock_name": ["巨大", "巨大", "豐泰", "元大台灣50"], "type": ["twse"] * 4,
                    "date": ["2023-06-30", "2026-10-27", "2026-10-27", "2026-10-27"]})})
                si = data_tw.fetch_stock_info(fm2)
                assert si.set_index("stock_id").at["9921", "industry_category"] == "運動休閒"
                u2 = data_tw.common_stock_universe(si)
                assert set(u2["stock_id"]) == {"9921", "9910"}, u2
            finally:
                data_tw.INFO_PATH = old_info
            # 財報、月營收：抓失敗不記為完成
            from screener import month_rev
            olds = (data_tw.TW_FIN_DIR, data_tw.META_PATH, month_rev.REV_PATH, month_rev.REV_META, month_rev.fetch_openapi_latest)
            data_tw.TW_FIN_DIR, data_tw.META_PATH = d / "tw_fin", d / "tw_fin_meta.json"
            month_rev.REV_PATH, month_rev.REV_META = d / "rev.csv.gz", d / "rev_meta.json"
            month_rev.fetch_openapi_latest = lambda: pd.DataFrame(columns=["id", "period", "revenue"])
            try:
                uni = pd.DataFrame({"stock_id": ["3001"]})
                fm3 = FakeFM({}, fail={("TaiwanStockBalanceSheet", "3001"), ("TaiwanStockMonthRevenue", "3001")})
                lag = cfg["reporting_lag"]["tw"]
                data_tw.update_fundamentals(uni, lag, fm3, frozen={"3001"})
                assert data_tw._load_meta()["3001"] == {"fail": 1}
                assert data_tw.backfill_progress(uni)["have"] == 0
                pr = month_rev.update(uni, fm3)
                assert pr["have"] == 0
                fm3.fail = set()
                data_tw.update_fundamentals(uni, lag, fm3, frozen={"3001"})
                assert data_tw._load_meta()["3001"].get("v") == data_tw.SCHEMA_VERSION
                n0 = fm3.calls
                data_tw.update_fundamentals(uni, lag, fm3, frozen={"3001"})
                assert fm3.calls == n0, "delisted: fetched once, not rechecked"
                assert month_rev.update(uni, fm3)["have"] == 1
                # 舊版檔案：抓失敗被記成「沒有資料」的，補試一次（之後就不再查）
                import json as _json
                data_tw.META_PATH.write_text(_json.dumps({"3001": {"last_period": "1900-01-01", "checked": "2026-10-28", "v": data_tw.SCHEMA_VERSION, "miss": 0}}))
                n0 = fm3.calls
                data_tw.update_fundamentals(uni, lag, fm3, frozen={"3001"})
                assert fm3.calls == n0 + 3 and data_tw._load_meta()["3001"].get("retried")
                data_tw.update_fundamentals(uni, lag, fm3, frozen={"3001"})
                assert fm3.calls == n0 + 3
                month_rev.REV_META.write_text(_json.dumps({"3001": {"backfilled": True, "n": 0}}))
                n0 = fm3.calls
                month_rev.update(uni, fm3)
                month_rev.update(uni, fm3)
                assert fm3.calls == n0 + 1
                legacy = data_tw.load_delisted().drop(columns=["px_fail"])
                legacy.loc[:, "last_trade"] = None
                legacy.to_csv(data_tw.DELISTED_PATH, index=False)
                ld = data_tw.load_delisted()
                assert not ld["px_done"].iloc[0] and ld["px_fail"].iloc[0] == data_tw.MAX_FAIL - 1
                # 資料來源整個連不上：連續 5 次失敗就停止這一輪
                fm4 = data_tw.FinMind(token="x")
                fm4.s.get = lambda *a, **k: (_ for _ in ()).throw(data_tw.requests.ConnectionError("down"))
                data_tw.time.sleep, _sl = (lambda *_: None), data_tw.time.sleep
                try:
                    for _ in range(4):
                        assert fm4.get("TaiwanStockPrice", "1").empty and not fm4.last_ok
                    try:
                        fm4.get("TaiwanStockPrice", "1")
                        raise AssertionError("should stop after 5 failures in a row")
                    except data_tw.FinMindLimit:
                        pass
                finally:
                    data_tw.time.sleep = _sl
            finally:
                data_tw.TW_FIN_DIR, data_tw.META_PATH, month_rev.REV_PATH, month_rev.REV_META, month_rev.fetch_openapi_latest = olds
        finally:
            data_tw.DELISTED_PATH, data_tw.DELISTED_PX_PATH, data_tw.DELISTED_DAILY_PATH, loaders.CACHE_DIR = old
    print("delisted fetch/load ok; failed requests retried")


if __name__ == "__main__":
    main()
