"""總經指標：信用利差（企業信用風險）。

資料來源：
  BAA10Y   Moody's Baa 公司債殖利率 − 10 年期美債殖利率（FRED，1986 年起，每日）
           Moody's 資料有版權，網站只顯示訊號狀態與變化，不公開原始數列。
  HYG/IEF  高收益債 ETF 對 7–10 年美債 ETF 的含息價格比（Yahoo，2007 年起）
           比值下跌 = 高收益債落後公債 = 信用市場轉差。

訊號（每月底判斷，下個月套用）：
  jump    利差 3 個月內上升 ≥ jump_pp 個百分點
  z       利差高於過去 36 個月平均 z_min 個標準差以上，且近 3 個月仍在上升
  hyg_ma  HYG/IEF 比值跌破自己的 ma_months 個月均線
"""
from __future__ import annotations

import io
import time

import numpy as np
import pandas as pd
import requests

from .common import log

FRED_CSV = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={sid}"


def fetch_fred(sid: str, max_age_hours: float = 20.0) -> pd.Series:
    """下載 FRED 日資料（免金鑰 CSV）。失敗時用快取。"""
    # 存在暫存資料夾、不放進公開的 data 分支（Moody's 資料有版權）
    import tempfile
    from pathlib import Path
    path = Path(tempfile.gettempdir()) / f"fred_{sid}.csv"
    fresh = path.exists() and (time.time() - path.stat().st_mtime) < max_age_hours * 3600
    if not fresh:
        import os
        key = os.environ.get("FRED_API_KEY")
        for attempt in range(4):
            try:
                if key:   # 有金鑰就走官方 API（較穩定）
                    r = requests.get("https://api.stlouisfed.org/fred/series/observations",
                                     params={"series_id": sid, "api_key": key, "file_type": "json"},
                                     timeout=(15, 120))
                    r.raise_for_status()
                    obs = r.json().get("observations", [])
                    df = pd.DataFrame({"date": [o["date"] for o in obs], sid: [o["value"] for o in obs]})
                else:
                    r = requests.get(FRED_CSV.format(sid=sid), timeout=(15, 120),
                                     headers={"User-Agent": "Mozilla/5.0 (Macintosh) personal-research"})
                    r.raise_for_status()
                    df = pd.read_csv(io.StringIO(r.text))
                if df.shape[1] >= 2 and len(df) > 100:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    df.to_csv(path, index=False)
                    break
            except Exception as e:  # noqa
                log.warning("FRED %s attempt %d failed: %s", sid, attempt + 1, e)
                time.sleep(10 * (attempt + 1))
    if not path.exists():
        return pd.Series(dtype=float)
    df = pd.read_csv(path)
    dcol, vcol = df.columns[0], df.columns[1]          # observation_date / DATE , 值
    s = pd.to_numeric(df[vcol], errors="coerce")
    s.index = pd.to_datetime(df[dcol])
    return s.dropna().sort_index()


def to_monthly(s: pd.Series) -> pd.Series:
    if s is None or s.empty:
        return pd.Series(dtype=float)
    m = s.resample("ME").last().dropna()
    m.index = m.index.normalize()
    return m


def load_macro(adj_monthly: pd.DataFrame | None = None) -> dict:
    """回傳 {'baa': 月底利差, 'hyg_ief': 月底含息價格比}（缺資料者為空）。"""
    out = {"baa": to_monthly(fetch_fred("BAA10Y"))}
    if adj_monthly is not None and {"HYG", "IEF"} <= set(adj_monthly.columns):
        r = (adj_monthly["HYG"] / adj_monthly["IEF"]).dropna()
        out["hyg_ief"] = r
    else:
        out["hyg_ief"] = pd.Series(dtype=float)
    return out


def credit_signal(macro: dict, t, cfg: dict | None = None) -> dict:
    """在月底 t（含）之前的資料判斷信用狀態。cfg = risk.credit_overlay。"""
    cfg = cfg or {}
    rule = cfg.get("rule", "jump")
    res = {"rule": rule, "risk_off": False, "level": None, "chg3": None, "z": None,
           "ratio_vs_ma": None, "available": False}
    t = pd.Timestamp(t)
    baa = (macro or {}).get("baa", pd.Series(dtype=float))
    b = baa.loc[:t] if len(baa) else baa
    if len(b) >= 4:
        res["level"] = float(b.iloc[-1])
        res["chg3"] = float(b.iloc[-1] - b.iloc[-4])
        w = b.iloc[-36:]
        if len(w) >= 24 and w.std() > 0:
            res["z"] = float((b.iloc[-1] - w.mean()) / w.std())
    ratio = (macro or {}).get("hyg_ief", pd.Series(dtype=float))
    rr = ratio.loc[:t] if len(ratio) else ratio
    n = int(cfg.get("ma_months", 6))
    if len(rr) >= n:
        res["ratio_vs_ma"] = float(rr.iloc[-1] / rr.iloc[-n:].mean() - 1)
    if rule == "jump" and res["chg3"] is not None:
        res["available"] = True
        res["risk_off"] = res["chg3"] >= float(cfg.get("jump_pp", 0.5))
    elif rule == "z" and res["z"] is not None:
        res["available"] = True
        res["risk_off"] = res["z"] >= float(cfg.get("z_min", 1.0)) and (res["chg3"] or 0) > 0
    elif rule == "hyg_ma" and res["ratio_vs_ma"] is not None:
        res["available"] = True
        res["risk_off"] = res["ratio_vs_ma"] < 0
    return res


def describe(sig: dict) -> str:
    """給網站與通知看的一句話（不含原始利差數值）。"""
    parts = []
    if sig.get("chg3") is not None:
        d = sig["chg3"]
        parts.append(f"公司債利差近 3 個月{'擴大' if d > 0 else '收斂'} {abs(d):.2f} 個百分點")
    if sig.get("z") is not None:
        parts.append(f"相對近 3 年{'偏高' if sig['z'] > 0.5 else '偏低' if sig['z'] < -0.5 else '持平'}")
    if sig.get("ratio_vs_ma") is not None:
        parts.append(f"高收益債相對公債{'走弱' if sig['ratio_vs_ma'] < 0 else '走強'}")
    return "；".join(parts) or "資料不足"
