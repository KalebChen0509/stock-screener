"""價格資料：Yahoo Finance（yfinance）。

月資料（回測用）存成長格式 data/cache/prices_monthly.csv.gz：
  ticker, month(月底日期), close（已還原分割，未還原股息）, adj_close（含息還原）,
  volume（月成交股數）, split（當月分割倍數）
"""
from __future__ import annotations

import os
import time

import numpy as np
import pandas as pd

from .common import CACHE_DIR, ensure_dir, log

MONTHLY_PATH = CACHE_DIR / "prices_monthly.csv.gz"
DAILY_PATH = CACHE_DIR / "prices_daily.csv.gz"


def _yf():
    import yfinance as yf  # 延遲載入
    return yf


def _download(tickers: list[str], **kw) -> pd.DataFrame:
    yf = _yf()
    for attempt in range(3):
        try:
            df = yf.download(tickers, group_by="ticker", auto_adjust=False, actions=True,
                             threads=True, progress=False, **kw)
            return df
        except Exception as e:  # noqa
            log.warning("yfinance error %s (attempt %d)", e, attempt + 1)
            time.sleep(10 * (attempt + 1))
    return pd.DataFrame()


def _to_long(df: pd.DataFrame, tickers: list[str]) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    rows = []
    if not isinstance(df.columns, pd.MultiIndex):
        df = pd.concat({tickers[0]: df}, axis=1)
    for t in tickers:
        if t not in df.columns.get_level_values(0):
            continue
        sub = df[t].copy()
        if "Close" not in sub or sub["Close"].dropna().empty:
            continue
        sub = sub.dropna(subset=["Close"])
        out = pd.DataFrame({
            "ticker": t,
            "date": sub.index,
            "close": sub["Close"].values,
            "adj_close": sub.get("Adj Close", sub["Close"]).values,
            "volume": sub.get("Volume", pd.Series(np.nan, index=sub.index)).values,
            "split": sub.get("Stock Splits", pd.Series(0.0, index=sub.index)).fillna(0).values,
        })
        rows.append(out)
    if not rows:
        return pd.DataFrame()
    res = pd.concat(rows, ignore_index=True)
    res["date"] = pd.to_datetime(res["date"]).dt.tz_localize(None)
    return res


def fetch_monthly(tickers: list[str], start: str = "2011-01-01", chunk: int = 150,
                  save: bool = True) -> pd.DataFrame:
    tickers = sorted(set(tickers))
    frames = []
    for i in range(0, len(tickers), chunk):
        part = tickers[i:i + chunk]
        df = _download(part, start=start, interval="1mo")
        lf = _to_long(df, part)
        if not lf.empty:
            frames.append(lf)
        log.info("monthly prices %d/%d", min(i + chunk, len(tickers)), len(tickers))
        time.sleep(1.0)
    if not frames:
        return pd.DataFrame(columns=["ticker", "close", "adj_close", "volume", "split", "month"])
    res = pd.concat(frames, ignore_index=True)
    res["month"] = (res["date"] + pd.offsets.MonthEnd(0)).dt.normalize()
    res = res.drop(columns="date").drop_duplicates(["ticker", "month"], keep="last")
    if save:
        ensure_dir(MONTHLY_PATH.parent)
        res.to_csv(MONTHLY_PATH, index=False, compression="gzip")
    return res


def fetch_daily(tickers: list[str], period: str = "2y", chunk: int = 150) -> pd.DataFrame:
    tickers = sorted(set(tickers))
    frames = []
    for i in range(0, len(tickers), chunk):
        part = tickers[i:i + chunk]
        lf = _to_long(_download(part, period=period, interval="1d"), part)
        if not lf.empty:
            frames.append(lf)
        time.sleep(1.0)
    if not frames:
        return pd.DataFrame()
    res = pd.concat(frames, ignore_index=True)
    return res


DELISTED_MONTHLY_PATH = CACHE_DIR / "tw_delisted_prices.csv.gz"
DELISTED_DAILY_PATH = CACHE_DIR / "tw_delisted_daily.csv.gz"


def load_monthly(include_delisted: bool = False) -> pd.DataFrame:
    """include_delisted：併入下市台股的月股價（FinMind，回測用；Yahoo 沒有下市股票）。"""
    m = pd.read_csv(MONTHLY_PATH, parse_dates=["month"])
    if include_delisted and DELISTED_MONTHLY_PATH.exists():
        d = pd.read_csv(DELISTED_MONTHLY_PATH, parse_dates=["month"])
        d = d[~d["ticker"].isin(set(m["ticker"]))]
        if len(d):
            m = pd.concat([m, d[m.columns.intersection(d.columns)]], ignore_index=True)
    return m


