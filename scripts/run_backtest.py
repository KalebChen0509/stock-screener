"""執行 10 年回測，輸出 site/data/backtest.json。"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from pathlib import Path  # noqa: E402

from screener import prices  # noqa: E402
from screener.backtest import perf_stats, run_backtest, screen_key  # noqa: E402
from screener.common import SITE_DATA, load_etf_universe, load_strategy, log, write_json  # noqa: E402
from screener.etf import static_frame  # noqa: E402
from screener.loaders import load_infos, load_panels  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default=None)
    ap.add_argument("--out", default=str(SITE_DATA / "backtest.json"))
    ap.add_argument("--no-variants", action="store_true")
    ap.add_argument("--no-stress", action="store_true")
    ap.add_argument("--no-daily", action="store_true", help="不抓日線，停損只在月底檢查")
    args = ap.parse_args()
    cfg = load_strategy()
    monthly = prices.load_monthly()
    infos = load_infos()
    panels, _ = load_panels(infos, monthly)
    fx_now = monthly[monthly["ticker"] == "TWD=X"].sort_values("month")["close"].iloc[-1]
    etf_static = static_frame(load_etf_universe(), fx_now)
    events = {}
    try:
        from screener.events import load_tw_events, load_us_events
        from screener.data_us import load_universe
        events = {"us": load_us_events(load_universe()), "tw": load_tw_events()}
    except Exception as e:  # noqa
        log.warning("events not loaded: %s", e)
    from screener.common import apply_patch
    import json as _json
    from screener.macro import load_macro
    try:
        macro = load_macro(prices.wide_adj(monthly) if hasattr(prices, "wide_adj") else
                           monthly.pivot_table(index="month", columns="ticker", values="adj_close", aggfunc="last"))
        log.info("macro: baa %d months, hyg/ief %d months", len(macro["baa"]), len(macro["hyg_ief"]))
    except Exception as e:  # noqa
        log.warning("macro not loaded: %s", e)
        macro = {}
    screen_cache, collected, done = {}, {}, {}
    daily_box = {"df": pd.DataFrame()}

    def run_cfg(c):
        """跑一組設定。第一次遇到某種選股設定時，先跑一次「不停損」收集排名名單，再補抓日線。"""
        key = _json.dumps(c, sort_keys=True, default=str)
        if key in done:
            return done[key]
        sk = screen_key(c)
        stop_on = bool(((c.get("risk") or {}).get("stop_loss") or {}).get("enabled"))
        if sk not in collected:
            col = set()
            c0 = apply_patch(c, {"risk": {"stop_loss": {"enabled": False}}})
            o0 = run_backtest(c0, panels, infos, monthly, etf_static, start=args.start, events=events,
                              screen_cache=screen_cache, collect=col, macro=macro)
            done[_json.dumps(c0, sort_keys=True, default=str)] = o0
            collected[sk] = col
            if not args.no_daily:
                allt = set().union(*collected.values()) | {"0050.TW", "SPY"}
                daily_box["df"] = prices.load_daily_adj(sorted(allt), start="2015-06-01")
                log.info("daily for stop-loss: %d tickers", daily_box["df"].shape[1])
            if not stop_on:
                return o0
        o = run_backtest(c, panels, infos, monthly, etf_static, start=args.start, events=events,
                         daily=daily_box["df"], screen_cache=screen_cache, macro=macro)
        done[key] = o
        return o

    out = run_cfg(cfg)
    m = out["monthly"]
    cap = cfg["portfolio"]["initial_capital"]
    rf = cfg["backtest"].get("risk_free", 0.0)

    series_cols = ["ret_gross", "ret_net"] + [c for c in m.columns if c.startswith("bench_")]
    labels = {"ret_gross": "策略（未扣費）", "ret_net": "策略（扣費後）",
              "sleeve_tw": "台股個股部位", "sleeve_us": "美股個股部位", "sleeve_etf": "ETF 部位",
              "sleeve_core": "核心部位（大盤／債）"}
    sleeve_cols = [c for c in ("sleeve_tw", "sleeve_us", "sleeve_etf", "sleeve_core") if c in m]
    for c in series_cols:
        if c.startswith("bench_"):
            labels[c] = c[6:]

    def stats_for(sub):
        return {labels[c]: {**perf_stats(sub[c], rf),
                            "final_amount": float(cap * (1 + sub[c]).prod())} for c in series_cols}

    last5 = m[m.index > m.index[-1] - pd.DateOffset(years=5)]

    def sleeves_for(sub):
        res = {}
        pairs = {"sleeve_tw": "bench_0050.TW", "sleeve_us": "bench_SPY", "sleeve_etf": "bench_VT", "sleeve_core": "bench_VT"}
        for c in sleeve_cols:
            if c not in sub or (sub[c].fillna(0) == 0).all():
                continue
            st = perf_stats(sub[c], rf)
            b = pairs.get(c)
            if b in sub:
                st["bench"] = b[6:]
                st["bench_cagr"] = perf_stats(sub[b], rf).get("cagr")
            res[labels[c]] = st
        return res

    def regimes_for(sub):
        """各種市況下的表現：以混合基準為大盤。"""
        bcol = "bench_混合基準(20/20/60)"
        if bcol not in sub:
            return {}
        b, sN, sG = sub[bcol], sub["ret_net"], sub["ret_gross"]
        up, dn = b > 0, b < 0
        out = {
            "up_capture": float(sN[up].mean() / b[up].mean()) if up.any() else None,
            "down_capture": float(sN[dn].mean() / b[dn].mean()) if dn.any() else None,
            "win_rate_vs_bench": float((sN > b).mean()),
            "up_months": int(up.sum()), "down_months": int(dn.sum()),
            "avg_excess_up": float((sN - b)[up].mean()) if up.any() else None,
            "avg_excess_down": float((sN - b)[dn].mean()) if dn.any() else None,
        }
        # 滾動 12 個月：大盤空頭年（12 個月報酬 < 0）vs 多頭年
        rb = (1 + b).rolling(12).apply(np.prod, raw=True) - 1
        rs = (1 + sN).rolling(12).apply(np.prod, raw=True) - 1
        ok = rb.notna()
        out["rolling12_beat_rate"] = float((rs[ok] >= rb[ok]).mean()) if ok.any() else None
        out["rolling12_beat_rate_bear"] = float((rs[ok & (rb < 0)] >= rb[ok & (rb < 0)]).mean()) if (ok & (rb < 0)).any() else None
        out["rolling12_beat_rate_bull"] = float((rs[ok & (rb >= 0)] >= rb[ok & (rb >= 0)]).mean()) if (ok & (rb >= 0)).any() else None
        crises = {"2018Q4 升息與貿易戰": ("2018-10", "2018-12"), "2020 新冠崩盤": ("2020-02", "2020-03"),
                  "2020-21 復甦": ("2020-04", "2021-12"), "2022 升息熊市": ("2022-01", "2022-10"),
                  "2023-24 AI 多頭": ("2023-01", "2024-12")}
        cr = {}
        for k, (a, z) in crises.items():
            w = sub.loc[(sub.index >= pd.Timestamp(a)) & (sub.index <= pd.Timestamp(z) + pd.offsets.MonthEnd(0))]
            if len(w):
                cr[k] = {"strategy_net": float((1 + w["ret_net"]).prod() - 1),
                         "strategy_gross": float((1 + w["ret_gross"]).prod() - 1),
                         "bench": float((1 + w[bcol]).prod() - 1)}
        out["periods"] = cr
        return out
    def crises_for(sub):
        """危機區間：區間報酬、區間最大回撤、跌段（到大盤谷底）與反彈段報酬、停損次數。"""
        bcol = "bench_混合基準(20/20/60)"
        out = {}
        for c in cfg["backtest"].get("crises", []) or []:
            a, z = pd.Timestamp(c["start"]) + pd.offsets.MonthEnd(0), pd.Timestamp(c["end"]) + pd.offsets.MonthEnd(0)
            w = sub.loc[(sub.index > a) & (sub.index <= z)]
            if w.empty or bcol not in w:
                continue
            bn = (1 + w[bcol]).cumprod()
            trough = bn.idxmin()
            row = {}
            for lab, col in (("strategy", "ret_net"), ("bench", bcol)):
                nv = (1 + w[col]).cumprod()
                full = pd.concat([pd.Series([1.0]), nv.reset_index(drop=True)])
                row[lab] = {"total": float(nv.iloc[-1] - 1),
                            "mdd": float((full / full.cummax() - 1).min()),
                            "fall": float(nv.loc[trough] - 1),
                            "rebound": float(nv.iloc[-1] / nv.loc[trough] - 1)}
            row["stops"] = int(w["n_stops"].sum()) if "n_stops" in w else 0
            row["trough"] = trough.strftime("%Y-%m")
            out[c["name"]] = row
        return out

    def outcomes_for(spells, flat=0.03):
        """每筆持股分五類：大賺（賺且贏大盤）、小賺（賺但輸大盤）、持平（±3%）、小跌（賠但比大盤少跌）、大跌（賠且輸大盤）。"""
        cats = ["大賺", "小賺", "持平", "小跌", "大跌"]

        def cat(r, b):
            if abs(r) <= flat:
                return "持平"
            if r > 0:
                return "大賺" if (b is None or not np.isfinite(b) or r > b) else "小賺"
            return "小跌" if (b is not None and np.isfinite(b) and r > b) else "大跌"
        out = {}
        groups = {"全部": spells, "台股": [x for x in spells if x["market"] == "tw"],
                  "美股": [x for x in spells if x["market"] == "us"],
                  "已賣出": [x for x in spells if x["reason"] != "持有中"]}
        for g, lst in groups.items():
            lst = [x for x in lst if x["ret"] is not None and np.isfinite(x["ret"])]
            n = len(lst)
            row = {"n": n, "cats": {}}
            for c in cats:
                sub = [x for x in lst if cat(x["ret"], x["bench"]) == c]
                row["cats"][c] = {"n": len(sub), "share": len(sub) / n if n else None,
                                  "avg_ret": float(np.mean([x["ret"] for x in sub])) if sub else None,
                                  "avg_excess": float(np.mean([x["ret"] - x["bench"] for x in sub if np.isfinite(x["bench"])])) if sub else None,
                                  "avg_months": float(np.mean([x["months"] for x in sub])) if sub else None}
            if n:
                row["win_rate"] = float(np.mean([x["ret"] > 0 for x in lst]))
                bb = [x for x in lst if np.isfinite(x["bench"])]
                row["beat_rate"] = float(np.mean([x["ret"] > x["bench"] for x in bb])) if bb else None
                row["avg_ret"] = float(np.mean([x["ret"] for x in lst]))
                row["median_ret"] = float(np.median([x["ret"] for x in lst]))
                row["avg_months"] = float(np.mean([x["months"] for x in lst]))
                row["stop_exits"] = int(sum(x["reason"].startswith("停損") for x in lst))
                row["best"] = max(lst, key=lambda x: x["ret"])
                row["worst"] = min(lst, key=lambda x: x["ret"])
            out[g] = row
        return out

    names = {mk: infos[mk]["name"].to_dict() for mk in infos}
    etf_names = {e["ticker"]: e["name"] for e in load_etf_universe()}
    hold = []
    for h in out["holdings"]:
        hold.append({
            "month": h["month"].strftime("%Y-%m"),
            "tw": [[s, names.get("tw", {}).get(s, "")] for s in h["tw"]],
            "us": [[s, names.get("us", {}).get(s, "")] for s in h["us"]],
            "etf": [[s, etf_names.get(s, "")] for s in h["etf"]],
        })
    res = {
        "generated": pd.Timestamp.now(tz="Asia/Taipei").strftime("%Y-%m-%d %H:%M"),
        "start": m.index[0].strftime("%Y-%m"), "end": m.index[-1].strftime("%Y-%m"),
        "initial_capital": cap,
        "weights": cfg["portfolio"]["weights"],
        "costs": cfg["costs"],
        "months": [d.strftime("%Y-%m") for d in m.index],
        "nav": {labels[c]: list((cap * (1 + m[c].fillna(0)).cumprod()).round(0)) for c in series_cols},
        "stats_full": stats_for(m),
        "stats_5y": stats_for(last5),
        "counts": {"tw": list(m["n_tw"]), "us": list(m["n_us"]), "etf": list(m["n_etf"]),
                   "cash": list(m["cash_weight"].round(4))},
        "cost_total_pct": float(m.attrs.get("total_cost", 0)),
        "withholding_total_pct": float(m.attrs.get("total_withholding", 0)),
        "avg_monthly_cost": float(m["cost"].mean()),
        "holdings": hold,
        "sleeves_full": sleeves_for(m), "sleeves_5y": sleeves_for(last5),
        "regimes_full": regimes_for(m), "regimes_5y": regimes_for(last5),
        "sleeve_nav": {labels[c]: list((cap * (1 + m[c].fillna(0)).cumprod()).round(0)) for c in sleeve_cols if not (m[c].fillna(0) == 0).all()},
        "data_coverage": {mk: int(panels[mk]["id"].nunique()) for mk in panels},
        "stop_loss": (cfg.get("risk", {}) or {}).get("stop_loss", {}),
        "crises": crises_for(m),
        "trade_outcomes": outcomes_for([{**x, "name": names.get(x["market"], {}).get(x["id"], "")} for x in out.get("spells", [])]),
        "trades": [{**x, "name": names.get(x["market"], {}).get(x["id"], "")} for x in out.get("spells", [])],
        "crisis_defs": cfg["backtest"].get("crises", []),
        "daily_stop_tickers": int(daily_box["df"].shape[1]) if len(daily_box["df"]) else 0,
        "stops": [{**s_, "name": names.get(s_["market"], {}).get(s_["id"], "")} for s_ in out.get("stops", [])],
    }
    # ---------- 多方案比較 ----------
    variants = {}
    if not args.no_variants:
        for v in cfg["backtest"].get("variants", []) or []:
            name, patch = v["name"], v.get("patch") or {}
            log.info("variant: %s", name)
            vm = run_cfg(apply_patch(cfg, patch))["monthly"] if patch else m
            v5 = vm[vm.index > vm.index[-1] - pd.DateOffset(years=5)]
            sf, s5 = stats_for(vm), stats_for(v5)
            mid = vm.index[len(vm) // 2]
            h1, h2 = vm[vm.index <= mid], vm[vm.index > mid]
            half = {}
            for lab, sub in (("h1", h1), ("h2", h2)):
                st_ = stats_for(sub)
                half[lab] = {"cagr": st_["策略（扣費後）"]["cagr"], "mdd": st_["策略（扣費後）"]["max_drawdown"],
                             "bench_cagr": st_.get("混合基準(20/20/60)", {}).get("cagr"),
                             "from": sub.index[0].strftime("%Y-%m"), "to": sub.index[-1].strftime("%Y-%m")}
            variants[name] = {
                "full": sf["策略（扣費後）"], "full_gross": sf["策略（未扣費）"], "y5": s5["策略（扣費後）"],
                "regimes": regimes_for(vm), "sleeves": sleeves_for(vm),
                "avg_tw": float(vm["n_tw"].mean()), "avg_us": float(vm["n_us"].mean()),
                "n_stops": int(vm["n_stops"].sum()) if "n_stops" in vm else 0,
                "crises": crises_for(vm),
                "halves": half,
                "group": v.get("group", "stop" if set(patch) <= {"risk"} else "other"),
                "weights": apply_patch(cfg, patch)["portfolio"]["weights"],
                "fixed": apply_patch(cfg, patch)["portfolio"].get("fixed_sleeves", []),
                "is_stop_variant": v.get("group", "stop" if set(patch) <= {"risk"} else "other") in ("stop", "macro"),
                "credit_off_months": int(vm["credit_off"].sum()) if "credit_off" in vm else 0,
                "nav": list((cap * (1 + vm["ret_net"].fillna(0)).cumprod()).round(0)),
            }
    res["variants"] = variants
    # ---------- 風險／報酬散佈（效率前緣參考） ----------
    pts = [{"name": k, "kind": "variant", "group": v.get("group"), "cagr": v["full"]["cagr"],
            "vol": v["full"]["vol"], "mdd": v["full"]["max_drawdown"], "sharpe": v["full"]["sharpe"]}
           for k, v in variants.items()]
    for k, v in res["stats_full"].items():
        if k not in ("策略（扣費後）", "策略（未扣費）"):
            pts.append({"name": k, "kind": "bench", "cagr": v["cagr"], "vol": v["vol"],
                        "mdd": v["max_drawdown"], "sharpe": v["sharpe"]})
    res["frontier"] = pts
    # ---------- 第二層：指數壓力測試（只測停損規則） ----------
    sc = cfg["backtest"].get("stress") or {}
    if sc and not args.no_stress:
        try:
            from screener.backtest import StopRule
            from screener.common import CACHE_DIR
            from screener.stress import run_stress
            rules = {"無停損": StopRule(apply_patch(cfg, {"risk": {"stop_loss": {"enabled": False}}}))}
            for v in cfg["backtest"].get("variants", []) or []:
                patch = v.get("patch") or {}
                if v.get("group", "stop") == "stop" and set(patch) <= {"risk"} and v["name"] not in rules:
                    rules[v["name"]] = StopRule(apply_patch(cfg, patch))
            sd = prices.load_daily_adj(sc.get("assets", []), start="1999-01-01",
                                       path=CACHE_DIR / "stress_daily.csv.gz")
            res["stress"] = {"rules": list(rules), "episodes": sc.get("episodes", []),
                             "results": run_stress(sd, rules, sc)}
            log.info("stress test: %d assets", len(res["stress"]["results"]))
        except Exception as e:  # noqa
            log.warning("stress test failed: %s", e)
    write_json(Path(args.out), res)
    for k, v in res["stats_full"].items():
        log.info("%-22s CAGR %6.2f%%  MDD %6.2f%%  final %12.0f", k, 100 * v["cagr"],
                 100 * v["max_drawdown"], v["final_amount"])


if __name__ == "__main__":
    main()
