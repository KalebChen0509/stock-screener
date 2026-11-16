"""【2026/11/14】過去一年回測（使用者要求）：2025/10/31 第一次決策，持有 2025-11～2026-10 共 12 個月，並行模擬的 6 個版本，
外加「查證沒有預知未來」。

查證方法（截斷測試）：對每一個決策月底 T，把**所有輸入資料砍到只剩 T 當天以前看得到的**，從頭重跑到 T，
比對「T 這天決定要持有什麼」跟完整資料跑出來的是不是一模一樣。只要程式有任何地方偷看了 T 之後的資料，兩邊就會不一樣。
  股價（月線、日線）：只留 ≤ T        財報：原始季資料只留公布日（available_date）≤ T，再重新計算指標（分割紀錄不截斷，見程式註解）
  台股月營收：只留「T 的上一個月」以前（T 當月營收要下個月 10 號才公布）     重大事件：只留 ≤ T
截斷測試查不到的（另外在報告裡說明）：資料本身事後被修正（台股財報用的是現在的版本）、美股清單是現在的清單、
規則是 2026 年 10～11 月看過歷史資料才定的。

    SCREENER_CACHE=<data 分支>/cache .venv/bin/python research/last_year.py [--skip-check]
"""
import argparse
import os
import pickle
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _paths import OUT  # noqa: E402

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from screener import events as evmod  # noqa: E402
from screener import month_rev as mrev  # noqa: E402
from screener import paper, prices  # noqa: E402
from screener.backtest import run_backtest  # noqa: E402
from screener.common import CACHE_DIR, apply_patch, load_etf_universe, load_strategy  # noqa: E402
from screener.etf import static_frame  # noqa: E402
from screener.loaders import load_infos, load_panels  # noqa: E402
from screener.metrics import fundamental_panel  # noqa: E402

START, END = "2025-11-01", "2026-10-31"
D = OUT / "last_year"
D.mkdir(exist_ok=True)


def load_events():
    ev = {}
    try:
        from screener.data_us import load_universe
        ev["us"] = evmod.load_us_events(load_universe())
    except Exception as e:  # noqa
        print("美股事件載入失敗：", str(e)[:100])
    try:
        ev["tw"] = evmod.load_tw_events()
    except Exception as e:  # noqa
        print("台股事件載入失敗：", str(e)[:100])
    return ev


