"""美股下市公司股價（Tiingo）：用來估計美股回測的倖存者偏差。

美股回測的股票池是「現在還在交易」的公司（SEC 目前的代號清單＋Yahoo 股價），倒閉、被併購而下市的公司不在裡面。
Tiingo 免費帳號每月最多 500 檔不同股票、每小時約 50 次請求，抓不完全部下市公司（2016～2026 約 4,000 檔），
所以「隨機抽樣」N 檔，抓完整日線，再用抽樣比例推估全體的影響（scripts/us_survivorship.py）。

API Key 只放在 GitHub Secrets（TIINGO_API_KEY），程式從環境變數讀，不印出來。
快取（data 分支 cache/us_delisted/）：
  tickers.csv     Tiingo 支援的代號清單（免 Key）篩出的下市候選
  sample.csv      抽中的代號（固定亂數種子，重跑也一樣）
  daily.csv.gz    抽中代號的日線（date, ticker, close, adj_close, volume）
  state.json      已抓完／抓不到的代號
"""
from __future__ import annotations

import io
import json
import os
import time
import zipfile

import numpy as np
import pandas as pd
import requests

from .common import CACHE_DIR, log

DIR = CACHE_DIR / "us_delisted"
LIST_URL = "https://apimedia.tiingo.com/docs/tiingo/daily/supported_tickers.zip"
PRICE_URL = "https://api.tiingo.com/tiingo/daily/{t}/prices"
EXCHANGES = {"NYSE", "NASDAQ", "AMEX", "NYSE MKT", "NYSE ARCA", "BATS"}
END_FROM, END_TO = "2016-10-01", "2026-06-30"     # 回測期間內停止交易
SAMPLE_N = 450                                    # 留一些額度給重試（每月 500 檔）
SEED = 20261106


class RateLimited(Exception):
    pass


def candidates(listing: pd.DataFrame, active: set[str]) -> pd.DataFrame:
    """下市候選：美國交易所普通股（代號 1～5 個英文字母）、回測期間停止交易、交易超過一年；
    代號在清單裡出現不只一次、或現在被別的公司使用的不要（抓到的可能是新公司的股價，例如 BBBY）。"""
    t = listing.copy()
    for c in ("ticker", "exchange", "assetType", "priceCurrency", "startDate", "endDate"):
        t[c] = t[c].fillna("").astype(str)
    t = t[(t["assetType"] == "Stock") & (t["priceCurrency"] == "USD") & t["exchange"].isin(EXCHANGES)]
    n = t["ticker"].value_counts()
    t = t[(t["endDate"] >= END_FROM) & (t["endDate"] <= END_TO) & t["ticker"].str.fullmatch(r"[A-Z]{1,5}")]
    t = t[(t["ticker"].map(n) == 1) & ~t["ticker"].isin(active)]
    life = pd.to_datetime(t["endDate"], errors="coerce") - pd.to_datetime(t["startDate"], errors="coerce")
    return t[life > pd.Timedelta(days=365)][["ticker", "exchange", "startDate", "endDate"]].reset_index(drop=True)


def draw_sample(cand: pd.DataFrame, n: int = SAMPLE_N, seed: int = SEED) -> pd.DataFrame:
    """固定種子隨機抽樣（跟下市年份無關，才能用抽樣比例推估全體）。"""
    n = min(n, len(cand))
    idx = np.random.default_rng(seed).choice(len(cand), size=n, replace=False)
    out = cand.iloc[sorted(idx)].reset_index(drop=True)
    out["weight"] = len(cand) / n                  # 每一檔代表全體的幾檔
    return out


def parse_prices(js, ticker: str) -> pd.DataFrame:
    rows = [{"date": r.get("date", "")[:10], "ticker": ticker, "close": r.get("close"), "adj_close": r.get("adjClose"),
             "volume": r.get("volume")} for r in (js or []) if isinstance(r, dict)]
    df = pd.DataFrame(rows, columns=["date", "ticker", "close", "adj_close", "volume"])
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    return df.dropna(subset=["date", "adj_close"])


def _state() -> dict:
    p = DIR / "state.json"
    return json.loads(p.read_text()) if p.exists() else {"done": [], "miss": []}


