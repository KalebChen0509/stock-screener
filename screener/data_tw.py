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

# 註：FinMind 的「其他」是證交所「其他業」（巨大、美利達、寶成、豐泰等真正的公司），不是非股票證券，不排除
NON_STOCK_CATEGORIES = {"ETF", "ETN", "受益證券", "存託憑證", "Index", "大盤", "所有證券",
                        "創新版股票", "臺灣存託憑證", "指數投資證券(ETN)", "上櫃指數股票型基金(ETF)",
                        "上市指數股票型基金", "上櫃ETF", "上市ETF", "受益憑證"}
GENERIC_CATEGORIES = {"其他", "電子工業", "其他電子類"}   # 同一檔有多個產業別時，最後才用這幾個
FINANCIAL_CATEGORIES = {"金融保險業", "金融保險", "金融業", "證券業", "保險業", "銀行業"}


SCHEMA_VERSION = 2   # 標準欄位有變動時加 1，舊快取會自動重抓
MAX_FAIL = 3         # 同一筆資料連續抓失敗幾次才放棄（記為沒有資料）


class FinMindLimit(Exception):
    pass


class FinMind:
    def __init__(self, token: str | None = None, max_calls: int = 550, pause: float = 0.3):
        self.token = token or os.environ.get("FINMIND_TOKEN", "")
        self.max_calls = max_calls
        self.calls = 0
        self.pause = pause
        self.last_ok = True     # 最近一次請求是否成功取得回應（失敗 4 次 → False，資料要下次再抓，不能當成「沒有資料」）
        self.consec_fail = 0    # 連續失敗的請求數：太多代表資料來源或網路整個斷了，這一輪先停，不要把每檔都記一次失敗
        self.s = requests.Session()
        if self.token:
            self.s.headers["Authorization"] = f"Bearer {self.token}"

    def get(self, dataset: str, data_id: str | None = None, start: str = "2010-01-01",
            end: str | None = None) -> pd.DataFrame:
        if self.calls >= self.max_calls:
            raise FinMindLimit(f"reached per-run budget {self.max_calls}")
        self.last_ok = False
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
            try:
                js = r.json()
            except ValueError:
                log.warning("FinMind %s %s bad JSON", dataset, data_id)
                time.sleep(5 * (attempt + 1))
                continue
            time.sleep(self.pause)
            self.last_ok = True
            self.consec_fail = 0
            return pd.DataFrame(js.get("data", []))
        self.consec_fail += 1
        if self.consec_fail >= 5:
            raise FinMindLimit(f"FinMind unreachable ({self.consec_fail} requests failed in a row); stop this run and retry later")
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
    # 同一檔可能有多列（舊產業別、改上市／上櫃）：取最新日期的一列；同日期時優先用明確的產業別
    if "date" in df:
        df["_generic"] = df["industry_category"].isin(GENERIC_CATEGORIES)
        df = df.sort_values(["stock_id", "date", "_generic"], ascending=[True, False, True]).drop(columns="_generic")
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
                        start: str = "2011-01-01", recheck_days: int = 3,
                        frozen: set | None = None) -> int:
    """增量更新台股財報快取。回傳本次成功更新的公司數。
    frozen：已下市的公司，抓過一次（目前版本）就不再重查。"""
    ensure_dir(TW_FIN_DIR)
    meta = _load_meta()
    today = date.today()
    exp_q = expected_latest_quarter(today, lag)
    in_window_q = (pd.Timestamp(today) - pd.offsets.QuarterEnd(1)).normalize()
    frozen = frozen or set()

    todo = []
    for sid in universe["stock_id"]:
        m = meta.get(sid)
        if not m or m.get("v", 1) < SCHEMA_VERSION:
            todo.append((0, sid))
            continue
        if sid in frozen:
            # 下市公司抓過就不再查；但舊版可能把「抓失敗」記成沒有資料 → 沒有任何一季的再補試一次
            if m.get("last_period", "") == "1900-01-01" and not m.get("retried"):
                todo.append((3, sid))
            continue
        last_q = pd.Timestamp(m.get("last_period", "1900-01-01"))
        last_chk = datetime.fromisoformat(m.get("checked", "1900-01-01")).date()
        stale = (today - last_chk).days
        # 連續 2 次以上查不到應有的季報（下市、暫停交易、資料商缺漏）：改成每週才查一次，不要每天浪費額度
        wait = 1 if m.get("miss", 0) < 2 else 7
        if last_q < exp_q:
            if stale >= wait:
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
            ok = getattr(fm, "last_ok", True)
            bal = fm.get("TaiwanStockBalanceSheet", sid, s)
            ok = ok and getattr(fm, "last_ok", True)
            cf = fm.get("TaiwanStockCashFlowsStatement", sid, s)
            ok = ok and getattr(fm, "last_ok", True)
            prev = meta.get(sid) or {}
            if not ok and prev.get("fail", 0) + 1 < MAX_FAIL:
                # 網路或資料來源暫時有問題：不記為完成，下次再抓（連續失敗 MAX_FAIL 次才放棄）
                meta[sid] = {**prev, "fail": prev.get("fail", 0) + 1}
                log.warning("  %s: request failed, will retry next run", sid)
                continue
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
            prev_last = pd.Timestamp(prev.get("last_period", "1900-01-01"))
            miss = prev.get("miss", 0) + 1 if (pd.Timestamp(last) <= prev_last and pd.Timestamp(last) < exp_q) else 0
            meta[sid] = {"last_period": str(pd.Timestamp(last).date()),
                         "checked": today.isoformat(), "v": SCHEMA_VERSION, "miss": miss}
            if sid in frozen:
                meta[sid]["retried"] = True
            done += 1
            if done % 50 == 0:
                _save_meta(meta)
                log.info("  ... %d done (%d calls)", done, fm.calls)
    except FinMindLimit as e:
        log.warning("FinMind budget/limit reached: %s", e)
    _save_meta(meta)
    log.info("TW fundamentals updated: %d stocks, %d API calls", done, fm.calls)
    return done


