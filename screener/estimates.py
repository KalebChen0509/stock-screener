"""美股分析師預估（Yahoo Finance 免費資料）：目標價、評等分布、EPS 預估、財報超乎／低於預期。

快取 cache/us_estimates.json：{代號: {...}, "_meta": {...}}
每週一整批更新追蹤的公司；其他日子只更新最近幾天公布財報的公司（拿到「超乎預期」數字）。
台股沒有可靠的免費分析師預估來源，所以只做美股。只用在網站與財報快報，不影響選股。
"""
from __future__ import annotations

import json
import math
import time

import pandas as pd

from .common import CACHE_DIR, log

PATH = CACHE_DIR / "us_estimates.json"


def _f(x):
    try:
        x = float(x)
        return None if math.isnan(x) or math.isinf(x) else x
    except (TypeError, ValueError):
        return None


def one(sym: str, yf=None) -> dict | None:
    """單一檔的預估資料；抓不到的欄位就略過。"""
    if yf is None:
        import yfinance as yf
    t = yf.Ticker(sym)
    out = {}
    try:
        pt = t.analyst_price_targets or {}
        out["target"] = {k: _f(pt.get(k)) for k in ("mean", "median", "high", "low", "current") if _f(pt.get(k)) is not None}
    except Exception as e:  # noqa
        log.debug("targets %s: %s", sym, e)
    try:
        rs = t.recommendations_summary if hasattr(t, "recommendations_summary") else t.recommendations
        if rs is not None and len(rs):
            r0 = rs.iloc[0]
            out["rating"] = {k: int(r0.get(k, 0) or 0) for k in ("strongBuy", "buy", "hold", "sell", "strongSell")}
    except Exception as e:  # noqa
        log.debug("recs %s: %s", sym, e)
    try:
        ee = t.earnings_estimate
        if ee is not None and len(ee):
            out["eps"] = {str(i): {k: _f(ee.loc[i].get(k)) for k in ("avg", "low", "high", "yearAgoEps", "numberOfAnalysts", "growth")}
                          for i in ee.index}
    except Exception as e:  # noqa
        log.debug("eps est %s: %s", sym, e)
    try:
        ed = t.get_earnings_dates(limit=8)
        if ed is not None and len(ed):
            ed = ed.dropna(subset=[c for c in ed.columns if "Reported" in c])
            if len(ed):
                r = ed.iloc[0]
                est = _f(r.get("EPS Estimate"))
                act = _f(r.get("Reported EPS"))
                sur = _f(r.get("Surprise(%)"))
                out["last"] = {"date": str(pd.Timestamp(ed.index[0]).date()), "est": est, "actual": act,
                               "surprise": sur / 100 if sur is not None else (act / est - 1 if est and act is not None else None)}
    except Exception as e:  # noqa
        log.debug("earnings dates %s: %s", sym, e)
    return out or None


def load() -> dict:
    return json.loads(PATH.read_text()) if PATH.exists() else {}


def update(tickers: list[str], today=None, recent: list[str] | None = None, full_weekday: int = 0,
           sleep: float = 0.6, fetch=one) -> dict:
    """週一（full_weekday）整批更新；其他天只更新 recent（最近公布財報的）與還沒有資料的。"""
    today = pd.Timestamp(today or pd.Timestamp.today()).normalize()
    db = load()
    meta = db.get("_meta", {})
    full = today.weekday() == full_weekday and meta.get("full") != str(today.date())
    todo = list(dict.fromkeys(tickers if full else [t for t in (recent or []) if t in tickers] + [t for t in tickers if t not in db]))
    ok = 0
    for sym in todo[:400]:
        try:
            r = fetch(sym)
        except Exception as e:  # noqa
            log.debug("estimates %s: %s", sym, e)
            r = None
        if r:
            r["asof"] = str(today.date())
            db[sym] = r
            ok += 1
        time.sleep(sleep)
    if full:
        meta["full"] = str(today.date())
    meta["updated"] = str(today.date())
    db["_meta"] = meta
    PATH.parent.mkdir(parents=True, exist_ok=True)
    PATH.write_text(json.dumps(db, ensure_ascii=False))
    log.info("estimates: %d/%d updated (full=%s)", ok, len(todo), full)
    return {"updated": ok, "tried": len(todo), "full": full}


def site_table(db: dict | None = None, prices: dict | None = None) -> dict:
    """網站用：目標價（與目前股價的差距）、評等分布、EPS 預估、上次財報超乎預期幅度。"""
    db = load() if db is None else db
    out = {}
    for sym, r in db.items():
        if sym.startswith("_"):
            continue
        x = {"asof": r.get("asof")}
        tg = r.get("target") or {}
        px = (prices or {}).get(sym) or tg.get("current")
        if tg.get("mean"):
            x["target"] = tg.get("mean")
            x["target_hi"], x["target_lo"] = tg.get("high"), tg.get("low")
            if px:
                x["upside"] = tg["mean"] / px - 1
        if r.get("rating"):
            x["rating"] = r["rating"]
        e = r.get("eps") or {}
        for k, lab in (("0q", "eps_q"), ("0y", "eps_y"), ("+1y", "eps_ny")):
            if e.get(k, {}).get("avg") is not None:
                x[lab] = e[k]["avg"]
        if e.get("+1y", {}).get("growth") is not None:
            x["eps_ny_growth"] = e["+1y"]["growth"]
        if e.get("0q", {}).get("numberOfAnalysts"):
            x["n"] = int(e["0q"]["numberOfAnalysts"])
        if r.get("last"):
            x["last"] = r["last"]
        out[sym] = x
    return out


def surprise_text(r: dict | None, report_date=None) -> str:
    """財報快報用：「EPS 2.10 vs 預估 1.95（超乎預期 +7.7%）」。只用日期相符（±5 天）的那一次。"""
    if not r or not r.get("last"):
        return ""
    L = r["last"]
    if report_date is not None and abs((pd.Timestamp(L["date"]) - pd.Timestamp(report_date)).days) > 5:
        return ""
    if L.get("actual") is None or L.get("est") is None:
        return ""
    s = L.get("surprise")
    word = "" if s is None else ("超乎預期" if s > 0.005 else "低於預期" if s < -0.005 else "符合預期")
    return f"EPS {L['actual']:.2f} vs 預估 {L['est']:.2f}" + (f"（{word} {s * 100:+.1f}%）" if s is not None else "")
