"""台股資料：FinMind API（股票清單、三大財報）。

財報以 FinMind 長格式（date, stock_id, type, value）抓下後，轉成 schema.py 的標準欄位，
並快取在 data/cache/tw_fin/<stock_id>.csv.gz。
免費註冊會員每小時約 600 次請求，所以採「增量 + 每次執行有請求上限」的方式慢慢回填。
"""
from __future__ import annotations

import json
import os
import time
from datetime import date, datetime

import numpy as np
import pandas as pd
import requests

from .common import CACHE_DIR, ensure_dir, log
from .schema import ALL_FIELDS, FLOW_FIELDS

API = "https://api.finmindtrade.com/api/v4/data"
TW_FIN_DIR = CACHE_DIR / "tw_fin"
META_PATH = CACHE_DIR / "tw_fin_meta.json"
INFO_PATH = CACHE_DIR / "tw_stock_info.csv"

# 每個標準欄位對應的 FinMind type 候選名稱。"sum" 代表把有出現的全部加總。
INCOME_MAP = {
    "revenue": ["Revenue", "OperatingRevenue", "TotalOperatingRevenue"],
    "cogs": ["CostOfGoodsSold", "OperatingCosts", "CostOfRevenue"],
    "gross_profit": ["GrossProfit", "GrossProfitLossFromOperations"],
    "op_income": ["OperatingIncome", "NetOperatingIncomeLoss", "OperatingIncomeLoss"],
    "nonop": ["TotalNonoperatingIncomeAndExpense", "NonoperatingIncomeAndExpenses"],
    "pretax": ["PreTaxIncome", "ProfitLossBeforeTax", "IncomeBeforeIncomeTax"],
    "rd": ["ResearchAndDevelopmentExpenses", "ResearchAndDevelopmentExpense",
           "ResearchAndDevelopmentExpenditure"],
    "net_income": ["IncomeAfterTaxes", "TotalConsolidatedProfitForThePeriod",
                   "ProfitLoss", "IncomeFromContinuingOperations",
                   "EquityAttributableToOwnersOfParent"],
}
BALANCE_MAP = {
    "cash": ["CashAndCashEquivalents"],
    "st_inv": ("sum", ["CurrentFinancialAssetsAtFairvalueThroughProfitOrLoss",
                       "CurrentFinancialAssetsAtFairValueThroughProfitOrLoss",
                       "CurrentFinancialAssetsAtFairValueThroughOtherComprehensiveIncome",
                       "CurrentFinancialAssetsAtAmortizedCost",
                       "CurrentFinancialAssetsAtAmortisedCost",
                       # FinMind 實際使用的名稱（無 NonCurrent 字尾者為流動）
                       "FinancialAssetsAtFairvalueThroughOtherComprehensiveIncome",
                       "FinancialAssetsAtAmortizedCost"]),
    "receivables": ("sum", ["AccountsReceivableNet", "NotesReceivableNet",
                            "AccountsReceivableDuefromRelatedPartiesNet"]),
    "inventory": ["Inventories", "Inventory"],
    "current_assets": ["CurrentAssets", "TotalCurrentAssets"],
    "total_assets": ["TotalAssets"],
    "current_liab": ["CurrentLiabilities", "TotalCurrentLiabilities"],
    "total_liab": ["Liabilities", "TotalLiabilities"],
    "equity": ["Equity", "TotalEquity", "EquityAttributableToOwnersOfParent"],
    "goodwill": ["Goodwill"],
    "intangibles": ["IntangibleAssets"],
    "lt_inv": ("sum", ["InvestmentAccountedForUsingEquityMethod",
                       "NoncurrentFinancialAssetsAtFairValueThroughOtherComprehensiveIncome",
                       "NoncurrentFinancialAssetsAtFairvalueThroughProfitOrLoss",
                       "NoncurrentFinancialAssetsAtFairValueThroughProfitOrLoss",
                       "NonCurrentFinancialAssetsAtFairvalueThroughProfitOrLoss",
                       "FinancialAssetsAtFairvalueThroughOtherComprehensiveIncomeNonCurrent"]),
    "debt": ("sum", ["ShorttermBorrowings", "ShortTermBorrowings",
                     "ShorttermNotesAndBillsPayable", "ShortTermNotesAndBillsPayable",
                     "LongtermBorrowingsCurrentPortion", "CurrentPortionOfLongtermBorrowings",
                     "CurrentPortionOfBondsPayable", "BondsPayable",
                     "LongtermBorrowings", "LongTermBorrowings"]),
    "share_capital": ["OrdinaryShare", "CommonStock", "CapitalStock",
                      "OrdinaryShareCapital", "ShareCapital"],
}
CASHFLOW_MAP = {
    "cfo": ["CashFlowsFromOperatingActivities", "NetCashInflowFromOperatingActivities",
            "NetCashFlowsFromUsedInOperatingActivities", "CashGeneratedFromOperations"],
    "capex": ["PropertyAndPlantAndEquipment", "AcquisitionOfPropertyPlantAndEquipment",
              "AcquisitionOfPropertyPlantAndEquipments"],
    "cfi": ["CashProvidedByInvestingActivities", "CashFlowsFromUsedInInvestingActivities",
            "NetCashFlowsFromUsedInInvestingActivities", "NetCashInflowOutflowFromInvestingActivities"],
    "cff": ["CashFlowsProvidedFromFinancingActivities", "NetCashFlowsFromUsedInFinancingActivities",
            "CashProvidedByFinancingActivities", "NetCashInflowOutflowFromFinancingActivities"],
}