def load_delisted_daily() -> pd.DataFrame:
    """下市台股日收盤寬表（index=日期, columns=代號），併進停損用的日線。"""
    if not DELISTED_DAILY_PATH.exists():
        return pd.DataFrame()
    d = pd.read_csv(DELISTED_DAILY_PATH, parse_dates=["date"])
    if d.empty:
        return pd.DataFrame()
    return d.pivot_table(index="date", columns="ticker", values="adj_close", aggfunc="last").sort_index()


def monthly_tickers() -> set:
    if not MONTHLY_PATH.exists():
        return set()
    return set(pd.read_csv(MONTHLY_PATH, usecols=["ticker"])["ticker"].astype(str))


def split_factor_table(monthly: pd.DataFrame) -> pd.DataFrame:
    """每個 ticker 的分割事件（月份, 倍數）。"""
    s = monthly[monthly["split"] > 0][["ticker", "month", "split"]]
    return s.sort_values(["ticker", "month"])


def future_split_factor(splits: pd.DataFrame, ticker_dates: pd.DataFrame) -> pd.Series:
    """F(d) = d 之後（到今天）所有分割倍數的乘積。用來把當時申報的股數換成今天的分割基準。
    ticker_dates 需含 ticker, date 欄位；回傳與其對齊的 Series。"""
    out = pd.Series(1.0, index=ticker_dates.index)
    if splits.empty:
        return out
    g = {t: grp for t, grp in splits.groupby("ticker")}
    for idx, t, d in zip(ticker_dates.index, ticker_dates["ticker"], ticker_dates["date"]):
        grp = g.get(t)
        if grp is None or pd.isna(d):
            continue
        after = grp[grp["month"] > (pd.Timestamp(d) + pd.offsets.MonthEnd(0))]
        if not after.empty:
            out[idx] = float(np.prod(after["split"].values))
    return out


# ---------------------------------------------------------------------
#  回測停損用的日線（只抓曾進入排名名單的股票，避免下載全市場）
# ---------------------------------------------------------------------
BT_DAILY_PATH = CACHE_DIR / "daily_bt.csv.gz"


def _fetch_daily_adj(tickers: list[str], start: str, chunk: int = 100) -> pd.DataFrame:
    frames = []
    for i in range(0, len(tickers), chunk):
        part = tickers[i:i + chunk]
        lf = _to_long(_download(part, start=start, interval="1d"), part)
        if not lf.empty:
            frames.append(lf[["ticker", "date", "adj_close"]])
        log.info("daily (backtest) %d/%d", min(i + chunk, len(tickers)), len(tickers))
        time.sleep(1.0)
    if not frames:
        return pd.DataFrame(columns=["ticker", "date", "adj_close"])
    out = pd.concat(frames, ignore_index=True)
    out["date"] = pd.to_datetime(out["date"]).dt.tz_localize(None).dt.normalize()
    return out


def load_daily_adj(tickers, start: str = "2015-06-01", path=None, max_age_days: int = 20) -> pd.DataFrame:
    """回傳日線含息還原價寬表（index=日期, columns=代號）。

    快取裡已有且夠新的代號不重抓；過舊的整段重抓（含息還原價會因新的除息回溯調整，不能只補尾巴）。
    """
    path = path or BT_DAILY_PATH
    tickers = sorted({t for t in tickers if t})
    cache = pd.read_csv(path, parse_dates=["date"]) if os.path.exists(path) else \
        pd.DataFrame(columns=["ticker", "date", "adj_close"])
    if len(cache):
        cache["date"] = pd.to_datetime(cache["date"])
    fresh_cut = pd.Timestamp.today().normalize() - pd.Timedelta(days=max_age_days)
    first_need = pd.Timestamp(start) + pd.Timedelta(days=40)
    st = cache.groupby("ticker")["date"].agg(["min", "max"]) if len(cache) else pd.DataFrame(columns=["min", "max"])
    need = [t for t in tickers if t not in st.index or st.at[t, "max"] < fresh_cut or st.at[t, "min"] > first_need]
    # 抓不到資料的代號（下市等）記在 .miss，不要每次重試
    miss_p = str(path) + ".miss"
    missed = set(open(miss_p).read().split()) if os.path.exists(miss_p) else set()
    need = [t for t in need if t not in missed or t in st.index]
    if need:
        log.info("daily (backtest): fetching %d tickers", len(need))
        new = _fetch_daily_adj(need, start)
        got = set(new["ticker"])
        missed |= {t for t in need if t not in got}
        cache = pd.concat([cache[~cache["ticker"].isin(got)], new], ignore_index=True)
        cache["adj_close"] = cache["adj_close"].astype(float).round(5)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        cache.to_csv(path, index=False, compression="gzip")
        with open(miss_p, "w") as f:
            f.write("\n".join(sorted(missed)))
    sub = cache[cache["ticker"].isin(tickers)]
    if sub.empty:
        return pd.DataFrame()
    return sub.pivot_table(index="date", columns="ticker", values="adj_close", aggfunc="last").sort_index()
