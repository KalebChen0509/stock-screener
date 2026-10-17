"""新聞與重大事件（第一階段）。

1. 美股：SEC 8-K 事件項目（有完整歷史，可回測）
   4.02 財報不可信賴／重編、1.03 破產、3.01 下市警告 → 否決；4.01 更換會計師 → 否決較短期間
2. 台股：證交所／櫃買中心 OpenAPI「每日重大訊息」，以關鍵字判斷嚴重度。
   這個 API 只提供當天資料，所以系統每天把它存下來，自己累積歷史（目前無法回測過去 10 年）。
3. 台股董監質押比：證交所／櫃買中心 OpenAPI（只有最新值，只用在即時篩選）。
4. 持股與追蹤清單的新聞標題：美股用 Yahoo Finance、台股用 FinMind TaiwanStockNews，
   以關鍵字標記負面新聞，附在每日通知與網站上（不影響篩選）。
"""
from __future__ import annotations

import re
from datetime import date, timedelta

import numpy as np
import pandas as pd
import requests

from .common import CACHE_DIR, ensure_dir, log

TW_EVENTS_PATH = CACHE_DIR / "tw_events.csv.gz"
TW_PLEDGE_PATH = CACHE_DIR / "tw_pledge.csv"

TW_SEVERE = r"重編|更正.*財務報告|保留意見|無法表示意見|否定意見|跳票|退票|重整|破產|終止上市|終止上櫃|停止買賣|掏空|背信|侵占|淨值為負|違約|無法如期償還|紓困|檢調|搜索|羈押"
TW_WARN = r"更換.*會計師|變更.*會計師|會計師.*異動|董事長.*(辭|異動|解任)|總經理.*(辭|異動|解任)|財務主管.*(辭|異動)|會計主管.*(辭|異動)|訴訟|裁罰|罰鍰|減資|下修|資產減損|停工|火災|爆炸|召回"
NEWS_NEG = re.compile(
    r"lawsuit|sued|probe|investigation|fraud|downgrad|recall|cuts? (guidance|outlook|forecast)|lowers? (guidance|outlook|forecast)|"
    r"bankrupt|default|layoff|restat|subpoena|SEC charges|short seller|miss(es|ed)? estimates|"
    r"下修|虧損|調查|起訴|裁罰|違約|跌停|減資|停工|召回|掏空|爆雷|衰退|大跌|重挫|降評", re.I)

TWSE_EVENTS = "https://openapi.twse.com.tw/v1/opendata/t187ap04_L"
TPEX_EVENTS = "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap04_O"
TWSE_PLEDGE = "https://openapi.twse.com.tw/v1/opendata/t187ap11_L"
TPEX_PLEDGE = "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap11_O"


def _get_json(url: str):
    try:
        r = requests.get(url, timeout=60, headers={"User-Agent": "Mozilla/5.0 stock-screener"})
        if r.status_code == 200:
            return r.json()
        log.warning("GET %s -> %s", url, r.status_code)
    except Exception as e:  # noqa
        log.warning("GET %s failed: %s", url, e)
    return None


def _col(rows: list[dict], *keys) -> str | None:
    """在 API 回傳的欄位名稱中，找包含所有關鍵字的那一欄。"""
    if not rows:
        return None
    for k in rows[0].keys():
        if all(x in k for x in keys):
            return k
    return None


# ---------------------------------------------------------------- 美股 8-K
def load_us_events(universe: pd.DataFrame) -> pd.DataFrame:
    from .data_us import US_EVENTS_PATH
    if not US_EVENTS_PATH.exists():
        return pd.DataFrame(columns=["market", "id", "date", "code", "severity", "text"])
    ev = pd.read_csv(US_EVENTS_PATH, dtype={"items": str})
    cmap = dict(zip(universe["cik"].astype(int), universe["ticker"]))
    ev["id"] = ev["cik"].astype(int).map(cmap)
    ev = ev.dropna(subset=["id"])
    ev["date"] = pd.to_datetime(ev["date"])
    rows = []
    for r in ev.itertuples():
        for code in str(r.items).split(","):
            code = code.strip()
            rows.append(("us", r.id, r.date, code))
    return pd.DataFrame(rows, columns=["market", "id", "date", "code"])