NON_STOCK_CATEGORIES = {"ETF", "ETN", "受益證券", "存託憑證", "Index", "大盤", "所有證券",
                        "創新版股票", "臺灣存託憑證", "指數投資證券(ETN)", "上櫃指數股票型基金(ETF)",
                        "上市指數股票型基金", "上櫃ETF", "上市ETF", "受益憑證", "其他"}
FINANCIAL_CATEGORIES = {"金融保險業", "金融保險", "金融業", "證券業", "保險業", "銀行業"}


SCHEMA_VERSION = 2   # 標準欄位有變動時加 1，舊快取會自動重抓


class FinMindLimit(Exception):
    pass


class FinMind:
    def __init__(self, token: str | None = None, max_calls: int = 550, pause: float = 0.3):
        self.token = token or os.environ.get("FINMIND_TOKEN", "")
        self.max_calls = max_calls
        self.calls = 0
        self.pause = pause
        self.s = requests.Session()
        if self.token:
            self.s.headers["Authorization"] = f"Bearer {self.token}"

    def get(self, dataset: str, data_id: str | None = None, start: str = "2010-01-01",
            end: str | None = None) -> pd.DataFrame:
        if self.calls >= self.max_calls:
            raise FinMindLimit(f"reached per-run budget {self.max_calls}")
        params = {"dataset": dataset, "start_date": start}
        if data_id:
            params["data_id"] = data_id
        if end:
            params["end_date"] = end
        for attempt in range(4):
            try:
                r = self.s.get(API, params=params, timeout=60)
            except requests.RequestException as e:
                log.warning("FinMind %s %s error %s", dataset, data_id, e)
                time.sleep(5 * (attempt + 1))
                continue
            self.calls += 1
            if r.status_code == 402 or "upper limit" in r.text[:300]:
                raise FinMindLimit(r.text[:200])
            if r.status_code != 200:
                log.warning("FinMind %s %s HTTP %s %s", dataset, data_id, r.status_code, r.text[:200])
                time.sleep(5 * (attempt + 1))
                continue
            js = r.json()
            time.sleep(self.pause)
            return pd.DataFrame(js.get("data", []))
        return pd.DataFrame()


# ----------------------------------------------------------------------------
def fetch_stock_info(fm: FinMind) -> pd.DataFrame:
    """上市櫃普通股清單（含產業別）。"""
    df = fm.get("TaiwanStockInfo", start="2000-01-01")
    if df.empty:
        if INFO_PATH.exists():
            return pd.read_csv(INFO_PATH, dtype={"stock_id": str})
        raise RuntimeError("TaiwanStockInfo empty")
    df["stock_id"] = df["stock_id"].astype(str)
    df = df.drop_duplicates("stock_id", keep="first")
    df.to_csv(ensure_dir(INFO_PATH.parent) / INFO_PATH.name, index=False)
    return df


def common_stock_universe(info: pd.DataFrame) -> pd.DataFrame:
    df = info.copy()
    df = df[df["type"].isin(["twse", "tpex"])]
    df = df[df["stock_id"].str.fullmatch(r"[1-9]\d{3}")]
    df = df[~df["industry_category"].isin(NON_STOCK_CATEGORIES)]
    df["is_financial"] = df["industry_category"].isin(FINANCIAL_CATEGORIES)
    df["yahoo"] = df["stock_id"] + np.where(df["type"] == "twse", ".TW", ".TWO")
    return df[["stock_id", "stock_name", "industry_category", "type", "is_financial", "yahoo"]]


# ----------------------------------------------------------------------------
def _pick(wide: pd.DataFrame, spec) -> pd.Series:
    if isinstance(spec, tuple):
        cols = [c for c in spec[1] if c in wide.columns]
        if not cols:
            return pd.Series(np.nan, index=wide.index)
        return wide[cols].sum(axis=1, min_count=1)
    for c in spec:
        if c in wide.columns and wide[c].notna().any():
            return wide[c]
    return pd.Series(np.nan, index=wide.index)


