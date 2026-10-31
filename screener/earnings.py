"""財報快報：公司一公布財報就通知，附上財報後的股價反應（相對大盤）與主要數字。

涵蓋：實際持股、追蹤清單、並行模擬的持股、美股市值前 N 大、台股市值前 N 大（config earnings）。
  美股：SEC 8-K 第 2.02 項（Results of Operations，就是財報新聞稿）。用公告時間判斷盤前或盤後，
        盤後公布 → 反應看「隔一個交易日收盤 ÷ 公布當天收盤」；盤前或盤中 → 「當天收盤 ÷ 前一天收盤」。
        營收、淨利年增率等 10-Q 的 XBRL 數字出來後才有（大公司多半同一天）。
  台股季報：FinMind 抓到新一季財報的那天視為公布（通常是前一晚公布），反應看公布後第一個交易日。
  台股月營收：每月 10 日前公布，只列數字（年增率），不看股價反應。
每一筆財報通知兩次以內：剛公布（反應還沒出來）一次、反應出來後一次；月營收一次。
第一次執行時，台股的季報與月營收只記錄目前的進度、不通知（避免一次湧進幾百筆舊資料）。
"""
from __future__ import annotations

import json
import time

import numpy as np
import pandas as pd
import requests

from . import names, prices
from .common import CACHE_DIR, log

STATE_PATH = CACHE_DIR / "earnings_state.json"
WATCH_PATH = CACHE_DIR / "earnings_watch.json"     # 台股要每天檢查季報的公司（fetch_tw 用）
DAILY_PATH = CACHE_DIR / "earnings_daily.csv.gz"
SEC_SUBS = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
SEC_FACTS = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"
SEC_TICKERS = "https://www.sec.gov/files/company_tickers.json"
BENCH = {"tw": "0050.TW", "us": "SPY"}


class Sec:
    """SEC 請求：要帶聯絡資訊的 User-Agent，每秒不超過 10 次。"""

    def __init__(self):
        from .data_us import UA
        self.s = requests.Session()
        self.s.headers.update({"User-Agent": UA, "Accept-Encoding": "gzip, deflate"})
        self.calls = 0

    def get_json(self, url: str) -> dict | None:
        for attempt in range(3):
            try:
                time.sleep(0.15)
                r = self.s.get(url, timeout=30)
                self.calls += 1
                if r.status_code == 404:
                    return None
                if r.status_code == 200:
                    return r.json()
                log.warning("SEC %s HTTP %s", url, r.status_code)
            except (requests.RequestException, ValueError) as e:
                log.warning("SEC %s error %s", url, e)
            time.sleep(2 * (attempt + 1))
        return None


# ----------------------------------------------------------------------------
def coverage(cfg: dict, results: dict, held: dict | None = None, positions: dict | None = None,
             watch: dict | None = None, paper_log: dict | None = None) -> dict:
    """要追蹤財報的公司：{"tw": {代號}, "us": {ticker}}。results：{市場: (全部股票 DataFrame, 排名)}。"""
    ec = cfg.get("earnings") or {}
    out = {"tw": set(), "us": set()}
    for mkt, n in (("tw", int(ec.get("tw_top_n", 50))), ("us", int(ec.get("us_top_n", 100)))):
        df = (results.get(mkt) or (None,))[0]
        if df is not None and len(df) and "market_cap" in df:
            out[mkt] |= set(df.nlargest(n, "market_cap")["id"].astype(str))
        out[mkt] |= {str(x) for x in (held or {}).get(mkt, []) or []}
        out[mkt] |= {str(x) for x in ((positions or {}).get(mkt) or {})}
        out[mkt] |= {str(x) for x in (watch or {}).get(mkt, []) or []}
    for v in (paper_log or {}).values():
        if v:
            for x in v[max(v)].get("weights", []):
                if x.get("kind") == "stock" and x.get("mkt") in out and x.get("id"):
                    out[x["mkt"]].add(str(x["id"]))
    return out