def key_w(h):
    return {(w["kind"], w["ticker"]): round(float(w["w"]), 6) for w in h.get("weights", [])}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-check", action="store_true")
    ap.add_argument("--check-only", action="store_true", help="完整回測用上次存的 full.pkl，只重跑截斷測試")
    ap.add_argument("--fresh-daily", action="store_true",
                    help="補抓上次回測持有過的股票的最新日線（快取裡有些只到 10 月下旬，會漏掉月底前幾天的每日停損）")
    a = ap.parse_args()
    cfg = load_strategy()
    vers = paper.versions(cfg)
    monthly = prices.load_monthly(include_delisted=True)
    infos = load_infos(include_delisted=True)
    panels, raws = load_panels(infos, monthly)
    splits = prices.split_factor_table(monthly)
    fx_now = monthly[monthly["ticker"] == "TWD=X"].sort_values("month")["close"].iloc[-1]
    es = static_frame(load_etf_universe(), fx_now)
    mrw = mrev.wide()
    ev = load_events()
    c = pd.read_csv(CACHE_DIR / "daily_bt.csv.gz", parse_dates=["date"])
    daily = c.pivot_table(index="date", columns="ticker", values="adj_close", aggfunc="last").sort_index()
    for f in ("paper_daily.csv.gz",):                       # 並行模擬的日線（比較新）
        if (CACHE_DIR / f).exists():
            p = pd.read_csv(CACHE_DIR / f, parse_dates=["date"]).pivot_table(index="date", columns="ticker", values="adj_close", aggfunc="last")
            daily = daily.combine_first(p)
    dl = prices.load_delisted_daily()
    if len(dl):
        daily = pd.concat([daily, dl[[x for x in dl.columns if x not in daily.columns]]], axis=1).sort_index()
    fp = D / "daily_fresh.csv.gz"
    if a.fresh_daily:
        old = pickle.load(open(D / "full.pkl", "rb"))["full"]
        tks = sorted({w["ticker"] for o in old.values() for h in o["holdings"] for w in h["weights"]} | {"0050.TW", "SPY", "TWD=X"})
        prices.load_daily_adj(tks, start="2025-06-01", path=fp, max_age_days=0)
    if fp.exists():                                         # 有補抓的就整檔用新的（同一個還原基準）
        fr = pd.read_csv(fp, parse_dates=["date"]).pivot_table(index="date", columns="ticker", values="adj_close", aggfunc="last").sort_index()
        keep = [c_ for c_ in daily.columns if c_ not in fr.columns]
        daily = pd.concat([daily[keep], fr], axis=1).sort_index()
        print(f"補抓日線 {fr.shape[1]} 檔，到 {fr.index[-1]:%Y-%m-%d}", flush=True)
    daily, _fixed = prices.repair_tw_jumps(daily)
    print(f"資料：月線到 {monthly['month'].max():%Y-%m}、日線到 {daily.index[-1]:%Y-%m-%d}；"
          f"台股財報最新公布日 {panels['tw']['available_date'].max():%Y-%m-%d}、美股 {panels['us']['available_date'].max():%Y-%m-%d}", flush=True)

    sc, full = {}, {}
    if a.check_only:
        full = pickle.load(open(D / "full.pkl", "rb"))["full"]
    for name, patch in ([] if a.check_only else vers):
        o = run_backtest(apply_patch(cfg, patch), panels, infos, monthly, es, start=START, end=END, events=ev, month_rev=mrw,
                         screen_cache=sc, daily=daily, decide_last=True)
        full[name] = o
        m = o["monthly"]
        held = {w["ticker"] for h in o["holdings"] for w in h["weights"] if w["kind"] == "stock"}
        print(f"{name}：{len(m)} 個月、淨報酬 {(1 + m['ret_net']).prod() - 1:+.1%}；個股 {len(held)} 檔，有日線 {len(held & set(daily.columns))} 檔", flush=True)
    names = {mk: infos[mk]["name"].to_dict() for mk in infos}
    if not a.check_only:
        pickle.dump({"full": {k: {x: v[x] for x in ("monthly", "holdings", "stops", "spells")} for k, v in full.items()}, "names": names},
                    open(D / "full.pkl", "wb"))
    if a.skip_check:
        return

    # ---------------- 截斷測試
    decisions = [pd.Timestamp(h["month"]) for h in full[vers[0][0]]["holdings"]]
    rows = []
    for T in decisions:
        mT = monthly[monthly["month"] <= T]
        # 財報：先把原始季資料砍到公布日 ≤ T，再重新計算所有指標（近四季、三年平均、財報驚喜…）
        # 分割紀錄不截斷：快取的股價已經是「今天的分割基準」，股數要換算到同一個基準才是當時真正的市值
        # （11/16 第一次測試把分割也砍掉 → POWL 2026-04 1 拆 3 之類的公司市值被算成 1/3，出現 44 個假差異）
        pT = {k: fundamental_panel(raws[k][raws[k]["available_date"] <= T], splits,
                                   dict(zip(infos[k].index, infos[k]["yahoo"]))) for k in raws}
        rT = mrw[mrw.index <= T - pd.offsets.MonthEnd(1)]
        eT = {k: v[v["date"] <= T] for k, v in ev.items()}
        dT = daily.loc[:T]
        scT = {}
        for name, patch in vers:
            o = run_backtest(apply_patch(cfg, patch), pT, infos, mT, es, start=START, end=str(T.date()), events=eT, month_rev=rT,
                             screen_cache=scT, daily=dT, decide_last=True)
            hT = o["holdings"][-1]
            assert pd.Timestamp(hT["month"]) == T, (hT["month"], T)
            hF = next(h for h in full[name]["holdings"] if pd.Timestamp(h["month"]) == T)
            wT, wF = key_w(hT), key_w(hF)
            same_tk = set(wT) == set(wF)
            dmax = max((abs(wT.get(k, 0) - wF.get(k, 0)) for k in set(wT) | set(wF)), default=0.0)
            # 已完成月份的報酬也要一樣（停損路徑）
            mT_, mF_ = o["monthly"]["ret_net"], full[name]["monthly"]["ret_net"].loc[:T]
            rdiff = float((mT_ - mF_.reindex(mT_.index)).abs().max()) if len(mT_) else 0.0
            rows.append({"決策日": T.strftime("%Y-%m-%d"), "版本": name, "持股相同": same_tk, "權重最大差": dmax, "已完成月份報酬最大差": rdiff,
                         "只在截斷版": sorted(k[1] for k in set(wT) - set(wF)), "只在完整版": sorted(k[1] for k in set(wF) - set(wT))})
        print(f"截斷測試 {T:%Y-%m-%d}：{sum(r['持股相同'] for r in rows[-len(vers):])}/{len(vers)} 個版本持股完全相同", flush=True)
        pd.DataFrame(rows).to_csv(D / "truncation_check.csv", index=False)
    R = pd.DataFrame(rows)
    bad = R[~R["持股相同"] | (R["權重最大差"] > 1e-4) | (R["已完成月份報酬最大差"] > 1e-6)]
    print(f"\n==== 截斷測試：{len(R)} 個（決策日 × 版本）裡，{len(R) - len(bad)} 個完全相同、{len(bad)} 個不同 ====")
    if len(bad):
        print(bad.to_string(index=False))


if __name__ == "__main__":
    main()