# ---------------------------------------------------------------- 台股重大訊息
def update_tw_events() -> pd.DataFrame:
    rows = []
    for url in (TWSE_EVENTS, TPEX_EVENTS):
        js = _get_json(url)
        if not js:
            continue
        cid = _col(js, "公司代號") or _col(js, "代號") or _col(js, "CompanyCode")
        subj = _col(js, "主旨") or _col(js, "標題")
        dcol = _col(js, "發言日期") or _col(js, "日期")
        if not cid or not subj:
            log.warning("unexpected columns from %s: %s", url, list(js[0].keys())[:12])
            continue
        for x in js:
            rows.append({"id": str(x.get(cid, "")).strip(), "date_raw": str(x.get(dcol, "")),
                         "text": str(x.get(subj, "")).strip()})
    if not rows:
        return load_tw_events()
    new = pd.DataFrame(rows)
    new["date"] = new["date_raw"].map(_roc_date).fillna(pd.Timestamp(date.today()))
    new["code"] = np.where(new["text"].str.contains(TW_SEVERE), "severe",
                           np.where(new["text"].str.contains(TW_WARN), "warn", "info"))
    new["market"] = "tw"
    new = new[["market", "id", "date", "code", "text"]]
    old = load_tw_events()
    allv = pd.concat([old, new]).drop_duplicates(["id", "date", "text"])
    ensure_dir(TW_EVENTS_PATH.parent)
    allv.to_csv(TW_EVENTS_PATH, index=False, compression="gzip")
    log.info("TW events: +%d (total %d)", len(new), len(allv))
    return allv


def _roc_date(s: str):
    s = re.sub(r"\D", "", s or "")
    try:
        if len(s) == 7:   # 民國 1131016
            return pd.Timestamp(year=int(s[:3]) + 1911, month=int(s[3:5]), day=int(s[5:7]))
        if len(s) == 8:
            return pd.Timestamp(s)
    except Exception:  # noqa
        return None
    return None


def load_tw_events() -> pd.DataFrame:
    if TW_EVENTS_PATH.exists():
        d = pd.read_csv(TW_EVENTS_PATH, parse_dates=["date"], dtype={"id": str})
        return d
    return pd.DataFrame(columns=["market", "id", "date", "code", "text"])


# ---------------------------------------------------------------- 否決判斷
def event_status(events: pd.DataFrame, date_: pd.Timestamp, cfg_ev: dict) -> pd.DataFrame:
    """回傳 index=id：event_veto（是否否決）、event_flags（說明）。"""
    if events is None or events.empty:
        return pd.DataFrame(columns=["event_veto", "event_flags"])
    veto_items = {str(k): v for k, v in cfg_ev.get("veto_items", {}).items()}
    flag_items = {str(k): v for k, v in cfg_ev.get("flag_items", {}).items()}
    look = max(list(veto_items.values()) + list(flag_items.values()) + [12])
    e = events[(events["date"] <= date_) & (events["date"] > date_ - pd.DateOffset(months=look))]
    out = {}
    for r in e.itertuples():
        months = (date_ - r.date).days / 30.4
        code = str(r.code)
        if code in veto_items and months <= veto_items[code]:
            d = out.setdefault(r.id, [False, []])
            d[0] = True
            d[1].append(f"{r.date:%Y-%m-%d} {label(code, getattr(r, 'text', ''))}")
        elif code in flag_items and months <= flag_items[code]:
            d = out.setdefault(r.id, [False, []])
            d[1].append(f"{r.date:%Y-%m-%d} {label(code, getattr(r, 'text', ''))}")
    df = pd.DataFrame.from_dict(out, orient="index", columns=["event_veto", "event_flags"])
    df["event_flags"] = df["event_flags"].map(lambda x: "；".join(x[-3:]))
    return df


US_ITEM_LABEL = {"4.02": "8-K 4.02 財報不可信賴（需重編）", "1.03": "8-K 1.03 破產／接管",
                 "3.01": "8-K 3.01 不符上市標準／下市通知", "4.01": "8-K 4.01 更換會計師",
                 "2.06": "8-K 2.06 重大資產減損", "2.05": "8-K 2.05 重組／裁員成本",
                 "5.02": "8-K 5.02 董事或高階主管異動"}


def label(code: str, text: str = "") -> str:
    if code in US_ITEM_LABEL:
        return US_ITEM_LABEL[code]
    t = text if isinstance(text, str) else ""
    return ("重大訊息（嚴重）：" if code == "severe" else "重大訊息：") + t[:40]


# ---------------------------------------------------------------- 台股董監質押
def update_tw_pledge() -> pd.DataFrame:
    frames = []
    for url in (TWSE_PLEDGE, TPEX_PLEDGE):
        js = _get_json(url)
        if not js:
            continue
        cid = _col(js, "公司代號") or _col(js, "代號") or _col(js, "CompanyCode")
        held = _col(js, "目前持股") or _col(js, "持股")
        pled = _col(js, "設質股數") or _col(js, "質")
        if not (cid and held and pled):
            log.warning("pledge columns not found at %s: %s", url, list(js[0].keys())[:15])
            continue
        d = pd.DataFrame(js)[[cid, held, pled]]
        d.columns = ["id", "held", "pledged"]
        for c in ("held", "pledged"):
            d[c] = pd.to_numeric(d[c].astype(str).str.replace(",", ""), errors="coerce")
        frames.append(d)
    if not frames:
        return load_tw_pledge()
    d = pd.concat(frames)
    d["id"] = d["id"].astype(str).str.strip()
    g = d.groupby("id")[["held", "pledged"]].sum()
    g["pledge_ratio"] = (g["pledged"] / g["held"]).where(g["held"] > 0)
    g = g.reset_index()[["id", "pledge_ratio"]]
    ensure_dir(TW_PLEDGE_PATH.parent)
    g.to_csv(TW_PLEDGE_PATH, index=False)
    log.info("TW pledge ratios: %d companies", len(g))
    return g


