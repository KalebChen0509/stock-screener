"""從快取組出回測／每日篩選需要的資料。"""
from __future__ import annotations

import pandas as pd

from . import data_tw, data_us, prices
from .common import CACHE_DIR, log
from .metrics import fundamental_panel


def load_infos(include_delisted: bool = False, tickers: set | None = None) -> dict:
    """include_delisted：加入 2016 年後下市（櫃）的台股（回測用，避免倖存者偏差），並標記下市日、最後交易日。
    tickers：月股價裡有的代號（None＝讀快取）；台股主代號沒有股價但另一個字尾有時，改用另一個（改上市／上櫃）。"""
    infos = {}
    p = CACHE_DIR / "tw_universe.csv"
    if p.exists():
        u = pd.read_csv(p, dtype={"stock_id": str})
        if include_delisted:
            dl = data_tw.load_delisted()
            if len(dl):
                extra = dl[~dl["stock_id"].isin(u["stock_id"])]
                u = pd.concat([u, extra[[c for c in u.columns if c in extra]]], ignore_index=True)
                dmap = dict(zip(dl["stock_id"], pd.to_datetime(dl["delist_date"], errors="coerce")))
                lmap = dict(zip(dl["stock_id"], pd.to_datetime(dl["last_trade"], errors="coerce")))
                u["delist_date"] = u["stock_id"].map(dmap)
                u["last_trade"] = u["stock_id"].map(lmap)
        have = tickers if tickers is not None else prices.monthly_tickers()
        if have:
            alt = u["stock_id"] + u["yahoo"].astype(str).str.endswith(".TWO").map({True: ".TW", False: ".TWO"})
            swap = ~u["yahoo"].isin(have) & alt.isin(have)
            u.loc[swap, "yahoo"] = alt[swap]
        infos["tw"] = pd.DataFrame({
            "name": u["stock_name"].values, "industry": u["industry_category"].values,
            "is_financial": u["is_financial"].astype(str).str.lower().isin(["true", "1"]).values,
            "yahoo": u["yahoo"].values,
        }, index=u["stock_id"].values)
        if "delist_date" in u:
            infos["tw"]["delist_date"] = pd.to_datetime(u["delist_date"]).values
            infos["tw"]["last_trade"] = pd.to_datetime(u["last_trade"]).values
    p = CACHE_DIR / "us_universe.csv"
    if p.exists():
        u = pd.read_csv(p, dtype={"ticker": str, "sic": str})
        infos["us"] = pd.DataFrame({
            "name": u["name"].values, "industry": u["industry"].values,
            "is_financial": u["is_financial"].astype(bool).values, "yahoo": u["yahoo"].values,
            "sic": u["sic"].values if "sic" in u else None,
        }, index=u["ticker"].values)
    for k in infos:
        infos[k].index.name = "id"
    return infos


def load_panels(infos: dict, monthly: pd.DataFrame | None = None) -> tuple[dict, dict]:
    """回傳 (panels, raw_fundamentals)。"""
    if monthly is None and prices.MONTHLY_PATH.exists():
        monthly = prices.load_monthly()
    splits = prices.split_factor_table(monthly) if monthly is not None else pd.DataFrame()
    panels, raws = {}, {}
    for mkt, loader in (("tw", data_tw.load_fundamentals), ("us", data_us.load_fundamentals)):
        if mkt not in infos:
            continue
        f = loader()
        if f.empty:
            continue
        f = f[f["id"].isin(infos[mkt].index)]
        ymap = dict(zip(infos[mkt].index, infos[mkt]["yahoo"]))
        panels[mkt] = fundamental_panel(f, splits, ymap)
        raws[mkt] = f
        log.info("%s panel: %d rows, %d companies", mkt, len(panels[mkt]), panels[mkt]["id"].nunique())
    return panels, raws