def _timing(accepted: str | None) -> str:
    """SEC 的 acceptanceDateTime 是美東時間（雖然結尾寫 Z）。盤前 < 9:30、盤後 ≥ 16:00。"""
    if not accepted:
        return "after"
    try:
        t = pd.Timestamp(str(accepted)[:19])
    except ValueError:
        return "after"
    h = t.hour + t.minute / 60
    return "before" if h < 9.5 else ("after" if h >= 16 else "during")


def us_events(sec: Sec, tick_cik: dict, since: pd.Timestamp) -> list[dict]:
    """tick_cik：{ticker: cik}。同一家公司多個代號（GOOG／GOOGL）只查一次。"""
    by_cik = {}
    for tk, cik in sorted(tick_cik.items()):
        by_cik.setdefault(int(cik), tk)
    ev = []
    for cik, tk in by_cik.items():
        js = sec.get_json(SEC_SUBS.format(cik=cik))
        rec = ((js or {}).get("filings") or {}).get("recent") or {}
        forms = rec.get("form", [])
        for i, f in enumerate(forms):
            if f != "8-K":
                continue
            items = str((rec.get("items") or [""] * len(forms))[i] or "")
            day = str(rec["filingDate"][i])
            if "2.02" not in [x.strip() for x in items.split(",")] or pd.Timestamp(day) < since:
                continue
            acc = rec["accessionNumber"][i]
            doc = (rec.get("primaryDocument") or [""] * len(forms))[i]
            acc_t = (rec.get("acceptanceDateTime") or [None] * len(forms))[i]
            ev.append({"key": f"us:{acc}", "market": "us", "id": tk, "cik": cik, "type": "earnings",
                       "date": day, "timing": _timing(acc_t), "accepted": acc_t,
                       "url": f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc.replace('-', '')}/{doc}"})
    return ev


def us_numbers(sec: Sec, cik: int, date: str) -> dict | None:
    """10-Q／10-K 的 XBRL 出來後：最新一季營收、淨利、每股盈餘與去年同季比較。還沒出來回傳 None。"""
    from .data_us import company_quarters
    cf = sec.get_json(SEC_FACTS.format(cik=int(cik)))
    if not cf:
        return None
    q = company_quarters(cf, int(cik))
    if q.empty:
        return None
    q = q.sort_values("period_end")
    last = q.iloc[-1]
    if pd.isna(last["available_date"]) or pd.Timestamp(last["available_date"]) < pd.Timestamp(date) - pd.Timedelta(days=3):
        return None                       # 這次財報的 10-Q 還沒申報
    return _yoy(q, last)


def _yoy(q: pd.DataFrame, last) -> dict:
    pe = pd.Timestamp(last["period_end"])
    prev = q[(q["period_end"] - (pe - pd.DateOffset(years=1))).abs() <= pd.Timedelta(days=20)]
    p = prev.iloc[-1] if len(prev) else None

    def g(c):
        a, b = last.get(c), (p.get(c) if p is not None else np.nan)
        return float(a / b - 1) if pd.notna(a) and pd.notna(b) and b > 0 else None
    sh = last.get("shares")
    eps = float(last["net_income"] / sh) if pd.notna(last.get("net_income")) and pd.notna(sh) and sh > 0 else None
    return {"period": pe.strftime("%Y-%m-%d"), "revenue": float(last["revenue"]) if pd.notna(last.get("revenue")) else None,
            "net_income": float(last["net_income"]) if pd.notna(last.get("net_income")) else None,
            "rev_yoy": g("revenue"), "ni_yoy": g("net_income"), "op_yoy": g("op_income"), "eps": eps}


def tw_quarterly(ids: set, seen: dict, today: pd.Timestamp, fin_dir=None) -> list[dict]:
    """台股：快取裡出現新一季財報 → 一筆事件。seen：{代號: 已知最新一季}（會更新）。第一次看到的公司只記錄不通知。"""
    from . import data_tw
    fin_dir = fin_dir or data_tw.TW_FIN_DIR
    ev = []
    for sid in sorted(ids):
        p = fin_dir / f"{sid}.csv.gz"
        if not p.exists():
            continue
        try:
            q = pd.read_csv(p, parse_dates=["period_end"]).sort_values("period_end")
        except Exception:  # noqa
            continue
        if q.empty:
            continue
        last = q.iloc[-1]
        pe = pd.Timestamp(last["period_end"]).strftime("%Y-%m-%d")
        old = seen.get(sid)
        seen[sid] = pe
        if old is None or pe <= old:
            continue
        ev.append({"key": f"twq:{sid}:{pe}", "market": "tw", "id": sid, "type": "earnings", "period": pe,
                   "date": (today - pd.Timedelta(days=1)).strftime("%Y-%m-%d"), "timing": "after",
                   "numbers": _yoy(q, last)})
    return ev


def tw_monthly(ids: set, wide: pd.DataFrame | None, seen: dict) -> list[dict]:
    """台股月營收：出現新的月份 → 一筆事件（年增率、近 3 個月年增率）。"""
    ev = []
    if wide is None or wide.empty:
        return ev
    for sid in sorted(ids):
        if sid not in wide:
            continue
        s = wide[sid].dropna()
        if s.empty:
            continue
        per = s.index[-1]
        k = per.strftime("%Y-%m-%d")
        old = seen.get(sid)
        seen[sid] = k
        if old is None or k <= old:
            continue
        ly = s.get(per - pd.DateOffset(years=1) + pd.offsets.MonthEnd(0))
        s3 = s.loc[:per].iloc[-3:]
        p3 = [s.get(d - pd.DateOffset(years=1) + pd.offsets.MonthEnd(0)) for d in s3.index]
        y3 = float(s3.sum() / np.nansum(p3) - 1) if len(s3) == 3 and all(pd.notna(x) and x > 0 for x in p3) else None
        ev.append({"key": f"twm:{sid}:{k}", "market": "tw", "id": sid, "type": "month_rev", "period": k,
                   "date": None, "numbers": {"revenue": float(s.iloc[-1]),
                                             "rev_yoy": float(s.iloc[-1] / ly - 1) if ly and ly > 0 else None,
                                             "rev_yoy_3m": y3}})
    return ev


def reaction(daily: pd.DataFrame, tk: str, bench: str, date: str, timing: str) -> dict | None:
    """財報後的股價反應（含息還原日收盤）。還沒有收盤資料時回傳 None（下次再算）。"""
    if daily is None or tk not in daily:
        return None
    s = daily[tk].dropna()
    if s.empty:
        return None
    d = pd.Timestamp(date)
    if timing == "after":
        a, b = s[s.index <= d], s[s.index > d]
    else:
        a, b = s[s.index < d], s[s.index >= d]
    if a.empty or b.empty:
        return None
    t0, t1 = a.index[-1], b.index[0]
    r = float(b.iloc[0] / a.iloc[-1] - 1)
    rb = None
    if bench in daily:
        bs = daily[bench].dropna()
        b0, b1 = bs[bs.index <= t0], bs[bs.index <= t1]
        if len(b0) and len(b1):
            rb = float(b1.iloc[-1] / b0.iloc[-1] - 1)
    return {"from": t0.strftime("%Y-%m-%d"), "to": t1.strftime("%Y-%m-%d"), "ret": r, "bench": rb,
            "excess": (r - rb) if rb is not None else None}


# ----------------------------------------------------------------------------
def _pct(x, d=1):
    return "–" if x is None or (isinstance(x, float) and not np.isfinite(x)) else f"{x * 100:+.{d}f}%"


def _line(e: dict) -> str:
    """兩行：「・代號 中文名｜說明」＋「公布時間；財報後漲跌；營收、淨利年增」。"""
    nm = names.tag(e["market"], e["id"], e.get("name"))
    n = e.get("numbers") or {}
    if e["type"] == "month_rev":
        return f"・{nm}\n    {e['period'][:7]} 月營收 年增 {_pct(n.get('rev_yoy'))}（近 3 個月 {_pct(n.get('rev_yoy_3m'))}）"
    if e["market"] == "tw":
        pe = pd.Timestamp(e.get("period") or e["date"])
        head = f"{pe.year}Q{(pe.month - 1) // 3 + 1} 季報（{pd.Timestamp(e['first_seen']).strftime('%m/%d')} 抓到）" \
            if e.get("first_seen") else f"{pe.year}Q{(pe.month - 1) // 3 + 1} 季報"
    else:
        when = {"before": "盤前", "during": "盤中", "after": "盤後"}.get(e.get("timing"), "")
        head = f"{pd.Timestamp(e['date']).strftime('%m/%d')} {when}公布" if e.get("date") else ""
    r = e.get("reaction")
    parts = [head]
    if r:
        bn = "SPY" if e["market"] == "us" else "0050"
        parts.append(f"財報後 {_pct(r['ret'])}（{bn} {_pct(r['bench'])}，相對 {_pct(r['excess'])}）")
    if n:
        bits = []
        if n.get("rev_yoy") is not None:
            bits.append(f"營收年增 {_pct(n['rev_yoy'])}")
        if n.get("ni_yoy") is not None:
            bits.append(f"淨利年增 {_pct(n['ni_yoy'])}")
        if n.get("eps") is not None:
            bits.append(f"單季 EPS {n['eps']:.2f}")
        if bits:
            parts.append("、".join(bits))
    return f"・{nm}\n    " + "；".join(p_ for p_ in parts if p_)


def digest(events: list[dict], upcoming: list[dict], today: pd.Timestamp) -> str:
    """要通知的內容（沒有新的財報就回傳空字串）。events 是這次要通知的事件。"""
    if not events:
        return ""
    lines = []
    done = [e for e in events if e["type"] == "earnings" and e.get("reaction")]
    new = [e for e in events if e["type"] == "earnings" and not e.get("reaction")]
    mrev = [e for e in events if e["type"] == "month_rev"]
    for mk, label in (("us", "美股"), ("tw", "台股季報")):
        d = sorted([e for e in done if e["market"] == mk], key=lambda e: -(abs(e["reaction"]["excess"] or 0)))
        if d:
            lines.append(f"【{label}：財報後漲跌】")
            lines += [_line(e) for e in d]
    if new:
        lines.append("【剛公布，下次回報漲跌】")
        lines += [_line(e) for e in sorted(new, key=lambda e: (e["market"], e["id"]))]
    if mrev:
        lines.append("【台股月營收】")
        lines += [_line(e) for e in sorted(mrev, key=lambda e: -((e.get("numbers") or {}).get("rev_yoy") or -9))]
    nxt = [u for u in upcoming if 0 <= (pd.Timestamp(u["date"]) - today).days <= 7]
    if nxt:
        lines.append("【未來 7 天預定公布（美股）】")
        lines.append("、".join(f"{u['id']} {names.info('us', u['id'], u.get('name'))[0]} {pd.Timestamp(u['date']).strftime('%m/%d')}"
                               for u in nxt[:30]))
    return "\n".join(lines)


def _upcoming_yf(tickers: list[str]) -> dict:
    """美股下一次財報日（Yahoo Finance）。抓不到就略過。"""
    out = {}
    try:
        import yfinance as yf
    except Exception:  # noqa
        return out
    for t in tickers:
        try:
            cal = yf.Ticker(t).calendar
            dts = (cal or {}).get("Earnings Date") if isinstance(cal, dict) else None
            if dts:
                out[t] = pd.Timestamp(dts[0]).strftime("%Y-%m-%d")
        except Exception:  # noqa
            pass
        time.sleep(0.2)
    return out


def update(cfg: dict, results: dict, infos: dict, held: dict | None = None, positions: dict | None = None,
           watch: dict | None = None, paper_log: dict | None = None, month_rev: pd.DataFrame | None = None,
           today=None, sec: Sec | None = None, daily_loader=None, upcoming_fn=None, fin_dir=None) -> dict | None:
    """更新財報事件。回傳 {"site": 網站資料, "digest": 要通知的文字}。"""
    ec = cfg.get("earnings") or {}
    if not ec.get("enabled"):
        return None
    today = pd.Timestamp(today or pd.Timestamp.today()).normalize()
    st = json.loads(STATE_PATH.read_text()) if STATE_PATH.exists() else {}
    cov = coverage(cfg, results, held, positions, watch, paper_log)
    WATCH_PATH.write_text(json.dumps({"tw": sorted(cov["tw"]), "us": sorted(cov["us"])}))
    nmap = {mk: infos[mk]["name"].to_dict() for mk in infos}
    for mk, pos in (positions or {}).items():
        for k, v in (pos or {}).items():
            if isinstance(v, dict) and v.get("name"):
                nmap.setdefault(mk, {}).setdefault(str(k), v["name"])
    sec = sec or Sec()
    # ---- 美股代號 → CIK（股票清單沒有的，例如 BRK-B，用 SEC 的代號表；每週更新一次）----
    tick_cik = {}
    if "us" in infos and "cik" in infos["us"]:
        tick_cik.update({t: int(c) for t, c in infos["us"]["cik"].dropna().items()})
    need = [t for t in cov["us"] if t not in tick_cik]
    if need:
        if not st.get("cik_map") or (today - pd.Timestamp(st.get("cik_map_date", "2000-01-01"))).days > 6:
            js = sec.get_json(SEC_TICKERS) or {}
            st["cik_map"] = {str(v["ticker"]).upper(): int(v["cik_str"]) for v in js.values()} if js else st.get("cik_map", {})
            st["cik_map_date"] = today.strftime("%Y-%m-%d")
        cm = st.get("cik_map") or {}
        for t in need:
            c = cm.get(t.upper()) or cm.get(t.upper().replace(".", "-")) or cm.get(t.upper().replace("-", "."))
            if c:
                tick_cik[t] = int(c)
    us_map = {t: tick_cik[t] for t in cov["us"] if t in tick_cik}
    # ---- 新事件 ----
    events = {e["key"]: e for e in st.get("events", [])}
    since = today - pd.Timedelta(days=int(ec.get("lookback_days", 10)))
    found = us_events(sec, us_map, since) if us_map else []
    first_tw = "tw_q" not in st
    st.setdefault("tw_q", {})
    st.setdefault("tw_m", {})
    found += tw_quarterly(cov["tw"], st["tw_q"], today, fin_dir)
    found += tw_monthly(cov["tw"], month_rev, st["tw_m"])
    if first_tw:
        found = [e for e in found if e["market"] == "us"]     # 第一次：台股只記錄進度
    for e in found:
        if e["key"] not in events:
            e["name"] = nmap.get(e["market"], {}).get(e["id"], "")
            e["status"] = "new"
            e["first_seen"] = today.strftime("%Y-%m-%d")
            events[e["key"]] = e
    # ---- 股價反應 ----
    ymap = {mk: (infos[mk]["yahoo"].to_dict() if mk in infos else {}) for mk in ("tw", "us")}
    tks = {ymap[e["market"]].get(e["id"], e["id"]) for e in events.values() if e["type"] == "earnings"} | set(BENCH.values())
    loader = daily_loader or (lambda t, s: prices.load_daily_adj(t, start=s, path=DAILY_PATH, max_age_days=1))
    try:
        daily = loader(sorted(tks), str((today - pd.Timedelta(days=70)).date()))
    except Exception as e:  # noqa
        log.warning("earnings: daily prices failed: %s", e)
        daily = pd.DataFrame()
    to_notify = []
    for e in events.values():
        if e["type"] == "earnings" and not e.get("reaction"):
            e["reaction"] = reaction(daily, ymap[e["market"]].get(e["id"], e["id"]), BENCH[e["market"]], e["date"], e["timing"])
        if e["market"] == "us" and e["type"] == "earnings" and not e.get("numbers") and e.get("facts_tries", 0) < 7:
            e["facts_tries"] = e.get("facts_tries", 0) + 1
            try:
                e["numbers"] = us_numbers(sec, e["cik"], e["date"])
            except Exception as ex:  # noqa
                log.warning("earnings: numbers %s failed: %s", e["id"], ex)
        # 通知：剛公布一次（反應還沒出來）、反應出來一次；月營收一次。
        # 這裡只挑出要通知的；真的寄出去之後才由 mark_notified 記錄（寄信失敗下次會再寄）
        if e["type"] == "month_rev":
            if not e.get("sent"):
                to_notify.append((e, "sent"))
        elif e.get("reaction"):
            if not e.get("sent_done"):
                to_notify.append((e, "sent_done"))
        elif not e.get("sent_new"):
            to_notify.append((e, "sent_new"))
    # ---- 美股未來財報日（每週更新一次）----
    if (today - pd.Timestamp(st.get("upcoming_date", "2000-01-01"))).days > 6 or upcoming_fn is not None:
        fn = upcoming_fn or _upcoming_yf
        try:
            st["upcoming"] = fn(sorted(ymap["us"].get(t, t) for t in cov["us"]))
            st["upcoming_date"] = today.strftime("%Y-%m-%d")
        except Exception as ex:  # noqa
            log.warning("earnings: upcoming failed: %s", ex)
    y2id = {v: k for k, v in ymap["us"].items()}
    upcoming = sorted([{"id": y2id.get(t, t), "name": nmap.get("us", {}).get(y2id.get(t, t), ""), "date": d}
                       for t, d in (st.get("upcoming") or {}).items() if pd.Timestamp(d) >= today],
                      key=lambda u: u["date"])
    # ---- 存檔、網站資料 ----
    keep = today - pd.Timedelta(days=int(ec.get("keep_days", 120)))
    evs = [e for e in events.values() if pd.Timestamp(e.get("date") or e.get("first_seen")) >= keep]
    st["events"] = evs
    STATE_PATH.write_text(json.dumps(st, ensure_ascii=False, default=str))
    recent = sorted(evs, key=lambda e: (e.get("date") or e.get("first_seen") or ""), reverse=True)
    site = {"as_of": today.strftime("%Y-%m-%d"), "coverage": {k: len(v) for k, v in cov.items()},
            "recent": recent[:300], "upcoming": upcoming[:200],
            "tw_deadlines": "台股季報法定期限：Q1 5/15、Q2 8/14、Q3 11/14、年報 3/31；月營收每月 10 日前",
            "sec_calls": sec.calls}
    log.info("earnings: coverage tw %d us %d, new events %d, notify %d, SEC calls %d",
             len(cov["tw"]), len(cov["us"]), len(found), len(to_notify), sec.calls)
    return {"site": site, "digest": digest([e for e, _ in to_notify], upcoming, today),
            "keys": [[e["key"], flag] for e, flag in to_notify]}


def mark_notified(keys: list) -> None:
    """通知真的送出去之後才呼叫：記錄哪些財報已經通知過（反應出來的那次也代表剛公布那次不用再通知）。"""
    if not keys or not STATE_PATH.exists():
        return
    st = json.loads(STATE_PATH.read_text())
    want = {}
    for k, flag in keys:
        want.setdefault(k, set()).add(flag)
    for e in st.get("events", []):
        for flag in want.get(e["key"], ()):
            e[flag] = True
            if flag == "sent_done":
                e["sent_new"] = True
    STATE_PATH.write_text(json.dumps(st, ensure_ascii=False, default=str))
