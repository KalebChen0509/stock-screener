"""盤中提醒（主力：Mac 的 scripts/mac_events.py 盤中每 5 分鐘；備援：GitHub Actions）：讀網站上的持股與合理價、data 分支的買進價，抓報價後檢查。

報價：日線歷史（均線／KD 用）一律 Yahoo；Mac 上台股的「今天」改用永豐即時快照（trader/quotes.py，子程序、唯讀 Key），
永豐登入失敗／拿不到就退回 Yahoo（約延遲 15～20 分鐘），每則提醒註明來源。美股只有 Yahoo。
"""
import json
import os
import re
import subprocess
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


def load_levels(cfg: dict, local: bool = True) -> dict:
    """自己設的價位：config/alert_levels.yaml（網站「🔔 提醒」產生）＋ alerts.yaml 的 levels。"""
    out = {m: dict(((cfg.get("levels") or {}).get(m) or {})) for m in ("tw", "us", "etf")}
    p = ROOT / "config" / "alert_levels.yaml"
    if local and p.exists():
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


RAW_MAIN = "https://raw.githubusercontent.com/{repo}/main/{path}"


def read_config(name: str, remote_repo: str | None):
    """config/ 底下的 YAML。remote_repo 有值（Mac）→ 讀 GitHub 上最新版（網站「🔔 提醒」改的是 GitHub 上的檔案，Mac 本機可能是舊的）。"""
    if remote_repo:
        try:
            r = requests.get(RAW_MAIN.format(repo=remote_repo, path=f"config/{name}"), timeout=30,
                             headers={"Cache-Control": "no-cache"})
            if r.status_code == 200:
                return yaml.safe_load(r.text) or {}
            if r.status_code == 404:
                return {}
        except Exception as e:  # noqa
            log.warning("讀 GitHub 上的 %s 失敗，改用本機：%s", name, e)
    p = ROOT / "config" / name
    return (yaml.safe_load(p.read_text(encoding="utf-8")) or {}) if p.exists() else {}


SINOPAC_PY = Path.home() / ".sinopac-venv" / "bin" / "python"
_ID_RE = re.compile(r"(?<![A-Za-z0-9])([A-Z])[1289][0-9]{8}(?![0-9])")


def sinopac_quotes(codes: list[str], timeout: int = 120) -> dict:
    """永豐即時快照 → {代號: {date, open, high, low, close}}；任何問題回傳 {}（呼叫端退回 Yahoo）。
    子程序的輸出只取最後一行 JSON；錯誤訊息不轉印（可能含身分證字號），只記錯誤類型。"""
    if not codes or not SINOPAC_PY.exists():
        return {}
    try:
        r = subprocess.run([str(SINOPAC_PY), str(ROOT / "trader" / "quotes.py"), *codes], capture_output=True,
                           text=True, timeout=timeout, cwd=str(ROOT))
        line = (r.stdout.strip().splitlines() or [""])[-1]
        res = json.loads(line) if line.startswith("{") else {"ok": False, "error": f"exit {r.returncode}"}
    except Exception as e:  # noqa
        res = {"ok": False, "error": e.__class__.__name__}
    if not res.get("ok"):
        log.warning("永豐報價失敗，改用 Yahoo：%s", _ID_RE.sub(r"\1*********", str(res.get("error")))[:80])
        return {}
    if res.get("usage_mb") is not None:
        log.info("永豐今日流量 %s／%s MB", res.get("usage_mb"), res.get("limit_mb"))
    return res.get("quotes") or {}


def overlay(df, q: dict | None):
    """把即時快照放進日線最後一天：快照日期＝Yahoo 最後一天 → 取代；比較新 → 加一列；比較舊或沒有 → None（不用）。"""
    if df is None or len(df) == 0 or not q:
        return None
    d = pd.Timestamp(q["date"])
    last = pd.Timestamp(df.index[-1])
    last = last.tz_localize(None) if last.tzinfo else last
    if d.normalize() < last.normalize():
        return None
    row = {k: float(q[k]) for k in ("open", "high", "low", "close")}
    out = df[["open", "high", "low", "close"]].astype(float)
    if d.normalize() == last.normalize():
        for k, v in row.items():
            out.iloc[-1, out.columns.get_loc(k)] = v
        return out
    add = pd.DataFrame([row], index=[d.normalize() if not getattr(df.index, "tz", None) else d.normalize().tz_localize(df.index.tz)])
    return pd.concat([out, add[out.columns]])


