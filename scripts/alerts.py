"""盤中提醒（GitHub Actions 每 30 分鐘執行）：讀網站上的持股與合理價、data 分支的買進價，抓 Yahoo 報價後檢查。"""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd  # noqa: E402
import requests  # noqa: E402
import yaml  # noqa: E402

from screener import alerts as A  # noqa: E402
from screener.common import ROOT, load_watchlist, log  # noqa: E402
from screener.notify import notify  # noqa: E402

STATE = Path(os.environ.get("ALERTS_STATE", ROOT / "alerts_state" / "state.json"))


def get_json(url: str):
    try:
        r = requests.get(url, timeout=60, headers={"Cache-Control": "no-cache"})
        return r.json() if r.status_code == 200 else None
    except Exception as e:  # noqa
        log.warning("get %s: %s", url, e)
        return None


def rows(tbl) -> dict:
    if not tbl:
        return {}
    cols = tbl["cols"]
    return {r[cols.index("id")]: dict(zip(cols, r)) for r in tbl["rows"]}


def yahoo(mkt: str, sid: str, row: dict | None) -> str:
    if mkt in ("us", "etf"):
        return sid.replace(".", "-") if mkt == "us" else sid
    y = (row or {}).get("yahoo")
    if isinstance(y, str) and y:
        return y
    return sid if sid.endswith((".TW", ".TWO")) else f"{sid}.TW"


def session(mkt: str, sid: str) -> str:
    """看哪個市場的開盤時間：ETF 依代號判斷（.TW／.TWO 是台股）。"""
    if mkt in ("tw", "us"):
        return mkt
    return "tw" if str(sid).endswith((".TW", ".TWO")) else "us"


def load_levels(cfg: dict) -> dict:
    """自己設的價位：config/alert_levels.yaml（網站「🔔 提醒」產生）＋ alerts.yaml 的 levels。"""
    out = {m: dict(((cfg.get("levels") or {}).get(m) or {})) for m in ("tw", "us", "etf")}
    p = ROOT / "config" / "alert_levels.yaml"
    if p.exists():
        try:
            extra = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
            for m in ("tw", "us", "etf"):
                out[m].update({str(k): v for k, v in (extra.get(m) or {}).items()})
        except Exception as e:  # noqa
            log.warning("alert_levels.yaml 格式有誤：%s", e)
    return out


def build_watch(cfg, port, state, stocks, positions, watch) -> list[dict]:
    pct = float(((port or {}).get("stop_loss") or {}).get("pct", 0.15))
    ent = (state or {}).get("entry", {})
    items = {}

    def add(mkt, sid, key=None, name=None, **kw):
        sid = str(sid)
        row = (stocks.get(mkt) or {}).get(sid) or {}
        w = items.setdefault((mkt, sid), {"mkt": mkt, "id": sid, "sess": session(mkt, sid), "yahoo": key or yahoo(mkt, sid, row),
                                          "tag": f"{sid.split('.')[0]} {name or row.get('name') or ''}".strip(),
                                          "fair_low": row.get("fair_low"), "fair_high": row.get("fair_high")})
        w.update({k: v for k, v in kw.items() if v is not None})

    for h in (port or {}).get("holdings", []):
        if h.get("kind") != "stock":
            continue
        e = ent.get(h["market"], {}).get(h["id"]) or {}
        add(h["market"], h["id"], key=h.get("key") or e.get("yahoo"), name=h.get("name"), stop=(e.get("px") or 0) * (1 - pct) or None,
            known=bool(h.get("key") or e.get("yahoo")) or None)
    for mkt, items_ in (positions or {}).items():
        for sid, p in (items_ or {}).items():
            p = p or {}
            cost = p.get("cost") or ((p.get("cost_total") / p["shares"]) if p.get("cost_total") and p.get("shares") else None)
            add(mkt, sid, name=p.get("name"), stop=cost * (1 - pct) if cost else None)
    for mkt in ("tw", "us"):
        for sid in watch.get(mkt) or []:
            add(mkt, sid)
    for mkt, lvs in (cfg.get("levels") or {}).items():
        for sid, lv in (lvs or {}).items():
            add(mkt, sid, above=(lv or {}).get("above"), below=(lv or {}).get("below"))
    return list(items.values())


def main():
    cfg = yaml.safe_load((ROOT / "config" / "alerts.yaml").read_text(encoding="utf-8")) or {}
    if not cfg.get("enabled", True):
        return
    now = pd.Timestamp.now(tz="UTC")
    force = os.environ.get("ALERTS_FORCE") == "1"
    open_mkts = {m for m in ("tw", "us") if force or A.market_open(m, now)}
    if not open_mkts:
        log.info("no market open at %s", now)
        return
    site = os.environ.get("PAGES_URL", "").rstrip("/")
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    port = get_json(f"{site}/data/portfolio.json") if site else None
    stocks = {m: rows(get_json(f"{site}/data/stocks_{m}.json")) for m in ("tw", "us")} if site else {}
    state = get_json(f"https://raw.githubusercontent.com/{repo}/data/cache/notify_state.json") if repo else None
    pos_p = ROOT / "config" / "positions.yaml"
    positions = yaml.safe_load(pos_p.read_text(encoding="utf-8")) if pos_p.exists() else {}
    positions = {m: {str(k): v for k, v in ((positions or {}).get(m) or {}).items()} for m in ("tw", "us")}
    cfg = {**cfg, "levels": load_levels(cfg)}
    watch = [w for w in build_watch(cfg, port, state, stocks, positions, load_watchlist()) if w["sess"] in open_mkts]
    if not watch:
        log.info("nothing to watch")
        return
    import yfinance as yf
    # 台股不知道是上市（.TW）還是上櫃（.TWO）時兩個都抓，哪個有資料用哪個
    for w in watch:
        if w["mkt"] == "tw" and w["yahoo"].endswith(".TW") and not w.get("known"):
            w["alt"] = w["yahoo"][:-3] + ".TWO"
    tick = sorted({w["yahoo"] for w in watch} | {w["alt"] for w in watch if w.get("alt")})
    raw = yf.download(tick, period="6mo", interval="1d", group_by="ticker", auto_adjust=False, progress=False, threads=True)
    bars = {}
    for t in tick:
        try:
            sub = raw[t] if isinstance(raw.columns, pd.MultiIndex) else raw
            bars[t] = sub.rename(columns=str.lower)[["open", "high", "low", "close"]].dropna()
        except Exception:  # noqa
            continue
    for w in watch:
        if w.get("alt") and (w["yahoo"] not in bars or len(bars[w["yahoo"]]) < 2) and w["alt"] in bars:
            w["yahoo"] = w["alt"]
    hits = A.check(watch, bars, cfg)
    today = now.tz_convert("Asia/Taipei").strftime("%Y-%m-%d")
    st = json.loads(STATE.read_text()) if STATE.exists() else {}
    fresh, st = A.new_only(hits, st, today)
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(st))
    log.info("alerts: %d checked, %d hits, %d new", len(watch), len(hits), len(fresh))
    if fresh:
        body = "\n".join(a["text"] for a in fresh)
        body += "\n\n報價來自 Yahoo，約延遲 15～20 分鐘；只是提醒，不會下單。" + (f"\n網站：{site}/" if site else "")
        t_local = now.tz_convert("Asia/Taipei").strftime("%H:%M")
        print(body)
        r = notify(f"盤中提醒 {t_local}（台北）：{len(fresh)} 則", body)
        log.info("notify %s", r)


if __name__ == "__main__":
    main()