def _wide(raw: pd.DataFrame) -> pd.DataFrame:
    if raw.empty:
        return pd.DataFrame()
    raw = raw[~raw["type"].astype(str).str.endswith("_per")]
    w = raw.pivot_table(index="date", columns="type", values="value", aggfunc="last")
    w.index = pd.to_datetime(w.index)
    return w.sort_index()


def _is_cumulative(s: pd.Series) -> bool:
    """判斷季資料是否為年初至今累計值：累計時 Q2 約為 Q1 的 2 倍。"""
    s = s.dropna().abs()
    if s.empty:
        return False
    q = s.index.quarter
    s1, s2 = s[q == 1], s[q == 2]
    years = sorted(set(s1.index.year) & set(s2.index.year))
    if len(years) < 2:
        return False
    a = sum(s1[s1.index.year == y].iloc[0] for y in years)
    b = sum(s2[s2.index.year == y].iloc[0] for y in years)
    return a > 0 and b / a > 1.5


def _decumulate(df: pd.DataFrame, cols: list[str]) -> pd.DataFrame:
    out = df.copy()
    for col in cols:
        s = df[col]
        res = pd.Series(np.nan, index=df.index)
        for idx in df.index:
            if idx.quarter == 1:
                res[idx] = s[idx]
            else:
                prev = idx - pd.offsets.QuarterEnd(1)
                if prev in s.index and pd.notna(s[prev]) and pd.notna(s[idx]):
                    res[idx] = s[idx] - s[prev]
        out[col] = res
    return out


def tw_available_date(period_end: pd.Timestamp, lag: dict) -> pd.Timestamp:
    q = period_end.quarter
    md = lag[f"Q{q}"]
    y = period_end.year + (1 if q == 4 else 0)
    return pd.Timestamp(f"{y}-{md}")


def standardize(inc: pd.DataFrame, bal: pd.DataFrame, cf: pd.DataFrame, lag: dict,
                stock_id: str) -> pd.DataFrame:
    wi, wb, wc = _wide(inc), _wide(bal), _wide(cf)
    idx = wi.index.union(wb.index).union(wc.index)
    if len(idx) == 0:
        return pd.DataFrame()
    wi, wb, wc = wi.reindex(idx), wb.reindex(idx), wc.reindex(idx)
    out = pd.DataFrame(index=idx)
    for k, spec in INCOME_MAP.items():
        out[k] = _pick(wi, spec)
    out["gross_profit"] = out["gross_profit"].fillna(out["revenue"] - out["cogs"])
    for k, spec in CASHFLOW_MAP.items():
        out[k] = _pick(wc, spec)
    out["capex"] = out["capex"].abs()
    for k, spec in BALANCE_MAP.items():
        out[k] = _pick(wb, spec)

    inc_cols = ["revenue", "gross_profit", "op_income", "nonop", "pretax", "net_income", "rd"]
    if _is_cumulative(out["revenue"]):
        out = _decumulate(out, inc_cols)
    cf_cols = ["cfo", "capex", "cfi", "cff"]
    if _is_cumulative(out["cfo"].abs() + out["capex"].fillna(0)):
        out = _decumulate(out, cf_cols)
    out["nonop"] = out["nonop"].fillna(out["pretax"] - out["op_income"])
    # 台股普通股面額多為 10 元
    out["shares"] = out["share_capital"] / 10.0
    out = out.drop(columns=["cogs", "share_capital"])
    out.index.name = "period_end"
    out = out.reset_index()
    out = out[out["period_end"].dt.is_quarter_end]
    out["available_date"] = out["period_end"].apply(lambda d: tw_available_date(d, lag))
    out["market"] = "tw"
    out["id"] = stock_id
    return out[["market", "id", "period_end", "available_date"] + ALL_FIELDS]


# ----------------------------------------------------------------------------
def _load_meta() -> dict:
    if META_PATH.exists():
        return json.loads(META_PATH.read_text())
    return {}


def _save_meta(meta: dict) -> None:
    ensure_dir(META_PATH.parent)
    META_PATH.write_text(json.dumps(meta, ensure_ascii=False))


def expected_latest_quarter(today: date, lag: dict) -> pd.Timestamp:
    """今天之前「應該已經公布」的最新一季（以法定期限計）。"""
    t = pd.Timestamp(today)
    qe = (t - pd.offsets.QuarterEnd(1)).normalize()
    for _ in range(6):
        if tw_available_date(qe, lag) <= t:
            return qe
        qe = (qe - pd.offsets.QuarterEnd(1)).normalize()
    return qe