def load_tw_pledge() -> pd.DataFrame:
    if TW_PLEDGE_PATH.exists():
        return pd.read_csv(TW_PLEDGE_PATH, dtype={"id": str})
    return pd.DataFrame(columns=["id", "pledge_ratio"])


# ---------------------------------------------------------------- 新聞標題
def fetch_news(items: list[tuple[str, str, str]], days: int = 3, finmind=None) -> list[dict]:
    """items：[(market, id, yahoo_ticker)]，回傳新聞清單（只用在通知與網站）。"""
    out = []
    since = pd.Timestamp(date.today() - timedelta(days=days))
    for mkt, sid, yt in items:
        try:
            if mkt == "us" or mkt == "etf":
                import yfinance as yf
                for n in (yf.Ticker(yt).news or [])[:8]:
                    c = n.get("content", n)
                    title = c.get("title") or ""
                    ts = c.get("pubDate") or c.get("providerPublishTime")
                    t = pd.to_datetime(ts, unit="s" if isinstance(ts, (int, float)) else None, utc=True)
                    t = t.tz_convert(None) if t is not None and t.tzinfo else t
                    if t is not None and t < since:
                        continue
                    url = (c.get("canonicalUrl") or {}).get("url") or c.get("link") or ""
                    out.append({"market": mkt, "id": sid, "date": str(t)[:16], "title": title,
                                "url": url, "negative": bool(NEWS_NEG.search(title))})
            elif mkt == "tw" and finmind is not None:
                d = finmind.get("TaiwanStockNews", sid, str(since.date()))
                for r in d.tail(8).itertuples():
                    title = getattr(r, "title", "")
                    out.append({"market": mkt, "id": sid, "date": str(getattr(r, "date", ""))[:16],
                                "title": title, "url": getattr(r, "link", ""),
                                "negative": bool(NEWS_NEG.search(title or ""))})
        except Exception as e:  # noqa
            log.debug("news %s %s: %s", mkt, sid, e)
    return out


# ---------------------------------------------------------------- 華爾街日報 RSS（公開標題＋一句摘要）
WSJ_FEEDS = {
    "市場": "https://feeds.content.dowjones.io/public/rss/RSSMarketsMain",
    "美國企業": "https://feeds.content.dowjones.io/public/rss/WSJcomUSBusiness",
    "科技": "https://feeds.content.dowjones.io/public/rss/RSSWSJD",
    "經濟": "https://feeds.content.dowjones.io/public/rss/socialeconomyfeed",
    "國際": "https://feeds.content.dowjones.io/public/rss/RSSWorldNews",
}
_SUFFIX = re.compile(r"\b(inc|corp|corporation|co|company|ltd|plc|holdings?|group|class [a-z]|the|n\.?v\.?|s\.?a\.?|lp|llc)\b\.?", re.I)


def fetch_wsj() -> list[dict]:
    import xml.etree.ElementTree as ET
    out = []
    for cat, url in WSJ_FEEDS.items():
        try:
            r = requests.get(url, timeout=30, headers={"User-Agent": "Mozilla/5.0 stock-screener"})
            root = ET.fromstring(r.content)
        except Exception as e:  # noqa
            log.warning("WSJ feed %s: %s", cat, e)
            continue
        for it in root.iter("item"):
            title = (it.findtext("title") or "").strip()
            out.append({"category": cat, "title": title, "summary": (it.findtext("description") or "").strip()[:300],
                        "url": (it.findtext("link") or "").strip(), "date": (it.findtext("pubDate") or "")[:25],
                        "negative": bool(NEWS_NEG.search(title))})
    seen, uniq = set(), []
    for n in out:
        if n["url"] not in seen:
            seen.add(n["url"])
            uniq.append(n)
    return uniq


def match_wsj(articles: list[dict], companies: list[tuple[str, str, str]]) -> list[dict]:
    """companies：[(market, id, name)]，用公司名稱（去掉 Inc. 等字尾）比對標題與摘要。"""
    res = []
    for mkt, sid, name in companies:
        core = _SUFFIX.sub("", str(name)).replace(",", " ").strip()
        core = re.sub(r"\s+", " ", core)
        if len(core) < 4:
            continue
        pat = re.compile(r"\b" + re.escape(core) + r"\b", re.I)
        for a in articles:
            if pat.search(a["title"]) or pat.search(a["summary"]):
                res.append({"market": mkt, "id": sid, "date": a["date"], "title": a["title"], "url": a["url"],
                            "negative": a["negative"], "source": "WSJ"})
    return res
