"""衍生性商品資料庫：台灣期交所（期貨、選擇權、三大法人、Put/Call 比）＋美國波動率（VIX）。

目前只用在網站的市場訊號與之後的避險回測，不影響選股，也不會下單。
資料放在快取的 derivs/ 資料夾（data 分支）：

  fut_daily.csv.gz    台指期 TX、小台 MTX、微台 TMF 的日資料（一般交易時段，所有到期月份）
  opt_daily.csv.gz    台指選擇權 TXO：只存「月選擇權」最近 2 個到期月、履約價在近月台指期 ±BAND（10%）內（控制檔案大小）
  pcr.csv.gz          台指選擇權 Put/Call 比（成交量、未平倉）
  insti_fut.csv.gz    三大法人期貨未平倉（口數）：期交所只給最近 3 年，更早的用 FinMind（2018-06 起）
  us_vol.csv.gz       ^VIX、^VIX3M 日收盤（Yahoo）
  state.json          每種資料已經抓完的月份（當月與上個月每次重抓）

期交所的歷史下載一次最多一個月，所以用「月」為單位回補，每次執行有請求數上限，抓不完下次接著抓。
"""
from __future__ import annotations

import csv
import io
import json
import os
import re
import time

import numpy as np
import pandas as pd
import requests

from .common import CACHE_DIR, log

DIR = CACHE_DIR / "derivs"
START = "2007-01"          # 回補起點（含 2008 金融海嘯）
BAND = 0.10                # 選擇權只存近月台指期 ±10% 內的履約價（避險常用的價外 5～10% 都在內）
UA = {"User-Agent": "Mozilla/5.0 (stock-screener; personal research)"}
FUT_URL = "https://www.taifex.com.tw/cht/3/futDataDown"
OPT_URL = "https://www.taifex.com.tw/cht/3/optDataDown"
PCR_URL = "https://www.taifex.com.tw/cht/3/pcRatioDown"
INSTI_URL = "https://www.taifex.com.tw/cht/3/futContractsDateDown"
FINMIND_URL = "https://api.finmindtrade.com/api/v4/data"
FUTS = {"TX": "2007-01", "MTX": "2007-01", "TMF": "2024-07"}     # 微台 2024/7 才上市
INSTI_NAMES = {"臺股期貨": "TX", "小型臺指期貨": "MTX", "微型臺指期貨": "TMF"}
TX_EQUIV = {"TX": 1.0, "MTX": 0.25, "TMF": 0.05}                 # 換算成大台口數（每點 200／50／10 元）
WHO = {"外資": "foreign", "外資及陸資": "foreign", "投信": "trust", "自營商": "dealer"}
FINMIND_FROM = "2018-06"   # FinMind 期貨法人資料從 2018/6 開始
TAIFEX_INSTI_YEARS = 3     # 期交所法人資料只保留最近 3 年


# ---------------------------------------------------------------- 解析（純函式，測試不需網路）
def _num(x):
    s = str(x).replace(",", "").strip()
    if s in ("", "-", "--"):
        return np.nan
    try:
        return float(s)
    except ValueError:
        return np.nan


def _rows(text: str) -> list[dict]:
    """期交所 CSV（已轉成 str）→ [{欄位: 值}]；不是 CSV（查詢錯誤時回傳 HTML）就回傳空的。"""
    text = (text or "").lstrip("﻿")
    if not text or text.lstrip().startswith("<"):
        return []
    rd = csv.reader(io.StringIO(text))
    head = None
    out = []
    for r in rd:
        if not r:
            continue
        r = [c.strip() for c in r]
        if head is None:
            head = r
            continue
        out.append(dict(zip(head, r)))
    return out


