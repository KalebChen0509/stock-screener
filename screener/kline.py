"""K 線資料：每檔近 5 年的日線開高低收量＋2011 年起的月線，供網站「看盤」使用（不影響選股與回測）。

快取（data 分支）：
  cache/kline/daily_XX.csv.gz   日線，依代號分成 24 個檔（單檔不超過 GitHub 100MB 限制）
  cache/kline/monthly.csv.gz    月線（只存日線開始之前的月份，只抓一次）
  cache/kline/state.json        每檔的進度、失敗次數
網站：site/data/kline/{tw,us,etf}/{代號}.json，與 kline/index.json

第一次回填 5 年日線一次抓太多會被 Yahoo 擋，所以每次最多回填 backfill_limit 檔，幾天內補完；
之後每天只補最近 10 天。發生股票分割時整檔重抓（舊資料的價格基準會變）。
"""
from __future__ import annotations

import json
import zlib
from pathlib import Path

import numpy as np
import pandas as pd

from . import prices
from .common import CACHE_DIR, log

N_SHARDS = 24
COLS = ["ticker", "date", "open", "high", "low", "close", "volume"]


def kdir() -> Path:
    return CACHE_DIR / "kline"


def shard(t: str) -> int:
    return zlib.crc32(t.encode()) % N_SHARDS


# ----------------------------------------------------------------------------
def _to_long_ohlc(df: pd.DataFrame, tickers: list[str]) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame(columns=COLS + ["split"])
    if not isinstance(df.columns, pd.MultiIndex):
        df = pd.concat({tickers[0]: df}, axis=1)
    rows = []
    lv0 = set(df.columns.get_level_values(0))
    for t in tickers:
        if t not in lv0:
            continue
        sub = df[t]
        if "Close" not in sub or sub["Close"].dropna().empty:
            continue
        sub = sub.dropna(subset=["Close"])
        rows.append(pd.DataFrame({
            "ticker": t, "date": pd.to_datetime(sub.index).tz_localize(None) if getattr(sub.index, "tz", None) else pd.to_datetime(sub.index),
            "open": sub.get("Open", sub["Close"]).values, "high": sub.get("High", sub["Close"]).values,
            "low": sub.get("Low", sub["Close"]).values, "close": sub["Close"].values,
            "volume": sub.get("Volume", pd.Series(np.nan, index=sub.index)).values,
            "split": sub.get("Stock Splits", pd.Series(0.0, index=sub.index)).fillna(0).values}))
    return pd.concat(rows, ignore_index=True) if rows else pd.DataFrame(columns=COLS + ["split"])


def yahoo_fetch(tickers: list[str], start: str, interval: str = "1d", chunk: int = 150) -> pd.DataFrame:
    """Yahoo Finance 開高低收（價格已做分割調整、未做股利還原）。"""
    import time
    out = []
    for i in range(0, len(tickers), chunk):
        part = tickers[i:i + chunk]
        df = prices._download(part, start=start, interval=interval)
        lf = _to_long_ohlc(df, part)
        if len(lf):
            out.append(lf)
        log.info("kline %s %d/%d", interval, min(i + chunk, len(tickers)), len(tickers))
        time.sleep(1.0)
    return pd.concat(out, ignore_index=True) if out else pd.DataFrame(columns=COLS + ["split"])


# ----------------------------------------------------------------------------
def load_daily() -> pd.DataFrame:
    fs = sorted(kdir().glob("daily_*.csv.gz"))
    if not fs:
        return pd.DataFrame(columns=COLS)
    return pd.concat([pd.read_csv(f, parse_dates=["date"]) for f in fs], ignore_index=True)


def save_daily(df: pd.DataFrame) -> None:
    kdir().mkdir(parents=True, exist_ok=True)
    df = df[COLS].sort_values(["ticker", "date"])
    sh = df["ticker"].map(shard)
    for k in range(N_SHARDS):
        df[sh == k].to_csv(kdir() / f"daily_{k:02d}.csv.gz", index=False, compression="gzip", float_format="%.6g")


def load_monthly() -> pd.DataFrame:
    p = kdir() / "monthly.csv.gz"
    return pd.read_csv(p, parse_dates=["date"]) if p.exists() else pd.DataFrame(columns=COLS)


def load_state() -> dict:
    p = kdir() / "state.json"
    return json.loads(p.read_text()) if p.exists() else {}


def _merge(old: pd.DataFrame, new: pd.DataFrame) -> pd.DataFrame:
    """新資料覆蓋同代號、同日期以後的舊資料。"""
    if new.empty:
        return old
    first_new = new.groupby("ticker")["date"].min()
    keep = old[~(old["ticker"].isin(first_new.index) & (old["date"] >= old["ticker"].map(first_new)))]
    return pd.concat([keep, new[COLS]], ignore_index=True)


