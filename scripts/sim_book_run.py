"""模擬投資帳本：在 Mac 上計算「系統線」「我的線」與基準，結果寫回私人倉庫（stock-screener-private），每週寄一封績效信。

由 scripts/mac_events.py 每小時呼叫一次；也可以手動跑：
    .venv/bin/python scripts/sim_book_run.py            # 計算＋上傳（到了寄信時間才寄）
    .venv/bin/python scripts/sim_book_run.py --email    # 立刻寄一封績效信（測試用）
    .venv/bin/python scripts/sim_book_run.py --dry      # 只計算、印出摘要，不上傳、不寄信
私人倉庫的檔案（網站「我的模擬」頁讀寫）：
    sim/config.json   {"monthly": 10000, "start": "2026-11-01", "system_version": "..."}
    sim/trades.json   [{"id","created"（UTC ISO）,"market":"tw"|"us","code","side":"buy"|"sell","amount"（台幣）,"frac","note"}]
    sim/results.json  Mac 寫的計算結果（網站顯示）
私人倉庫用 Mac 鑰匙圈裡的 GitHub 權杖（跟 push_update.sh 同一個），權杖要能存取 stock-screener-private。
"""
import argparse
import io
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd  # noqa: E402
import requests  # noqa: E402

from screener import sim_book as SB  # noqa: E402
from screener.common import log  # noqa: E402

HOME = Path.home() / ".stock-screener"
PRIV = HOME / "private"
PRIV_URL = "https://x-access-token@github.com/KalebChen0509/stock-screener-private.git"
RAW = "https://raw.githubusercontent.com/KalebChen0509/stock-screener"
DEFAULT = {"monthly": 10000, "start": "2026-11-01", "system_version": "成長動能（上限 10%）＋月底檢查停損",
           "fallback_version": "成長動能（單檔上限 10%）"}
BENCH = {"全買 0050（每月 1 萬）": {"0050.TW": 1.0}, "只買 ETF（每月 1 萬）": SB.ETF_ONLY}
ENV = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}


def git(*a, cwd=PRIV, check=True):
    return subprocess.run(["git", *a], cwd=cwd, capture_output=True, text=True, env=ENV, check=check)


def sync_repo() -> bool:
    """clone 或 pull 私人倉庫；失敗（還沒建立、權杖沒有權限）回傳 False。"""
    try:
        if not (PRIV / ".git").exists():
            HOME.mkdir(parents=True, exist_ok=True)
            r = subprocess.run(["git", "-c", "credential.helper=osxkeychain", "clone", "-q", PRIV_URL, str(PRIV)], capture_output=True, text=True, env=ENV)
            if r.returncode:
                log.warning("私人倉庫 clone 失敗：%s", r.stderr.strip()[-200:])
                return False
            git("config", "credential.helper", "osxkeychain")
        if not git("ls-remote", "--heads", "origin", check=False).stdout.strip():
            return True                                     # 空倉庫（建立時沒勾 README）：第一次推送時建立 main
        r = git("pull", "-q", "--rebase", "origin", "main", check=False)
        if r.returncode:
            log.warning("私人倉庫 pull 失敗：%s", r.stderr.strip()[-200:])
            return False
        return True
    except Exception as e:  # noqa
        log.warning("私人倉庫：%s", e)
        return False


def push_repo(msg: str) -> bool:
    git("add", "-A")
    if not git("status", "--porcelain").stdout.strip():
        return True
    git("-c", "user.name=sim-book", "-c", "user.email=sim-book@users.noreply.github.com", "commit", "-qm", msg)
    r = git("push", "-q", "origin", "HEAD:main", check=False)
    if r.returncode:                                          # 手機剛好也存了一筆：先合併再推
        git("pull", "-q", "--rebase", "origin", "main", check=False)
        r = git("push", "-q", "origin", "HEAD:main", check=False)
    if r.returncode:
        log.warning("私人倉庫 push 失敗：%s", r.stderr.strip()[-200:])
    return r.returncode == 0


def read_json(p: Path, default):
    try:
        return json.loads(p.read_text())
    except (OSError, json.JSONDecodeError):
        return default


def get_public(path: str, kind="json"):
    r = requests.get(f"{RAW}/{path}", timeout=60)
    r.raise_for_status()
    return r.json() if kind == "json" else r.text


def yahoo_map() -> dict:
    """台股代號 → Yahoo 代號（.TW 上市／.TWO 上櫃）。"""
    try:
        u = pd.read_csv(io.StringIO(get_public("data/cache/tw_universe.csv", "text")), dtype=str)
        return dict(zip(u["stock_id"], u["yahoo"]))
    except Exception as e:  # noqa
        log.warning("tw_universe：%s", e)
        return {}