def parse_fut(text: str) -> pd.DataFrame:
    """期貨每日交易行情 → date, contract, expiry, open, high, low, close, volume, settle, oi（只留一般時段、單式月份）。"""
    out = []
    for r in _rows(text):
        exp = r.get("到期月份(週別)", "").strip()
        if r.get("交易時段", "一般") != "一般" or not re.fullmatch(r"\d{6}(W\d)?", exp):
            continue                                   # 跳過盤後、價差單（201601/201602）
        out.append({"date": r.get("交易日期"), "contract": r.get("契約"), "expiry": exp,
                    "open": _num(r.get("開盤價")), "high": _num(r.get("最高價")), "low": _num(r.get("最低價")),
                    "close": _num(r.get("收盤價")), "volume": _num(r.get("成交量")),
                    "settle": _num(r.get("結算價")), "oi": _num(r.get("未沖銷契約數"))})
    df = pd.DataFrame(out, columns=["date", "contract", "expiry", "open", "high", "low", "close", "volume", "settle", "oi"])
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df.loc[df["settle"] <= 0, "settle"] = np.nan    # 到期日當天結算價欄是 0（最後結算價另外公告）
    return df.dropna(subset=["date"])


def front_settle(fut: pd.DataFrame, contract: str = "TX") -> pd.Series:
    """每天「近月」（最近到期的月合約）的結算價。"""
    f = fut[(fut["contract"] == contract) & fut["expiry"].str.fullmatch(r"\d{6}") & fut["settle"].notna()]
    if f.empty:
        return pd.Series(dtype=float)
    f = f.sort_values(["date", "expiry"])
    return f.groupby("date")["settle"].first()


def parse_opt(text: str, under: pd.Series, band: float = BAND, n_exp: int = 2) -> pd.DataFrame:
    """選擇權每日交易行情 → 只留月選擇權最近 n_exp 個到期月、履約價在 under（每天的近月台指期）±band 內。"""
    out = []
    for r in _rows(text):
        exp = r.get("到期月份(週別)", "").strip()
        if r.get("交易時段", "一般") != "一般" or not re.fullmatch(r"\d{6}", exp):
            continue                                   # 只要月選擇權（週選 W／F 不存）
        cp = {"買權": "C", "賣權": "P"}.get(r.get("買賣權"))
        if cp is None:
            continue
        out.append({"date": r.get("交易日期"), "expiry": exp, "strike": _num(r.get("履約價")), "cp": cp,
                    "close": _num(r.get("收盤價")), "settle": _num(r.get("結算價")),
                    "volume": _num(r.get("成交量")), "oi": _num(r.get("未沖銷契約數"))})
    df = pd.DataFrame(out, columns=["date", "expiry", "strike", "cp", "close", "settle", "volume", "oi"])
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df = df.dropna(subset=["date", "strike"])
    if df.empty:
        return df
    exps = df.groupby("date")["expiry"].apply(lambda s: set(sorted(s.unique())[:n_exp]))
    df = df[[e in exps[d] for d, e in zip(df["date"], df["expiry"])]]
    u = df["date"].map(under)
    keep = u.notna() & ((df["strike"] / u - 1).abs() <= band)
    return df[keep].reset_index(drop=True)


def parse_pcr(text: str) -> pd.DataFrame:
    out = []
    for r in _rows(text):
        out.append({"date": r.get("日期"), "put_vol": _num(r.get("賣權成交量")), "call_vol": _num(r.get("買權成交量")),
                    "pc_vol": _num(r.get("買賣權成交量比率%")) / 100, "put_oi": _num(r.get("賣權未平倉量")),
                    "call_oi": _num(r.get("買權未平倉量")), "pc_oi": _num(r.get("買賣權未平倉量比率%")) / 100})
    df = pd.DataFrame(out, columns=["date", "put_vol", "call_vol", "pc_vol", "put_oi", "call_oi", "pc_oi"])
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    return df.dropna(subset=["date"])


