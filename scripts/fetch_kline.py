"""更新 K 線資料（近 5 年日線＋2011 年起月線）並輸出網站「看盤」用的檔案。

第一次會分幾天回填（每次最多 KLINE_BACKFILL 檔，預設 1500），持股、追蹤清單、ETF 優先。
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd  # noqa: E402

from screener import kline, prices  # noqa: E402
from screener.common import CACHE_DIR, SITE_DATA, load_etf_universe, load_watchlist, log  # noqa: E402
from screener.loaders import load_infos  # noqa: E402


def ticker_map() -> dict:
    """Yahoo 代號 → (市場, 網站代號)：台股、有財報的美股、ETF 清單。"""
    have = prices.monthly_tickers() if prices.MONTHLY_PATH.exists() else None
    infos = load_infos(tickers=have)
    ymap = {}
    if "tw" in infos:
        for sid, y in zip(infos["tw"].index, infos["tw"]["yahoo"]):
            if isinstance(y, str) and y:
                ymap[y] = ("tw", str(sid))
    if "us" in infos:
        f = CACHE_DIR / "us_fundamentals.csv.gz"
        ids = set(pd.read_csv(f, usecols=["id"], dtype=str)["id"]) if f.exists() else set(infos["us"].index)
        for sid, y in zip(infos["us"].index, infos["us"]["yahoo"]):
            if sid in ids and isinstance(y, str) and y:
                ymap[y] = ("us", str(sid))
    for e in load_etf_universe():
        ymap[e["ticker"]] = ("etf", e["ticker"])
    for t in ("0050.TW", "SPY", "VT"):
        ymap.setdefault(t, ("etf", t))
    return ymap


def priority(ymap: dict) -> list[str]:
    """先回填你會看的：持股、追蹤清單、模擬持股、財報快報追蹤的公司、ETF，其餘依成交值。"""
    back = {(m, i): y for y, (m, i) in ymap.items()}
    first = [t for t, (m, _) in ymap.items() if m == "etf"]
    try:
        st = json.loads((CACHE_DIR / "notify_state.json").read_text())
        first += [back.get((m, i)) for m, ids in (st.get("held") or {}).items() for i in ids]
    except Exception:  # noqa
        pass
    wl = load_watchlist()
    first += [back.get((m, str(i))) for m in ("tw", "us") for i in (wl.get(m) or [])]
    try:
        pl = json.loads((CACHE_DIR / "paper_log.json").read_text())
        first += [x.get("ticker") for v in pl.values() for mth in v.values() for x in mth.get("weights", [])]
    except Exception:  # noqa
        pass
    try:
        ew = json.loads((CACHE_DIR / "earnings_watch.json").read_text())
        first += [back.get((m, str(i))) for m in ("tw", "us") for i in (ew.get(m) or [])]
    except Exception:  # noqa
        pass
    rest = []
    if prices.MONTHLY_PATH.exists():
        m = prices.load_monthly()
        recent = m[m["month"] >= m["month"].max() - pd.DateOffset(months=3)]
        tv = (recent["close"] * recent["volume"]).groupby(recent["ticker"]).mean().sort_values(ascending=False)
        rest = [t for t in tv.index if t in ymap]
    return [t for t in dict.fromkeys(first + rest) if t and t in ymap]


def main():
    ymap = ticker_map()
    pri = priority(ymap)
    tickers = pri + [t for t in ymap if t not in set(pri)]
    log.info("kline tickers: %d (priority %d)", len(tickers), len(pri))
    try:
        kline.update(tickers, priority=pri, backfill_limit=int(os.environ.get("KLINE_BACKFILL", 1500)))
    except Exception as e:  # noqa
        log.warning("kline update failed: %s", e)
    extra = {}
    try:   # 台股籌碼（法人買賣超、融資融券）放進同一個 K 線檔，給「法人」副圖用
        from screener import chips
        extra = {("tw", sid): {"chip": v} for sid, v in chips.series().items()}
    except Exception as e:  # noqa
        log.warning("chips series failed: %s", e)
    kline.export_site(SITE_DATA / "kline", ymap, extra=extra)


if __name__ == "__main__":
    main()