def to_ticker(t: dict, ymap: dict) -> str:
    code = str(t.get("code", "")).strip().upper()
    if t.get("market") == "tw":
        return ymap.get(code) or f"{code}.TW"
    return code


def decisions(cfg: dict) -> tuple[dict, str]:
    """系統線：並行模擬鎖定的每月決策 → {月底: {代號: 比重}}。新版本還沒有紀錄的月份用 fallback（第一個月兩者持股相同）。"""
    lg = get_public("data/cache/paper_log.json")
    v, fb = cfg["system_version"], cfg.get("fallback_version")
    out, note = {}, ""
    months = sorted(set(lg.get(v, {})) | set(lg.get(fb, {})))
    for m in months:
        rec = lg.get(v, {}).get(m) or lg.get(fb, {}).get(m)
        if not rec:
            continue
        if m not in lg.get(v, {}):
            note = f"{m} 用「{fb}」的決策（「{v}」那時還沒加入並行模擬；第一個月兩者持股相同）"
        w = {}
        for x in rec.get("weights", []):
            w[x["ticker"]] = w.get(x["ticker"], 0.0) + float(x["w"])
        out[pd.Period(m, "M").to_timestamp("M")] = w
    return out, note


def load_prices(tickers: list[str], start: str) -> SB.Prices:
    import yfinance as yf
    close, adj = {}, {}
    for tk in sorted(set(tickers) | {"TWD=X"}):
        try:
            d = yf.download(tk, start=start, interval="1d", auto_adjust=False, progress=False)
        except Exception as e:  # noqa
            log.warning("股價 %s：%s", tk, e)
            continue
        if d is None or d.empty:
            continue
        c = d["Close"].iloc[:, 0] if isinstance(d["Close"], pd.DataFrame) else d["Close"]
        a = d["Adj Close"].iloc[:, 0] if isinstance(d["Adj Close"], pd.DataFrame) else d["Adj Close"]
        c.index = pd.DatetimeIndex(c.index).tz_localize(None).normalize()
        a.index = c.index
        close[tk], adj[tk] = c.dropna(), a.dropna()
    fx = close.pop("TWD=X", pd.Series(dtype=float))
    adj.pop("TWD=X", None)
    return SB.Prices(pd.DataFrame(close), pd.DataFrame(adj), fx)


def compute(cfg: dict, trades: list[dict]) -> dict:
    ymap = yahoo_map()
    for t in trades:
        t["ticker"] = to_ticker(t, ymap)
    dec, note = decisions(cfg)
    tks = {tk for w in dec.values() for tk in w} | {t["ticker"] for t in trades} | {"0050.TW", "VT", "SPY"}
    start = cfg["start"]
    px = load_prices(sorted(tks), str((pd.Timestamp(start) - pd.Timedelta(days=45)).date()))
    tw_days = px.close["0050.TW"].dropna().index
    days = tw_days[tw_days >= pd.Timestamp(start)]
    if len(days) == 0:
        return {"status": "waiting", "msg": f"還沒到開始日 {start}"}
    names = {}
    try:
        nj = requests.get("https://kalebchen0509.github.io/stock-screener/data/names.json", timeout=60).json()
        for mk in ("tw", "us", "etf"):
            for code, v in (nj.get(mk) or {}).items():
                names[ymap.get(code, code) if mk == "tw" else code] = v[0]
    except Exception:  # noqa
        pass
    names.update({"0050.TW": "元大台灣50", "VT": "Vanguard 全世界股票", "SPY": "SPDR 標普 500"})
    m = float(cfg["monthly"])
    lines = {}
    sysb = SB.run_target_line("系統線", dec, px, days, start, m)
    lines["系統線"] = SB.summarize(sysb, px, names) | {"strategy": cfg["system_version"], "note": note}
    status, good = SB.check_trades(trades, px)          # 網站送來的紀錄先檢查（格式、代號抓不到股價）
    myb, st2 = SB.run_my_line(good, px, days, start, m)
    status.update(st2)
    lines["我的模擬"] = SB.summarize(myb, px, names)
    for k, w in BENCH.items():
        lines[k] = SB.summarize(SB.run_target_line(k, {pd.Timestamp(start) - pd.offsets.MonthEnd(1): w}, px, days, start, m), px, names)
    pend = [t for t in trades if status.get(t["id"], {}).get("status") == "待成交"]
    return {"status": "ok", "updated": pd.Timestamp.now(tz="Asia/Taipei").strftime("%Y-%m-%d %H:%M"),
            "config": cfg, "lines": lines, "trade_status": status, "pending": len(pend),
            "price_date": {"tw": tw_days[-1].strftime("%Y-%m-%d"),
                           "us": px.close["SPY"].dropna().index[-1].strftime("%Y-%m-%d") if "SPY" in px.close else None}}