def parse_insti_taifex(text: str) -> pd.DataFrame:
    """期交所三大法人（依商品）→ date, product, who, long_oi, short_oi, net_oi（口）。"""
    out = []
    for r in _rows(text):
        p, w = INSTI_NAMES.get(r.get("商品名稱")), WHO.get(r.get("身份別"))
        if p is None or w is None:
            continue
        lo, so = _num(r.get("多方未平倉口數")), _num(r.get("空方未平倉口數"))
        out.append({"date": r.get("日期"), "product": p, "who": w, "long_oi": lo, "short_oi": so, "net_oi": lo - so})
    df = pd.DataFrame(out, columns=["date", "product", "who", "long_oi", "short_oi", "net_oi"])
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    return df.dropna(subset=["date"])


def parse_insti_finmind(js: dict) -> pd.DataFrame:
    out = []
    for r in (js or {}).get("data") or []:
        w = WHO.get(r.get("institutional_investors"))
        if w is None or r.get("futures_id") not in TX_EQUIV:
            continue
        lo, so = _num(r.get("long_open_interest_balance_volume")), _num(r.get("short_open_interest_balance_volume"))
        out.append({"date": r.get("date"), "product": r["futures_id"], "who": w, "long_oi": lo, "short_oi": so, "net_oi": lo - so})
    df = pd.DataFrame(out, columns=["date", "product", "who", "long_oi", "short_oi", "net_oi"])
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    return df.dropna(subset=["date"])


def foreign_net_tx(insti: pd.DataFrame) -> pd.Series:
    """外資台指期淨未平倉（換算成大台口數；大台＋小台/4＋微台/20）。"""
    f = insti[insti["who"] == "foreign"].copy()
    if f.empty:
        return pd.Series(dtype=float)
    f["eq"] = f["net_oi"] * f["product"].map(TX_EQUIV)
    return f.groupby("date")["eq"].sum().sort_index()


def front_returns(fut: pd.DataFrame, contract: str = "TX") -> pd.Series:
    """台指期「近月連續」日報酬：每天用前一天持有的近月合約計算（換月時不會把價差當成報酬）。"""
    f = fut[(fut["contract"] == contract) & fut["expiry"].str.fullmatch(r"\d{6}") & fut["settle"].notna()]
    if f.empty:
        return pd.Series(dtype=float)
    px = f.pivot_table(index="date", columns="expiry", values="settle", aggfunc="last").sort_index()
    out, prev = {}, None
    for d, row in px.iterrows():
        avail = row.dropna()
        if prev is not None:
            # 用前一天和今天都有價格的「最近到期」合約；近月今天到期（沒有結算價）時就換下一個月
            both = sorted(set(prev.index) & set(avail.index))
            if both:
                out[d] = avail[both[0]] / prev[both[0]] - 1
        prev = avail if len(avail) else None
    return pd.Series(out, dtype=float).sort_index()


# ---------------------------------------------------------------- 下載
def _months(start: str, end: pd.Timestamp) -> list[pd.Timestamp]:
    return list(pd.period_range(start, end.to_period("M"), freq="M").to_timestamp())


def _post(url: str, data: dict, http=None, tries: int = 3) -> str | None:
    """回傳 Big5 解碼後的文字；連不上回傳 None（下次再試）。"""
    http = http or requests
    for i in range(tries):
        try:
            r = http.post(url, data=data, headers=UA, timeout=90)
            if r.status_code == 200:
                return r.content.decode("big5", errors="replace") if isinstance(r.content, bytes) else str(r.content)
        except Exception as e:  # noqa
            log.debug("derivs %s: %s", url, e)
        time.sleep(3 * (i + 1))
    return None


def _rng(m: pd.Timestamp) -> tuple[str, str]:
    return m.strftime("%Y/%m/01"), (m + pd.offsets.MonthEnd(0)).strftime("%Y/%m/%d")


def _load(name: str) -> pd.DataFrame:
    p = DIR / f"{name}.csv.gz"
    if not p.exists():
        return pd.DataFrame()
    return pd.read_csv(p, parse_dates=["date"], dtype={"expiry": str})


