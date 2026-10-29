"""並行模擬（paper trading）：不動用實際的錢，每月照各版本的規則「假裝」換股並記錄績效。

做法（設定在 config 的 paper）：
  1. 從第一次決策（start 的前一個月底）到最後一個完整月份，用回測同一套程式往前跑（含停損、交易成本），
     得到已完成月份的報酬；最後一個月底的決策（還沒有報酬）就是「目前的模擬持股」。
  2. 每個月的決策第一次算出來就寫進 data/cache/paper_log.json 鎖定，之後資料更新也不改，
     網站顯示的持股以鎖定的為準（重新計算結果不同時會標示出來）。
  3. 當月還沒結束：用最新的日收盤價估計月初到今天的報酬（不含月中停損，只是估計）。
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd

from . import prices
from .backtest import run_backtest
from .common import CACHE_DIR, apply_patch, log

PAPER_LOG = CACHE_DIR / "paper_log.json"
PAPER_DAILY = CACHE_DIR / "paper_daily.csv.gz"


def versions(cfg: dict) -> list[tuple[str, dict]]:
    """paper.versions 裡的方案名稱 → (名稱, 設定 patch)；名稱要在 backtest.variants 裡。"""
    p = cfg.get("paper") or {}
    var = {v["name"]: (v.get("patch") or {}) for v in ((cfg.get("backtest") or {}).get("variants") or [])}
    out = []
    for n in p.get("versions") or []:
        if n in var:
            out.append((n, var[n]))
        else:
            log.warning("paper: version %s not found in backtest.variants", n)
    return out


def _last(daily: pd.DataFrame, tk: str, t) -> float:
    if daily is None or tk not in daily:
        return np.nan
    s = daily[tk].loc[:t].dropna()
    return float(s.iloc[-1]) if len(s) else np.nan


def mark_to_market(weights: list[dict], daily: pd.DataFrame, t0) -> tuple[float | None, str | None, float]:
    """從月底 t0 的收盤到日線最新一天，依決策權重估計報酬（台幣計價；美股乘上匯率變化）。
    回傳 (報酬, 最新日期, 有價格的權重比例)。現金（權重合計不到 1 的部分）報酬為 0。"""
    if daily is None or daily.empty or not weights:
        return None, None, 0.0
    last_day = daily.index.max()
    if last_day <= pd.Timestamp(t0):
        return 0.0, pd.Timestamp(t0).strftime("%Y-%m-%d"), 1.0
    fx0, fx1 = _last(daily, "TWD=X", t0), _last(daily, "TWD=X", last_day)
    fx_r = fx1 / fx0 if np.isfinite(fx0) and np.isfinite(fx1) and fx0 > 0 else 1.0
    r, covered, tot = 0.0, 0.0, 0.0
    for x in weights:
        tk, w = x["ticker"], float(x["w"])
        tot += w
        p0, p1 = _last(daily, tk, t0), _last(daily, tk, last_day)
        if not (np.isfinite(p0) and np.isfinite(p1)) or p0 <= 0:
            continue
        rr = p1 / p0 - 1
        if not tk.endswith((".TW", ".TWO")):
            rr = (1 + rr) * fx_r - 1
        r += w * rr
        covered += w
    return float(r), last_day.strftime("%Y-%m-%d"), (covered / tot if tot > 0 else 0.0)


def _cum(r: pd.Series) -> float | None:
    r = pd.to_numeric(r, errors="coerce").dropna()
    return float((1 + r).prod() - 1) if len(r) else None


def update(cfg: dict, panels: dict, infos: dict, monthly: pd.DataFrame, etf_static: pd.DataFrame,
           events: dict | None = None, month_rev: pd.DataFrame | None = None, today=None,
           daily_loader=None) -> dict | None:
    """更新並行模擬，回傳網站用的 paper.json 內容。daily_loader(tickers, start) → 日線寬表（測試可替換）。"""
    p = cfg.get("paper") or {}
    if not p.get("enabled"):
        return None
    today = pd.Timestamp(today or pd.Timestamp.today()).normalize()
    start = pd.Timestamp(p["start"])
    first_decision = start + pd.offsets.MonthEnd(0) - pd.offsets.MonthEnd(1)
    last_full = today - pd.offsets.MonthEnd(1)
    vers = versions(cfg)
    res = {"start": start.strftime("%Y-%m"), "first_decision": first_decision.strftime("%Y-%m-%d"),
           "as_of": today.strftime("%Y-%m-%d"), "versions": [], "status": "running"}
    if last_full < first_decision or not vers:
        res["status"] = "waiting"
        return res
    lg = json.loads(PAPER_LOG.read_text()) if PAPER_LOG.exists() else {}
    names = {mk: infos[mk]["name"].to_dict() for mk in infos}
    cache: dict = {}

    def run_all(daily=None):
        out = {}
        for name, patch in vers:
            c = apply_patch(cfg, patch)
            out[name] = run_backtest(c, panels, infos, monthly, etf_static, start=str(start.date()),
                                     end=str(last_full.date()), events=events, month_rev=month_rev,
                                     screen_cache=cache, daily=daily, decide_last=True)
        return out

    outs = run_all()
    # 有日線才能逐日模擬停損（同回測），也用來估計當月報酬
    tks = {x["ticker"] for o in outs.values() for h in o["holdings"] for x in h.get("weights", [])} | {"TWD=X"}
    d_start = str((first_decision - pd.DateOffset(days=60)).date())
    loader = daily_loader or (lambda t, s: prices.load_daily_adj(t, start=s, path=PAPER_DAILY, max_age_days=1))
    try:
        daily = loader(sorted(tks), d_start)
    except Exception as e:  # noqa
        log.warning("paper: daily prices failed: %s", e)
        daily = pd.DataFrame()
    if len(daily):
        outs = run_all(daily)
    for name, _patch in vers:
        o = outs[name]
        m = o["monthly"]
        hl = o["holdings"]
        final = hl[-1] if hl and hl[-1].get("final") else None
        lv = lg.setdefault(name, {})
        for h in hl:
            k = pd.Timestamp(h["month"]).strftime("%Y-%m")
            if k not in lv:
                lv[k] = {"first_seen": today.strftime("%Y-%m-%d"), "weights": h.get("weights", [])}
        dm = pd.Timestamp(final["month"]).strftime("%Y-%m") if final else None
        locked = lv.get(dm, {}).get("weights", []) if dm else []
        now_tk = {x["ticker"] for x in (final or {}).get("weights", [])}
        changed = bool(final) and now_tk != {x["ticker"] for x in locked}
        mtd, mtd_day, cov = mark_to_market(locked, daily, final["month"]) if final else (None, None, 0.0)
        r_net = m["ret_net"] if "ret_net" in m else pd.Series(dtype=float)
        cum = _cum(r_net)
        cum_all = ((1 + (cum or 0.0)) * (1 + (mtd or 0.0)) - 1) if (cum is not None or mtd is not None) else None
        hold = {"tw": [], "us": [], "etf": []}
        for x in locked:
            if x.get("kind") == "stock" and x.get("mkt") in ("tw", "us"):
                hold[x["mkt"]].append([x["id"], names.get(x["mkt"], {}).get(x["id"], ""), round(x["w"], 4)])
            else:
                hold["etf"].append([x["ticker"], {"fill": "大盤補位", "core": "固定部位"}.get(x.get("kind"), "ETF"),
                                    round(x["w"], 4)])
        for k_ in hold:
            hold[k_].sort(key=lambda z: -z[2])
        sl = {}
        for mk, sc, bc in (("tw", "sleeve_tw", "bench_0050.TW"), ("us", "sleeve_us", "bench_SPY")):
            if sc in m and bc in m and len(m):
                sl[mk] = {"cum": _cum(m[sc]), "bench_cum": _cum(m[bc])}
        res["versions"].append({
            "name": name, "decision_month": dm, "months": [pd.Timestamp(i).strftime("%Y-%m") for i in m.index],
            "monthly_net": [float(x) for x in r_net.fillna(0)], "cum_closed": cum,
            "mtd": mtd, "mtd_to": mtd_day, "mtd_coverage": cov, "cum": cum_all,
            "sleeves": sl, "holdings": hold, "changed_since_locked": changed,
            "n_stops": int(m["n_stops"].sum()) if "n_stops" in m and len(m) else 0,
        })
    PAPER_LOG.write_text(json.dumps(lg, ensure_ascii=False))
    ctrl = next((v for v in res["versions"] if v["name"].startswith("只買 ETF")), None)
    if ctrl and ctrl.get("cum") is not None:
        for v in res["versions"]:
            v["vs_etf"] = (v["cum"] - ctrl["cum"]) if v.get("cum") is not None else None
    log.info("paper: %s", {v["name"]: (round(v["cum"], 4) if v.get("cum") is not None else None) for v in res["versions"]})
    return res
