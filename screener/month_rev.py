"""台股月營收：每月 10 日前公布，比季報快，是「營收動能」最即時的資料。

歷史：FinMind TaiwanStockMonthRevenue（每檔一次請求，依成交值大的先抓，每天用剩下的 API 額度慢慢回填）
每月更新：TWSE／TPEx OpenAPI 一次取得全部公司最新一個月（不耗 FinMind 額度）

快取：data/cache/tw_month_rev.csv.gz（id, period＝營收月份的月底, revenue）
      data/cache/tw_month_rev_meta.json（每檔已回填到哪個月）

時點（避免前視偏誤）：月份 M 的營收在 M+1 月 10 日前公布 → 在 M+1 月底的調倉日才使用。
"""
from __future__ import annotations

import json
from datetime import date

import numpy as np
import pandas as pd
import requests

from .common import CACHE_DIR, log

REV_PATH = CACHE_DIR / "tw_month_rev.csv.gz"
REV_META = CACHE_DIR / "tw_month_rev_meta.json"
OPENAPI = ["https://openapi.twse.com.tw/v1/opendata/t187ap05_L",
           "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap05_O"]


def load() -> pd.DataFrame:
    if not REV_PATH.exists():
        return pd.DataFrame(columns=["id", "period", "revenue"])
    d = pd.read_csv(REV_PATH, dtype={"id": str}, parse_dates=["period"])
    return d


def _save(d: pd.DataFrame) -> None:
    d = d.dropna(subset=["revenue"]).drop_duplicates(["id", "period"], keep="last").sort_values(["id", "period"])
    REV_PATH.parent.mkdir(parents=True, exist_ok=True)
    d.to_csv(REV_PATH, index=False, compression="gzip")


def _meta() -> dict:
    return json.loads(REV_META.read_text()) if REV_META.exists() else {}


def expected_latest_month(today: date | None = None) -> pd.Timestamp:
    """今天應該已公布的最新營收月份（月底日期）。"""
    t = pd.Timestamp(today or date.today())
    back = 1 if t.day > 10 else 2
    return (t.replace(day=1) - pd.DateOffset(months=back) + pd.offsets.MonthEnd(0)).normalize()


def _from_finmind(raw: pd.DataFrame, sid: str) -> pd.DataFrame:
    if raw is None or raw.empty or "revenue" not in raw:
        return pd.DataFrame(columns=["id", "period", "revenue"])
    y = pd.to_numeric(raw.get("revenue_year"), errors="coerce")
    m = pd.to_numeric(raw.get("revenue_month"), errors="coerce")
    per = pd.to_datetime(dict(year=y, month=m, day=1), errors="coerce") + pd.offsets.MonthEnd(0)
    return pd.DataFrame({"id": sid, "period": per, "revenue": pd.to_numeric(raw["revenue"], errors="coerce")}).dropna()


def _find(rec: dict, *keys):
    for k in rec:
        if any(s in k for s in keys):
            return rec[k]
    return None


def fetch_openapi_latest() -> pd.DataFrame:
    """上市＋上櫃最新一個月營收（全部公司一次取得）。欄位名稱以關鍵字比對，格式改變時自動略過。"""
    rows = []
    for url in OPENAPI:
        try:
            r = requests.get(url, timeout=60, headers={"User-Agent": "Mozilla/5.0"})
            r.raise_for_status()
            for rec in r.json():
                sid = _find(rec, "公司代號", "CompanyCode", "SecuritiesCompanyCode")
                ym = str(_find(rec, "資料年月", "DataYearMonth", "YearMonth") or "")
                rev = _find(rec, "當月營收", "MonthlyRevenue", "Revenue")
                if not sid or not ym.isdigit() or rev in (None, ""):
                    continue
                y, mth = (int(ym[:-2]), int(ym[-2:]))
                if y < 1911:
                    y += 1911                     # 民國年
                per = pd.Timestamp(year=y, month=mth, day=1) + pd.offsets.MonthEnd(0)
                rows.append({"id": str(sid).strip(), "period": per,
                             "revenue": float(str(rev).replace(",", "")) * 1000})  # 公開資訊以千元為單位
        except Exception as e:  # noqa
            log.warning("month revenue OpenAPI %s failed: %s", url, e)
    return pd.DataFrame(rows, columns=["id", "period", "revenue"])