def _save(name: str, df: pd.DataFrame, keys: list[str]):
    DIR.mkdir(parents=True, exist_ok=True)
    df = df.drop_duplicates(keys, keep="last").sort_values(keys)
    df.to_csv(DIR / f"{name}.csv.gz", index=False, compression="gzip")


def _merge(name: str, new: pd.DataFrame, keys: list[str], month: pd.Timestamp | None = None):
    old = _load(name)
    if month is not None and len(old):          # 同一個月重抓時先刪掉舊的（資料可能被更正）
        old = old[old["date"].dt.to_period("M") != month.to_period("M")]
    _save(name, pd.concat([old, new], ignore_index=True) if len(old) else new, keys)


def load_state() -> dict:
    p = DIR / "state.json"
    return json.loads(p.read_text()) if p.exists() else {}


def save_state(st: dict):
    DIR.mkdir(parents=True, exist_ok=True)
    (DIR / "state.json").write_text(json.dumps(st, ensure_ascii=False, indent=1))


def update(max_requests: int = 60, today=None, http=None, sleep: float = 2.0, finmind_token: str | None = None) -> dict:
    """依序補：美國 VIX → 期貨 → 選擇權（要用期貨算價平）→ P/C 比 → 法人。回傳這次做了什麼與整體進度。"""
    today = pd.Timestamp(today or pd.Timestamp.now(tz="Asia/Taipei").tz_localize(None)).normalize()
    st = load_state()
    recent = {today.to_period("M"), today.to_period("M") - 1}          # 這個月和上個月：資料可能還在補／更正
    budget = {"n": max_requests}
    did = {}

    def todo(key, start):
        done = set(st.get(key, []))
        return [m for m in _months(start, today) if m.strftime("%Y-%m") not in done or m.to_period("M") in recent]

    def mark(key, m):
        if m.to_period("M") in recent:
            return                                  # 近兩個月每次都重抓，不記成完成
        st.setdefault(key, [])
        if m.strftime("%Y-%m") not in st[key]:
            st[key].append(m.strftime("%Y-%m"))

    def spend():
        if budget["n"] <= 0:
            return False
        budget["n"] -= 1
        return True

    # 0) 美國波動率（一次抓完整段，Yahoo；只算 1 次請求，放最前面才不會被回補用完額度）
    if spend():
        try:
            from .prices import _download, _to_long
            tk = ["^VIX", "^VIX3M"]
            lf = _to_long(_download(tk, start="2007-01-01", interval="1d"), tk)
            if len(lf):
                v = lf[["ticker", "date", "close"]].copy()
                v["date"] = pd.to_datetime(v["date"]).dt.normalize()
                _save("us_vol", v.dropna(), ["ticker", "date"])
                did["us_vol"] = len(v)
        except Exception as e:  # noqa
            log.warning("us vol failed: %s", e)

    # 1) 期貨
    for c, start in FUTS.items():
        for m in todo(f"fut_{c}", start):
            if not spend():
                break
            a, b = _rng(m)
            txt = _post(FUT_URL, {"down_type": "1", "commodity_id": c, "queryStartDate": a, "queryEndDate": b}, http)
            if txt is None:
                continue
            df = parse_fut(txt)
            _merge("fut_daily", df, ["date", "contract", "expiry"], month=None)
            mark(f"fut_{c}", m)
            did[f"fut_{c}"] = did.get(f"fut_{c}", 0) + 1
            time.sleep(sleep)
    fut = _load("fut_daily")
    under = front_settle(fut, "TX") if len(fut) else pd.Series(dtype=float)
    fut_done = set(st.get("fut_TX", []))

    # 2) 選擇權（那個月的台指期要先有，才知道價平在哪）
    for m in todo("opt_TXO", START):
        if m.strftime("%Y-%m") not in fut_done and m.to_period("M") not in recent:
            continue
        if not spend():
            break
        a, b = _rng(m)
        txt = _post(OPT_URL, {"down_type": "1", "commodity_id": "TXO", "queryStartDate": a, "queryEndDate": b}, http)
        if txt is None:
            continue
        _merge("opt_daily", parse_opt(txt, under), ["date", "expiry", "strike", "cp"], month=m)
        mark("opt_TXO", m)
        did["opt_TXO"] = did.get("opt_TXO", 0) + 1
        time.sleep(sleep)

    # 3) Put/Call 比
    for m in todo("pcr", START):
        if not spend():
            break
        a, b = _rng(m)
        txt = _post(PCR_URL, {"queryStartDate": a, "queryEndDate": b}, http)
        if txt is None:
            continue
        _merge("pcr", parse_pcr(txt), ["date"])
        mark("pcr", m)
        did["pcr"] = did.get("pcr", 0) + 1
        time.sleep(sleep)

    # 4) 三大法人期貨未平倉：最近 3 年用期交所，更早用 FinMind（2018/6 起；沒有 token 也能抓，但有次數限制）
    cut = (today - pd.DateOffset(years=TAIFEX_INSTI_YEARS) + pd.offsets.MonthBegin(1)).normalize()
    for m in todo("insti", FINMIND_FROM):
        if not spend():
            break
        if m >= cut:
            a, b = _rng(m)
            txt = _post(INSTI_URL, {"queryStartDate": a, "queryEndDate": b}, http)
            if txt is None:
                continue
            df = parse_insti_taifex(txt)
        else:
            frames, ok = [], True
            for c in TX_EQUIV:
                try:
                    params = {"dataset": "TaiwanFuturesInstitutionalInvestors", "data_id": c,
                              "start_date": m.strftime("%Y-%m-01"), "end_date": (m + pd.offsets.MonthEnd(0)).strftime("%Y-%m-%d")}
                    tok = finmind_token if finmind_token is not None else os.environ.get("FINMIND_TOKEN", "")
                    r = (http or requests).get(FINMIND_URL, params=params, timeout=60,
                                               headers={"Authorization": f"Bearer {tok}"} if tok else None)
                    js = r.json() if r.status_code == 200 else None
                except Exception:  # noqa
                    js = None
                if js is None or js.get("status") not in (200, None):
                    ok = False
                    break
                frames.append(parse_insti_finmind(js))
            if not ok:
                continue
            df = pd.concat(frames, ignore_index=True)
        _merge("insti_fut", df, ["date", "product", "who"], month=m)
        mark("insti", m)
        did["insti"] = did.get("insti", 0) + 1
        time.sleep(sleep)

    save_state(st)
    prog = progress(st, today)
    log.info("derivs: did %s; progress %s", did, prog)
    return {"did": did, "progress": prog, "requests_left": budget["n"]}


