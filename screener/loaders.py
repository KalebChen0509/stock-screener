"""從快取組出回測／每日篩選需要的資料。"""
from __future__ import annotations

import pandas as pd

from . import data_tw, data_us, prices
from .common import CACHE_DIR, log
from .metrics import fundamental_panel


def load_infos() -> dict:
    infos = {}
    p = CACHE_DIR / "tw_universe.csv"
    if p.exists():
        u = pd.read_csv(p, dtype={"stock_id": str})
        infos["tw"] = pd.DataFrame({
            "name": u["stock_name"].values, "industry": u["industry_category"].values,
            "is_financial": u["is_financial"].astype(bool).values, "yahoo": u["yahoo"].values,
        }, index=u["stock_id"].values)
    p = CACHE_DIR / "us_universe.csv"
    if p.exists():
        u = pd.read_csv(p, dtype={"ticker": str, "sic": str})
        infos["us"] = pd.DataFrame({
            "name": u["name"].values, "industry": u["industry"].values,
            "is_financial": u["is_financial"].astype(bool).values, "yahoo": u["yahoo"].values,
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