# ----------------------------------------------------------------------------
#  下市（櫃）公司：回測要包含當時存在、之後下市的公司，否則會有倖存者偏差（只看到活下來的公司）
# ----------------------------------------------------------------------------
DELISTED_PATH = CACHE_DIR / "tw_delisted.csv"
DELISTED_PX_PATH = CACHE_DIR / "tw_delisted_prices.csv.gz"      # 月資料，欄位同 prices_monthly
DELISTED_DAILY_PATH = CACHE_DIR / "tw_delisted_daily.csv.gz"    # 日收盤（回測停損用），欄位同 daily_bt
DELISTED_COLS = ["stock_id", "stock_name", "industry_category", "type", "is_financial", "yahoo",
                 "delist_date", "last_trade", "px_done", "px_fail", "reused"]
REUSE_DAYS = 60     # 下市日之後超過 60 天還有成交 → 代號已被新公司沿用（或重新上市），不是同一家


def load_delisted(keep_reused: bool = False) -> pd.DataFrame:
    """keep_reused：保留「代號被沿用」的列（只有 update_delisted 自己需要，避免重抓）。"""
    if not DELISTED_PATH.exists():
        return pd.DataFrame(columns=DELISTED_COLS)
    d = pd.read_csv(DELISTED_PATH, dtype={"stock_id": str})
    d["reused"] = d["reused"].astype(str).str.lower().isin(["true", "1"]) if "reused" in d else False
    if not keep_reused:
        d = d[~d["reused"]].copy()
    d["is_financial"] = d["is_financial"].astype(str).str.lower().isin(["true", "1"])
    d["px_done"] = d["px_done"].astype(str).str.lower().isin(["true", "1"])
    if "px_fail" in d:
        d["px_fail"] = pd.to_numeric(d["px_fail"], errors="coerce").fillna(0).astype(int)
    else:
        # 舊版檔案（抓失敗也記為完成）：沒有股價的再補試一次
        d["px_fail"] = 0
        redo = d["px_done"] & d["last_trade"].isna()
        d.loc[redo, "px_done"] = False
        d.loc[redo, "px_fail"] = MAX_FAIL - 1
    return d


def _price_frames(raw: pd.DataFrame, tk: str) -> tuple[pd.DataFrame, pd.DataFrame, str | None]:
    """FinMind 日股價 → (月資料, 日資料, 最後交易日)。下市股沒有還原股價，以收盤價代替（不含股利，偏保守）。"""
    if raw is None or raw.empty or "close" not in raw:
        return pd.DataFrame(), pd.DataFrame(), None
    d = pd.DataFrame({"date": pd.to_datetime(raw["date"]), "close": pd.to_numeric(raw["close"], errors="coerce"),
                      "volume": pd.to_numeric(raw.get("Trading_Volume"), errors="coerce")})
    d = d[(d["close"] > 0) & (d["volume"].fillna(0) > 0)].sort_values("date")
    if d.empty:
        return pd.DataFrame(), pd.DataFrame(), None
    d["month"] = (d["date"] + pd.offsets.MonthEnd(0)).dt.normalize()
    g = d.groupby("month")
    m = pd.DataFrame({"ticker": tk, "close": g["close"].last(), "volume": g["volume"].sum()}).reset_index()
    m["adj_close"], m["split"] = m["close"], 0.0
    dd = d[d["date"] >= "2015-06-01"]
    daily = pd.DataFrame({"ticker": tk, "date": dd["date"].dt.normalize(), "adj_close": dd["close"].round(4)})
    return m[["ticker", "close", "adj_close", "volume", "split", "month"]], daily, str(d["date"].max().date())