def progress(st: dict | None = None, today=None) -> dict:
    st = st if st is not None else load_state()
    today = pd.Timestamp(today or pd.Timestamp.today()).normalize()
    last_full = today.to_period("M") - 2                               # 近兩個月每次都重抓，不算進度
    out = {}
    for key, start in [*((f"fut_{c}", s) for c, s in FUTS.items()), ("opt_TXO", START), ("pcr", START), ("insti", FINMIND_FROM)]:
        need = [p for p in pd.period_range(start, last_full, freq="M")]
        have = len(set(st.get(key, [])) & {p.strftime("%Y-%m") for p in need})
        out[key] = {"have": have, "total": len(need)}
    out["complete"] = all(v["have"] >= v["total"] for v in out.values() if isinstance(v, dict))
    return out


# ---------------------------------------------------------------- 網站用的摘要
def _pct_rank(s: pd.Series, days: int = 252) -> float | None:
    s = s.dropna()
    if len(s) < 20:
        return None
    w = s.iloc[-days:]
    return float((w <= w.iloc[-1]).mean())


def trend_status(monthly: pd.DataFrame | None = None, n: int = 10) -> dict:
    """大盤趨勢（回測用的規則）：價格跟「近 n 個月月收盤均價」比。
    official＝上一個「完整月份」月底的判斷（回測的避險訊號用這個，下個月才生效）；
    now＝用最新價（含本月至今）的即時狀態，只是預告，月底才算數。
    台股看加權指數（^TWII）、美股看 SPY。"""
    if monthly is None:
        from . import prices
        monthly = prices.load_monthly()
    w = monthly.pivot_table(index="month", columns="ticker", values="close", aggfunc="last").sort_index()
    out = {}
    for mk, tk in (("tw", "^TWII"), ("us", "SPY")):
        if tk not in w:
            continue
        s = w[tk].dropna()
        if len(s) < n + 2:
            continue
        gap = s / s.rolling(n).mean() - 1            # 價格比 n 個月均價高（＋）或低（−）幾成
        last_full = s.index[-2]                       # 最後一筆是本月至今（還沒收完）
        out[mk] = {"ticker": tk, "price": float(s.iloc[-1]), "ma": float(s.iloc[-n:].mean()), "gap_now": float(gap.iloc[-1]),
                   "below_now": bool(gap.iloc[-1] < 0), "official_month": last_full.strftime("%Y-%m"),
                   "gap_official": float(gap.iloc[-2]), "below_official": bool(gap.iloc[-2] < 0),
                   "series": [[d.strftime("%Y-%m"), round(float(v), 4)] for d, v in gap.dropna().iloc[-72:].items()]}
    return out