def update(tickers: list[str], today=None, years: int = 5, backfill_limit: int = 1500, priority: list[str] | None = None,
           fetch=yahoo_fetch, recent_days: int = 10) -> dict:
    """更新 K 線快取。回傳摘要。fetch(tickers, start, interval) 可在測試時替換。"""
    today = pd.Timestamp(today or pd.Timestamp.today()).normalize()
    start5 = today - pd.DateOffset(years=years) - pd.Timedelta(days=10)
    df, st = load_daily(), load_state()
    mon = load_monthly()
    have = set(df["ticker"]) if len(df) else set()
    tickers = list(dict.fromkeys(tickers))
    skip = {t for t, s in st.items() if s.get("skip_until", "") > today.strftime("%Y-%m-%d")}
    # 1) 已有資料：補最近幾天；有分割的整檔重抓
    upd = [t for t in tickers if t in have and t not in skip]
    refetch = [t for t in tickers if st.get(t, {}).get("refetch")]
    if upd:
        new = fetch(upd, str((today - pd.Timedelta(days=recent_days)).date()), "1d")
        if len(new):
            last = df.groupby("ticker")["date"].max()
            sp = new[(new["split"] != 0) & (new["date"] > new["ticker"].map(last))]
            refetch += sorted(set(sp["ticker"]))
            new = new[~new["ticker"].isin(set(sp["ticker"]))]
            df = _merge(df, new)
    # 2) 回填：還沒有資料的（優先順序：持股、追蹤、ETF 等，再依成交值），加上要重抓的
    pri = {t: i for i, t in enumerate(priority or [])}
    todo = [t for t in tickers if t not in have and t not in skip]
    pos = {t: i for i, t in enumerate(tickers)}
    todo.sort(key=lambda t: (pri.get(t, len(pri)), pos[t]))
    back = list(dict.fromkeys(refetch + todo))[:backfill_limit]
    got = set()
    if back:
        full = fetch(back, str(start5.date()), "1d")
        got = set(full["ticker"]) if len(full) else set()
        df = pd.concat([df[~df["ticker"].isin(got)], full[COLS]], ignore_index=True) if len(full) else df
        if got & set(refetch):     # 分割後月線也要重抓
            mon = mon[~mon["ticker"].isin(got & set(refetch))]
            for t in got & set(refetch):
                st.setdefault(t, {})["mfull"] = False
    for t in back:
        s = st.setdefault(t, {})
        s.pop("refetch", None)
        if t in got:
            s["fail"] = 0
        else:
            s["fail"] = s.get("fail", 0) + 1
            if s["fail"] >= 5:      # 連續 5 次抓不到：30 天後再試
                s["skip_until"] = (today + pd.Timedelta(days=30)).strftime("%Y-%m-%d")
                s["fail"] = 0
    for t in set(refetch) - got:
        st.setdefault(t, {})["refetch"] = True
    df = df[df["date"] >= start5].drop_duplicates(["ticker", "date"], keep="last")
    # 3) 月線（日線開始之前的月份）：每檔只抓一次
    first_d = df.groupby("ticker")["date"].min()
    mtodo = [t for t in tickers if t in first_d.index and not st.get(t, {}).get("mfull")][:backfill_limit]
    if mtodo:
        m = fetch(mtodo, "2011-01-01", "1mo")
        if len(m):
            ret = set(m["ticker"])        # 有回資料才算完成（新上市的公司可能沒有更早的月份）
            m["date"] = (pd.to_datetime(m["date"]) + pd.offsets.MonthEnd(0)).dt.normalize()
            m = m[m["date"] < m["ticker"].map(first_d).dt.to_period("M").dt.start_time]
            mon = pd.concat([mon[~mon["ticker"].isin(ret)], m[COLS]], ignore_index=True)
            for t in ret:
                st.setdefault(t, {})["mfull"] = True
    save_daily(df)
    mon[COLS].sort_values(["ticker", "date"]).to_csv(kdir() / "monthly.csv.gz", index=False, compression="gzip",
                                                      float_format="%.6g")
    (kdir() / "state.json").write_text(json.dumps(st))
    n_have = df["ticker"].nunique()
    out = {"tickers": len(tickers), "with_daily": int(n_have), "backfilled": len(got), "remaining": len([t for t in tickers if t not in set(df["ticker"])]),
           "refetched_splits": sorted(set(refetch) & got)}
    log.info("kline: %s", out)
    return out


# ----------------------------------------------------------------------------
def _r(x: float) -> float:
    if not np.isfinite(x):
        return None
    ax = abs(x)
    return round(float(x), 4 if ax < 1 else 3 if ax < 10 else 2 if ax < 10000 else 1)


def export_site(out_dir: Path, ymap: dict, df: pd.DataFrame | None = None, mon: pd.DataFrame | None = None) -> dict:
    """ymap：Yahoo 代號 → (市場, 網站代號)。每檔一個 JSON：日期用 1970 年起的天數，欄位分開存（檔案較小）。"""
    df = load_daily() if df is None else df
    mon = load_monthly() if mon is None else mon
    out_dir = Path(out_dir)
    idx = {"tw": [], "us": [], "etf": []}
    mg = dict(tuple(mon.groupby("ticker"))) if len(mon) else {}
    for t, g in df.groupby("ticker"):
        if t not in ymap:
            continue
        mkt, sid = ymap[t]
        g = g.reset_index(drop=True).sort_values("date")
        doc = {"id": sid, "yahoo": t,
               "t": (g["date"].values.astype("datetime64[D]").astype(np.int64)).tolist(),
               "o": [_r(x) for x in g["open"]], "h": [_r(x) for x in g["high"]], "l": [_r(x) for x in g["low"]],
               "c": [_r(x) for x in g["close"]], "v": [int(x) if np.isfinite(x) else 0 for x in g["volume"]]}
        m = mg.get(t)
        if m is not None and len(m):
            m = m.reset_index(drop=True).sort_values("date")
            doc["m"] = {"t": (m["date"].values.astype("datetime64[D]").astype(np.int64)).tolist(),
                        "o": [_r(x) for x in m["open"]], "h": [_r(x) for x in m["high"]], "l": [_r(x) for x in m["low"]],
                        "c": [_r(x) for x in m["close"]], "v": [int(x) if np.isfinite(x) else 0 for x in m["volume"]]}
        p = out_dir / mkt / f"{sid}.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(doc, separators=(",", ":")))
        idx[mkt].append(sid)
    for k in idx:
        idx[k].sort()
    (out_dir / "index.json").write_text(json.dumps(idx, separators=(",", ":")))
    log.info("kline site files: %s", {k: len(v) for k, v in idx.items()})
    return idx