def update(universe: pd.DataFrame, fm, liq_rank: dict | None = None, start: str = "2011-01-01") -> dict:
    """先用 OpenAPI 補最新月份，再用 FinMind 剩餘額度回填歷史。回傳進度。"""
    from .data_tw import FinMindLimit
    d = load()
    meta = _meta()
    latest = fetch_openapi_latest()
    if len(latest):
        # OpenAPI 單位若與 FinMind 不同（千元 vs 元），以已有歷史的同月數值比對校正
        if len(d):
            j = latest.merge(d, on=["id", "period"], suffixes=("", "_fm"))
            if len(j) > 20:
                ratio = (j["revenue_fm"] / j["revenue"]).median()
                if np.isfinite(ratio) and ratio > 0:
                    latest["revenue"] *= round(ratio, 3) if abs(ratio - 1) > 0.5 else 1.0
        d = pd.concat([d, latest], ignore_index=True)
        log.info("month revenue OpenAPI: %d rows (%s)", len(latest), latest["period"].max().date())
    ids = list(universe["stock_id"].astype(str))
    # 還沒回填的；以及舊版可能把「抓失敗」記成 0 筆的，補試一次
    todo = [s for s in ids if not meta.get(s, {}).get("backfilled")
            or (meta[s].get("n") == 0 and not meta[s].get("retried"))]
    liq_rank = liq_rank or {}
    todo.sort(key=lambda s: liq_rank.get(s, 10**9))
    done = 0
    try:
        for sid in todo:
            if fm.calls >= fm.max_calls:
                break
            raw = fm.get("TaiwanStockMonthRevenue", sid, start)
            if not getattr(fm, "last_ok", True):
                nf = meta.get(sid, {}).get("fail", 0) + 1
                if nf < 3:                       # 網路或資料來源暫時有問題：下次再抓（連續 3 次失敗才放棄）
                    meta[sid] = {"fail": nf}
                    continue
            new = _from_finmind(raw, sid)
            if len(new):
                d = pd.concat([d, new], ignore_index=True)
            meta[sid] = {"backfilled": True, "n": int(len(new)), "retried": True}
            done += 1
    except FinMindLimit as e:
        log.warning("FinMind limit during month revenue: %s", e)
    if len(d):
        _save(d)
    REV_META.write_text(json.dumps(meta))
    prog = {"have": sum(1 for s in ids if meta.get(s, {}).get("backfilled")), "total": len(ids), "done_now": done}
    log.info("month revenue backfill: %s", prog)
    return prog


def wide(d: pd.DataFrame | None = None) -> pd.DataFrame:
    d = load() if d is None else d
    if d is None or d.empty:
        return pd.DataFrame()
    return d.pivot_table(index="period", columns="id", values="revenue", aggfunc="last").sort_index()


def metrics_asof(w: pd.DataFrame, date_) -> pd.DataFrame:
    """在月底 date_ 能用的月營收（到上個月為止）算：近 3 個月年增率、近 12 個月年增率、加速度。"""
    if w is None or w.empty:
        return pd.DataFrame(columns=["rev_yoy_3m", "rev_yoy_12m", "rev_accel"])
    cut = (pd.Timestamp(date_) + pd.offsets.MonthEnd(0)) - pd.offsets.MonthEnd(1)
    a = w.loc[:cut]
    if len(a) < 24:
        return pd.DataFrame(columns=["rev_yoy_3m", "rev_yoy_12m", "rev_accel"])
    a = a.iloc[-24:]
    s3, p3 = a.iloc[-3:].sum(min_count=3), a.iloc[-15:-12].sum(min_count=3)
    s12, p12 = a.iloc[-12:].sum(min_count=10), a.iloc[-24:-12].sum(min_count=10)
    y3 = (s3 / p3 - 1).where(p3 > 0)
    y12 = (s12 / p12 - 1).where(p12 > 0)
    out = pd.DataFrame({"rev_yoy_3m": y3.clip(-0.9, 5), "rev_yoy_12m": y12.clip(-0.9, 5)})
    out["rev_accel"] = out["rev_yoy_3m"] - out["rev_yoy_12m"]
    # 最新一個月要是近期（避免已停止公布的公司）
    last_ok = a.iloc[-3:].notna().all()
    return out[last_ok.reindex(out.index).fillna(False)]