def summary(monthly: pd.DataFrame | None = None) -> dict:
    """最新的市場訊號，以及在近一年的位置（百分位：0＝一年最低、1＝一年最高）。只是資料，不是買賣建議。"""
    out = {"as_of": None, "signals": [], "progress": progress(), "trend": {}}
    try:
        out["trend"] = trend_status(monthly)
    except Exception as e:  # noqa
        log.warning("trend status failed: %s", e)
    fut, pcr, ins, vol = _load("fut_daily"), _load("pcr"), _load("insti_fut"), _load("us_vol")

    def add(key, label, s, fmt, note):
        s = s.dropna()
        if s.empty:
            return
        out["signals"].append({"key": key, "label": label, "date": s.index[-1].strftime("%Y-%m-%d"),
                               "value": float(s.iloc[-1]), "fmt": fmt, "pct_1y": _pct_rank(s), "note": note,
                               "series": [[d.strftime("%Y-%m-%d"), round(float(v), 4)] for d, v in s.iloc[-260:].items()]})
        d = s.index[-1].strftime("%Y-%m-%d")
        out["as_of"] = max(out["as_of"] or d, d)

    if len(ins):
        add("foreign_tx", "外資台指期淨未平倉（大台口數）", foreign_net_tx(ins), "int",
            "正數＝外資淨做多、負數＝淨做空。外資常用期貨避險現貨，單獨看不代表看多看空")
    if len(pcr):
        p = pcr.set_index("date").sort_index()
        add("pc_oi", "台指選擇權 Put/Call 未平倉比", p["pc_oi"], "ratio", "賣權未平倉÷買權未平倉。偏高常見於市場擔心下跌、買保險的時候")
    if len(fut):
        add("tx_front", "台指期近月結算價", front_settle(fut, "TX"), "price", "")
    if len(vol):
        w = vol.pivot_table(index="date", columns="ticker", values="close").sort_index()
        if "^VIX" in w:
            add("vix", "美國 VIX 波動率指數", w["^VIX"], "price", "S&P 500 選擇權隱含的未來 30 天波動；越高代表市場越恐慌")
        if {"^VIX", "^VIX3M"} <= set(w.columns):
            add("vix_term", "VIX ÷ VIX3M（期限結構）", w["^VIX"] / w["^VIX3M"], "ratio",
                "大於 1＝短期恐慌高於中期（倒掛），歷史上多出現在急跌時")
    return out