def email_text(res: dict) -> tuple[str, str]:
    L = res["lines"]
    rows = []
    for k in ("我的模擬", "系統線", "全買 0050（每月 1 萬）", "只買 ETF（每月 1 萬）"):
        x = L.get(k) or {}
        if x.get("empty") or not x:
            continue
        xi = f"、年化 {x['xirr']:+.1%}" if x.get("xirr") is not None else ""
        rows.append(f"・{k}：市值 {x['value']:,} 元／投入 {x['invested']:,} 元，損益 {x['pnl']:+,} 元（報酬 {x['twr']:+.1%}{xi}；最大跌幅 {x['mdd']:+.1%}）")
    me, sy = L.get("我的模擬", {}), L.get("系統線", {})
    top = "、".join(f"{h['name'] or h['ticker']} {h['ret']:+.0%}" for h in (me.get("holdings") or [])[:5] if h.get("ret") is not None)
    wk = pd.Timestamp(res["updated"][:10]) - pd.Timedelta(days=7)
    recent = [t for t in (me.get("trades") or []) if pd.Timestamp(t["date"]) > wk]
    lines = [f"模擬投資週報（{res['updated']}；台股股價到 {res['price_date']['tw']}、美股到 {res['price_date']['us']}）", ""] + rows
    if top:
        lines += ["", f"我的持股：{top}"]
    if recent:
        lines += ["", "這週我的買賣：" + "、".join(f"{t['date'][5:]} {t['side']} {t['ticker']} {t['amount']:,} 元" for t in recent)]
    if res.get("pending"):
        lines += ["", f"還有 {res['pending']} 筆在等收盤成交。"]
    sys_recent = [t for t in (sy.get("trades") or []) if pd.Timestamp(t["date"]) > wk]
    if sys_recent:
        lines += ["", f"系統線這週：{len(sys_recent)} 筆買賣（{sy.get('strategy', '')}）"]
    lines += ["", "網站「我的模擬」頁有完整明細（要在手機輸入過專用權杖才看得到）。",
              "這是模擬，不是真錢、也不是投資建議。報酬＝時間加權；年化＝資金加權（未滿一個月不算）。"]
    subj = f"【模擬週報】我的 {me.get('twr', 0):+.1%}｜系統 {sy.get('twr', 0):+.1%}"
    return subj, "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--email", action="store_true")
    ap.add_argument("--dry", action="store_true")
    a = ap.parse_args(argv)
    if a.dry:
        res = compute(DEFAULT, [])
        for k, x in res.get("lines", {}).items():
            print(k, {kk: x.get(kk) for kk in ("as_of", "value", "invested", "pnl", "twr", "mdd")}, "持股", len(x.get("holdings") or []))
        print(res.get("lines", {}).get("系統線", {}).get("note"))
        return 0
    if not sync_repo():
        print("私人倉庫無法存取（還沒建立，或 GitHub 權杖沒有勾 stock-screener-private）", flush=True)
        return 1
    sim = PRIV / "sim"
    sim.mkdir(exist_ok=True)
    cfg = {**DEFAULT, **read_json(sim / "config.json", {})}
    if not (sim / "config.json").exists():
        (sim / "config.json").write_text(json.dumps({k: cfg[k] for k in ("monthly", "start", "system_version")}, ensure_ascii=False, indent=1))
    if not (sim / "trades.json").exists():
        (sim / "trades.json").write_text("[]")
    trades = read_json(sim / "trades.json", [])
    res = compute(cfg, [dict(t) for t in trades])
    st_p = HOME / "sim_state.json"
    st = read_json(st_p, {})
    now = pd.Timestamp.now(tz="Asia/Taipei")
    # 週報：週六早上 8～12 點（美股週五收盤後）；月初第一個週報另外標記
    wk = f"{now.isocalendar().year}-W{now.isocalendar().week}"
    due = a.email or (now.weekday() == 5 and 8 <= now.hour < 12 and st.get("last_email") != wk)
    res["emailed"] = st.get("last_email")
    (sim / "results.json").write_text(json.dumps(res, ensure_ascii=False, default=str))
    ok = push_repo(f"sim {now:%Y-%m-%d %H:%M}")
    sent = None
    if due and res.get("status") == "ok":
        from screener.notify import notify
        subj, text = email_text(res)
        sent = notify(subj, text, scope="sim")
        if sent.get("email") or sent.get("line"):
            st["last_email"] = wk
    st["last_run"] = now.strftime("%Y-%m-%d %H:%M:%S")
    st_p.write_text(json.dumps(st))
    me = res.get("lines", {}).get("我的模擬", {})
    print(f"模擬帳本：我的 {me.get('twr', 0):+.1%}、系統 {res.get('lines', {}).get('系統線', {}).get('twr', 0):+.1%}、"
          f"待成交 {res.get('pending', 0)}、上傳 {'✔' if ok else '✘'}" + (f"、週報 {sent}" if sent else ""), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