def run(state_path: Path = STATE, site: str = "", repo: str = "", force: bool = False, now=None,
        remote_config: bool = False, send=None, download=None, live=None) -> dict:
    """檢查一次。state_path＝「今天已提醒」紀錄（Mac 與 GitHub 共用 events 分支的 alerts_state.json）。
    remote_config：設定檔讀 GitHub 上最新版（Mac 用）。send(subject, text)：預設 Email＋LINE（scope=alerts）。
    live(codes) → {代號: 快照}：台股即時報價（Mac 傳 sinopac_quotes；None＝只用 Yahoo）。"""
    state_path = Path(state_path)
    cfg = read_config("alerts.yaml", repo if remote_config else None)
    if not cfg.get("enabled", True):
        return {"skipped": "disabled"}
    now = pd.Timestamp.now(tz="UTC") if now is None else pd.Timestamp(now).tz_convert("UTC")
    open_mkts = {m for m in ("tw", "us") if force or A.market_open(m, now)}
    if not open_mkts:
        log.info("no market open at %s", now)
        return {"skipped": "closed"}
    site = site.rstrip("/")
    port = get_json(f"{site}/data/portfolio.json") if site else None
    stocks = {m: rows(get_json(f"{site}/data/stocks_{m}.json")) for m in ("tw", "us")} if site else {}
    state = get_json(f"https://raw.githubusercontent.com/{repo}/data/cache/notify_state.json") if repo else None
    positions = read_config("positions.yaml", repo if remote_config else None)
    positions = {m: {str(k): v for k, v in ((positions or {}).get(m) or {}).items()} for m in ("tw", "us")}
    levels = load_levels(cfg, local=not remote_config)
    if remote_config:                                   # 網站「🔔 提醒」的價位：用 GitHub 上最新版
        extra = read_config("alert_levels.yaml", repo)
        for m in ("tw", "us", "etf"):
            levels[m].update({str(k): v for k, v in (extra.get(m) or {}).items()})
    cfg = {**cfg, "levels": levels}
    watch = [w for w in build_watch(cfg, port, state, stocks, positions, load_watchlist()) if w["sess"] in open_mkts]
    if not watch:
        log.info("nothing to watch")
        return {"checked": 0, "hits": 0, "new": 0}
    # 台股不知道是上市（.TW）還是上櫃（.TWO）時兩個都抓，哪個有資料用哪個
    for w in watch:
        if w["mkt"] == "tw" and w["yahoo"].endswith(".TW") and not w.get("known"):
            w["alt"] = w["yahoo"][:-3] + ".TWO"
    tick = sorted({w["yahoo"] for w in watch} | {w["alt"] for w in watch if w.get("alt")})
    bars = (download or yahoo_bars)(tick)
    for w in watch:
        if w.get("alt") and (w["yahoo"] not in bars or len(bars[w["yahoo"]]) < 2) and w["alt"] in bars:
            w["yahoo"] = w["alt"]
    src = {}                                            # (mkt, id) → 報價來源
    tw = [w for w in watch if w["sess"] == "tw"]
    quotes = (live([w["yahoo"].split(".")[0] for w in tw]) or {}) if (live and tw) else {}
    for w in watch:
        nb = overlay(bars.get(w["yahoo"]), quotes.get(w["yahoo"].split(".")[0])) if w["sess"] == "tw" else None
        if nb is not None:
            bars[w["yahoo"]] = nb
            src[(w["mkt"], w["id"])] = "永豐即時"
        else:
            src[(w["mkt"], w["id"])] = "Yahoo 延遲"
    hits = A.check(watch, bars, cfg)
    for h in hits:                                      # 每則註明報價來源（key＝mkt:id:規則）
        m, i = h["key"].split(":")[:2]
        h["text"] += f"［{src.get((m, i), 'Yahoo 延遲')}］"
    today = now.tz_convert("Asia/Taipei").strftime("%Y-%m-%d")
    st = json.loads(state_path.read_text()) if state_path.exists() else {}
    fresh, st = A.new_only(hits, st, today)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(st))
    log.info("alerts: %d checked, %d hits, %d new", len(watch), len(hits), len(fresh))
    sent = None
    if fresh:
        body = "\n".join(a["text"] for a in fresh)
        body += ("\n\n［永豐即時］＝永豐 API 即時快照；［Yahoo 延遲］＝Yahoo，約延遲 15～20 分鐘。只是提醒，不會下單。"
                 + (f"\n網站：{site}/" if site else ""))
        t_local = now.tz_convert("Asia/Taipei").strftime("%H:%M")
        subj = f"盤中提醒 {t_local}（台北）：{len(fresh)} 則"
        sent = send(subj, body) if send else notify(subj, body, scope="alerts")
        log.info("notify %s", sent)
    n_live = sum(v == "永豐即時" for v in src.values())
    return {"checked": len(watch), "hits": len(hits), "new": len(fresh), "sent": sent, "live": n_live}


def yahoo_bars(tick: list[str]) -> dict:
    import yfinance as yf
    raw = yf.download(tick, period="6mo", interval="1d", group_by="ticker", auto_adjust=False, progress=False, threads=True)
    bars = {}
    for t in tick:
        try:
            sub = raw[t] if isinstance(raw.columns, pd.MultiIndex) else raw
            bars[t] = sub.rename(columns=str.lower)[["open", "high", "low", "close"]].dropna()
        except Exception:  # noqa
            continue
    return bars


def main():
    print(run(STATE, site=os.environ.get("PAGES_URL", ""), repo=os.environ.get("GITHUB_REPOSITORY", ""),
              force=os.environ.get("ALERTS_FORCE") == "1"))


if __name__ == "__main__":
    main()