def _save_state(st: dict):
    DIR.mkdir(parents=True, exist_ok=True)
    (DIR / "state.json").write_text(json.dumps(st, indent=1))


def load_daily() -> pd.DataFrame:
    p = DIR / "daily.csv.gz"
    return pd.read_csv(p, parse_dates=["date"]) if p.exists() else pd.DataFrame(columns=["date", "ticker", "close", "adj_close", "volume"])


def prepare(http=None, active: set[str] | None = None) -> pd.DataFrame:
    """下載 Tiingo 代號清單（免 Key）→ 候選 → 抽樣。已經抽過就沿用（不會因為清單更新而換樣本）。"""
    DIR.mkdir(parents=True, exist_ok=True)
    sp = DIR / "sample.csv"
    if sp.exists():
        return pd.read_csv(sp, dtype={"ticker": str})
    r = (http or requests).get(LIST_URL, timeout=120)
    r.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
        listing = pd.read_csv(z.open(z.namelist()[0]), dtype=str, keep_default_na=False)
    if active is None:
        from .data_us import load_universe
        active = set(load_universe()["ticker"].astype(str))
    cand = candidates(listing, active)
    cand.to_csv(DIR / "tickers.csv", index=False)
    s = draw_sample(cand)
    s.to_csv(sp, index=False)
    log.info("us delisted: %d candidates, sample %d", len(cand), len(s))
    return s


def fetch(max_requests: int = 45, sleep: float = 75.0, http=None, token: str | None = None) -> dict:
    """抓抽樣代號的日線。每次最多 max_requests 檔；被限流（429）就先停，下次接著抓。"""
    token = token if token is not None else os.environ.get("TIINGO_API_KEY", "")
    if not token:
        raise SystemExit("沒有 TIINGO_API_KEY（GitHub Secrets）")
    s = prepare(http)
    st = _state()
    todo = [r for r in s.itertuples() if r.ticker not in st["done"] and r.ticker not in st["miss"]]
    http = http or requests
    got, n = [], 0
    for r in todo[:max_requests]:
        start = (pd.Timestamp(r.startDate) if r.startDate else pd.Timestamp("2015-01-01"))
        start = max(start, pd.Timestamp("2015-01-01")).strftime("%Y-%m-%d")
        try:
            resp = http.get(PRICE_URL.format(t=r.ticker), params={"startDate": start, "endDate": r.endDate},
                            headers={"Authorization": f"Token {token}", "Content-Type": "application/json"}, timeout=60)
        except Exception as e:  # noqa
            log.warning("tiingo %s: %s", r.ticker, e.__class__.__name__)
            continue
        n += 1
        if resp.status_code == 429:
            log.warning("tiingo rate limited after %d requests", n)
            break
        if resp.status_code in (401, 403):
            _save_state(st)
            raise SystemExit("Tiingo 拒絕這把 Key（HTTP %s）：到 GitHub Secrets 確認 TIINGO_API_KEY 有貼對、前後沒有空白" % resp.status_code)
        if resp.status_code == 404:
            st["miss"].append(r.ticker)
        elif resp.status_code == 200:
            df = parse_prices(resp.json(), r.ticker)
            if len(df):
                got.append(df)
                st["done"].append(r.ticker)
            else:
                st["miss"].append(r.ticker)
        else:
            log.warning("tiingo %s: HTTP %s", r.ticker, resp.status_code)
        if n < max_requests:
            time.sleep(sleep)
    if got:
        old = load_daily()
        new = pd.concat([old] + got, ignore_index=True) if len(old) else pd.concat(got, ignore_index=True)
        new = new.drop_duplicates(["ticker", "date"], keep="last").sort_values(["ticker", "date"])
        new.to_csv(DIR / "daily.csv.gz", index=False, compression="gzip")
    _save_state(st)
    left = len(s) - len(st["done"]) - len(st["miss"])
    log.info("us delisted: fetched %d, done %d, miss %d, left %d", len(got), len(st["done"]), len(st["miss"]), left)
    return {"fetched": len(got), "done": len(st["done"]), "miss": len(st["miss"]), "left": left, "requests": n}