def _liquidity_rank(universe: pd.DataFrame) -> dict:
    """依近 3 個月成交值排序（需要已有價格快取；沒有就用代號順序）。"""
    p = CACHE_DIR / "prices_monthly.csv.gz"
    if not p.exists():
        return {}
    try:
        m = pd.read_csv(p, usecols=["ticker", "month", "close", "volume"], parse_dates=["month"])
        m = m[m["month"] >= m["month"].max() - pd.DateOffset(months=3)]
        tv = (m["close"] * m["volume"]).groupby(m["ticker"]).mean()
        y2id = dict(zip(universe["yahoo"], universe["stock_id"]))
        ranked = tv.sort_values(ascending=False)
        return {y2id[t]: i for i, t in enumerate(ranked.index) if t in y2id}
    except Exception:  # noqa
        return {}


def update_fundamentals(universe: pd.DataFrame, lag: dict, fm: FinMind,
                        start: str = "2011-01-01", recheck_days: int = 3) -> int:
    """增量更新台股財報快取。回傳本次成功更新的公司數。"""
    ensure_dir(TW_FIN_DIR)
    meta = _load_meta()
    today = date.today()
    exp_q = expected_latest_quarter(today, lag)
    in_window_q = (pd.Timestamp(today) - pd.offsets.QuarterEnd(1)).normalize()

    todo = []
    for sid in universe["stock_id"]:
        m = meta.get(sid)
        if not m or m.get("v", 1) < SCHEMA_VERSION:
            todo.append((0, sid))
            continue
        last_q = pd.Timestamp(m.get("last_period", "1900-01-01"))
        last_chk = datetime.fromisoformat(m.get("checked", "1900-01-01")).date()
        stale = (today - last_chk).days
        if last_q < exp_q and stale >= 1:
            todo.append((1, sid))
        elif last_q < in_window_q and stale >= recheck_days:
            todo.append((2, sid))
        elif stale >= 120:  # 偶爾全面刷新，抓到更正後數字
            todo.append((3, sid))
    # 同一優先級內，成交值大的先抓（網站上常用的股票先完成）
    liq = _liquidity_rank(universe)
    todo.sort(key=lambda x: (x[0], liq.get(x[1], 10**9)))
    log.info("TW fundamentals: %d stocks need update (expected latest %s)", len(todo), exp_q.date())

    done = 0
    try:
        for _, sid in todo:
            path = TW_FIN_DIR / f"{sid}.csv.gz"
            old = pd.read_csv(path, parse_dates=["period_end", "available_date"],
                              dtype={"id": str}) if path.exists() else pd.DataFrame()
            s = start
            if not old.empty and "rd" in old.columns:
                s = f"{old['period_end'].max().year - 1}-01-01"
            inc = fm.get("TaiwanStockFinancialStatements", sid, s)
            bal = fm.get("TaiwanStockBalanceSheet", sid, s)
            cf = fm.get("TaiwanStockCashFlowsStatement", sid, s)
            new = standardize(inc, bal, cf, lag, sid)
            if not new.empty:
                if not old.empty:
                    # 新抓的年度的第一季若缺前期資料，保留舊值
                    keep = old[~old["period_end"].isin(new["period_end"])]
                    new = pd.concat([keep, new]).sort_values("period_end")
                new.to_csv(path, index=False, compression="gzip")
                last = new["period_end"].max()
            else:
                last = old["period_end"].max() if not old.empty else pd.Timestamp("1900-01-01")
            meta[sid] = {"last_period": str(pd.Timestamp(last).date()),
                         "checked": today.isoformat(), "v": SCHEMA_VERSION}
            done += 1
            if done % 50 == 0:
                _save_meta(meta)
                log.info("  ... %d done (%d calls)", done, fm.calls)
    except FinMindLimit as e:
        log.warning("FinMind budget/limit reached: %s", e)
    _save_meta(meta)
    log.info("TW fundamentals updated: %d stocks, %d API calls", done, fm.calls)
    return done


def load_fundamentals() -> pd.DataFrame:
    frames = []
    for p in sorted(TW_FIN_DIR.glob("*.csv.gz")):
        try:
            frames.append(pd.read_csv(p, parse_dates=["period_end", "available_date"],
                                      dtype={"id": str}))
        except Exception as e:  # noqa
            log.warning("bad cache %s: %s", p, e)
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


def backfill_progress(universe: pd.DataFrame) -> dict:
    meta = _load_meta()
    have = sum(1 for s in universe["stock_id"] if s in meta and meta[s].get("v", 1) >= SCHEMA_VERSION)
    return {"have": have, "total": int(len(universe))}