def update_delisted(fm: FinMind, info: pd.DataFrame, since: str = "2016-01-01") -> pd.DataFrame:
    """下市（櫃）普通股清單＋股價（每檔只抓一次）。回傳清單（DELISTED_COLS）。
    財報與月營收由 update_fundamentals／month_rev.update 一併回填（傳入 frozen，抓過就不再重查）。"""
    old = load_delisted(keep_reused=True)
    try:
        dl = fm.get("TaiwanStockDelisting", start="2000-01-01")
    except FinMindLimit:
        return old[~old["reused"]] if len(old) else old
    if dl.empty:
        return old[~old["reused"]] if len(old) else old
    dl["stock_id"] = dl["stock_id"].astype(str)
    dl = dl[dl["stock_id"].str.fullmatch(r"[1-9]\d{3}") & (dl["date"].astype(str) >= since)]
    dl = dl.sort_values("date").drop_duplicates("stock_id", keep="last")
    inf = info.copy()
    inf["stock_id"] = inf["stock_id"].astype(str)
    inf = inf.drop_duplicates("stock_id", keep="first").set_index("stock_id")
    prev = old.set_index("stock_id") if len(old) else pd.DataFrame(columns=DELISTED_COLS[1:])
    rows = []
    for _, r in dl.iterrows():
        sid, dd = r["stock_id"], str(r["date"])[:10]
        ind = inf["industry_category"].get(sid) if sid in inf.index else None
        typ = inf["type"].get(sid) if sid in inf.index else None
        if ind in NON_STOCK_CATEGORIES:
            continue
        p = prev.loc[sid] if sid in prev.index else None
        rows.append({"stock_id": sid, "stock_name": r.get("stock_name", ""),
                     "industry_category": ind if isinstance(ind, str) else "未分類",
                     "type": typ if isinstance(typ, str) else "twse",
                     "is_financial": ind in FINANCIAL_CATEGORIES,
                     "yahoo": sid + (".TWO" if typ == "tpex" else ".TW"), "delist_date": dd,
                     "last_trade": p["last_trade"] if p is not None else None,
                     "px_done": bool(p["px_done"]) if p is not None else False,
                     "px_fail": int(p["px_fail"]) if p is not None else 0,
                     "reused": bool(p["reused"]) if p is not None else False})
    out = pd.DataFrame(rows, columns=DELISTED_COLS)
    out["last_trade"] = out["last_trade"].astype(object).where(out["last_trade"].notna(), None)
    todo = out[~out["px_done"].astype(bool)]
    mon = pd.read_csv(DELISTED_PX_PATH, parse_dates=["month"]) if DELISTED_PX_PATH.exists() else pd.DataFrame()
    day = pd.read_csv(DELISTED_DAILY_PATH, parse_dates=["date"]) if DELISTED_DAILY_PATH.exists() else pd.DataFrame()
    new_m, new_d = [], []
    done = 0
    try:
        for i, r in todo.iterrows():
            raw = fm.get("TaiwanStockPrice", r["stock_id"], "2011-01-01")
            if not getattr(fm, "last_ok", True) and int(r["px_fail"]) + 1 < MAX_FAIL:
                out.at[i, "px_fail"] = int(r["px_fail"]) + 1       # 抓失敗：下次再試
                continue
            m, d, last = _price_frames(raw, r["yahoo"])
            reused = last is not None and pd.Timestamp(last) > pd.Timestamp(r["delist_date"]) + pd.Timedelta(days=REUSE_DAYS)
            if len(m) and not reused:
                new_m.append(m)
                new_d.append(d)
            out.at[i, "reused"] = bool(reused)
            out.at[i, "last_trade"] = last
            out.at[i, "px_done"] = True
            done += 1
    except FinMindLimit as e:
        log.warning("FinMind limit during delisted prices: %s", e)
    if new_m:
        got = {f["ticker"].iloc[0] for f in new_m}
        mon = pd.concat([mon[~mon["ticker"].isin(got)] if len(mon) else mon] + new_m, ignore_index=True)
        day = pd.concat([day[~day["ticker"].isin(got)] if len(day) else day] + new_d, ignore_index=True)
        mon.to_csv(DELISTED_PX_PATH, index=False, compression="gzip")
        day.to_csv(DELISTED_DAILY_PATH, index=False, compression="gzip")
    ensure_dir(DELISTED_PATH.parent)
    out.to_csv(DELISTED_PATH, index=False)
    rz = out["reused"].astype(bool)
    log.info("TW delisted since %s: %d stocks (prices fetched now %d, with prices %d, waiting for retry %d, code reused %d)",
             since, int((~rz).sum()), done, int(out.loc[~rz, "last_trade"].notna().sum()),
             int((~out["px_done"].astype(bool)).sum()), int(rz.sum()))
    return out[~rz].copy()


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
