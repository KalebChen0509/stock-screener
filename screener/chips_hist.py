"""台股三大法人＋融資融券「歷史」（每月彙總），用來測試籌碼能不能幫選股。

每天的資料用 chips.fetch_day 抓（上市 T86＋融資、上櫃法人＋融資），抓完一個月就彙總成「每檔股票一列」存起來，
不存日資料（太大）。上市法人 2012/5 起、上櫃法人約 2014 年底起才有資料；回補從 START 開始、由新往舊補。

快取（data 分支 cache/chips_hist/）：
  monthly.csv.gz  month, id, foreign, trust, dealer, total（當月買賣超股數合計）,
                  foreign_days, trust_days（買超天數）, n_days（有法人資料的天數）, margin, short（月底餘額，張）
  state.json      已完成的月份
"""
from __future__ import annotations

import json
import time

import numpy as np
import pandas as pd

from . import chips
from .common import CACHE_DIR, log

DIR = CACHE_DIR / "chips_hist"
START = "2015-01"


def summarize(days: list[pd.DataFrame], month: pd.Timestamp) -> pd.DataFrame:
    """一個月的日資料 → 每檔一列。"""
    if not days:
        return pd.DataFrame()
    d = pd.concat(days, ignore_index=True).sort_values("date")
    g = d.groupby("id")
    out = pd.DataFrame({
        "foreign": g["foreign"].sum(min_count=1), "trust": g["trust"].sum(min_count=1),
        "dealer": g["dealer"].sum(min_count=1), "total": g["total"].sum(min_count=1),
        "foreign_days": g["foreign"].apply(lambda s: int((s > 0).sum())),
        "trust_days": g["trust"].apply(lambda s: int((s > 0).sum())),
        "n_days": g["foreign"].apply(lambda s: int(s.notna().sum())),
        "margin": g["margin"].apply(lambda s: s.dropna().iloc[-1] if s.notna().any() else np.nan),
        "short": g["short"].apply(lambda s: s.dropna().iloc[-1] if s.notna().any() else np.nan),
    }).reset_index()
    out.insert(0, "month", (month + pd.offsets.MonthEnd(0)).strftime("%Y-%m-%d"))
    return out


def load() -> pd.DataFrame:
    p = DIR / "monthly.csv.gz"
    if not p.exists():
        return pd.DataFrame()
    return pd.read_csv(p, parse_dates=["month"], dtype={"id": str})


def _state() -> dict:
    p = DIR / "state.json"
    return json.loads(p.read_text()) if p.exists() else {"done": []}


def todo_months(today=None, start: str = START) -> list[pd.Timestamp]:
    """還沒完成的完整月份，由新到舊。"""
    today = pd.Timestamp(today or pd.Timestamp.now(tz="Asia/Taipei").tz_localize(None)).normalize()
    last_full = today.to_period("M") - 1
    done = set(_state()["done"])
    ms = [p.to_timestamp() for p in pd.period_range(start, last_full, freq="M")]
    return [m for m in reversed(ms) if m.strftime("%Y-%m") not in done]


def update(max_minutes: float = 300, sleep: float = 1.2, today=None, fetch=None, start: str = START) -> dict:
    """由新往舊補完整月份；時間到、或連不上就先停（不把月份記成完成），下次接著補。"""
    fetch = fetch or (lambda day: chips.fetch_day(day, sleep=sleep))
    t0 = time.time()
    DIR.mkdir(parents=True, exist_ok=True)
    st = _state()
    done_now = []
    for m in todo_months(today, start):
        if (time.time() - t0) / 60 > max_minutes:
            break
        days, failed = [], False
        for day in pd.bdate_range(m, m + pd.offsets.MonthEnd(0)):
            x = fetch(day)
            if x is None:                       # 連不上：等一下重試一次，還是不行就整個月下次再補
                time.sleep(sleep * 10)
                x = fetch(day)
            if x is None:
                failed = True
                break
            if isinstance(x, pd.DataFrame) and len(x):
                days.append(x)
            time.sleep(sleep)
        if failed:
            log.warning("chips hist: %s 連不上，先停", m.strftime("%Y-%m"))
            break
        new = summarize(days, m)
        old = load()
        if len(old):
            old = old[old["month"].dt.strftime("%Y-%m") != m.strftime("%Y-%m")]
            old["month"] = old["month"].dt.strftime("%Y-%m-%d")
        allm = pd.concat([old, new], ignore_index=True) if len(old) else new
        allm.sort_values(["month", "id"]).to_csv(DIR / "monthly.csv.gz", index=False, compression="gzip")
        st["done"].append(m.strftime("%Y-%m"))
        (DIR / "state.json").write_text(json.dumps(st, indent=1))
        done_now.append(m.strftime("%Y-%m"))
        log.info("chips hist: %s 完成（%d 天、%d 檔）", m.strftime("%Y-%m"), len(days), len(new))
    left = len(todo_months(today, start))
    return {"done": done_now, "left": left, "minutes": round((time.time() - t0) / 60, 1)}
