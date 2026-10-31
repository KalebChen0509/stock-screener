"""美股資料：SEC EDGAR XBRL 大量下載檔（免費、官方、含申報日期可做 point-in-time）。

- company_tickers_exchange.json：代號、交易所
- submissions.zip：SIC 產業代碼
- companyfacts.zip：所有公司的 XBRL 財報數值
10-Q 的現金流量表是年初至今累計值，這裡會換算成單季值；Q4 = 全年 − 前三季累計。
"""
from __future__ import annotations

import json
import os
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd
import requests

from .common import CACHE_DIR, ensure_dir, log
from .schema import ALL_FIELDS, EXTRA_FLOW_FIELDS

UA = os.environ.get("SEC_USER_AGENT", "stock-screener research contact@example.com")
TICKERS_URL = "https://www.sec.gov/files/company_tickers_exchange.json"
FACTS_URL = "https://www.sec.gov/Archives/edgar/daily-index/xbrl/companyfacts.zip"
SUBS_URL = "https://www.sec.gov/Archives/edgar/daily-index/bulkdata/submissions.zip"
RAW_DIR = Path(os.environ.get("SEC_RAW_DIR", CACHE_DIR.parent / "sec_raw"))
US_FUND_PATH = CACHE_DIR / "us_fundamentals.csv.gz"
US_UNIVERSE_PATH = CACHE_DIR / "us_universe.csv"
US_EVENTS_PATH = CACHE_DIR / "us_events.csv.gz"

FORMS = {"10-Q", "10-K", "10-Q/A", "10-K/A", "10-KT"}

FLOW_CONCEPTS = {
    "revenue": ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax",
                "RevenueFromContractWithCustomerIncludingAssessedTax", "SalesRevenueNet",
                "SalesRevenueGoodsNet", "SalesRevenueServicesNet", "RevenuesNetOfInterestExpense"],
    "cogs": ["CostOfRevenue", "CostOfGoodsAndServicesSold", "CostOfGoodsSold", "CostOfServices",
             "CostOfGoodsAndServiceExcludingDepreciationDepletionAndAmortization"],
    "gross_profit": ["GrossProfit"],
    "op_income": ["OperatingIncomeLoss"],
    "nonop": ["NonoperatingIncomeExpense", "OtherNonoperatingIncomeExpense"],
    "pretax": ["IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
               "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments",
               "IncomeLossFromContinuingOperationsBeforeIncomeTaxesForeign"],
    "rd": ["ResearchAndDevelopmentExpense",
           "ResearchAndDevelopmentExpenseExcludingAcquiredInProcessCost"],
    "net_income": ["NetIncomeLoss", "ProfitLoss", "NetIncomeLossAvailableToCommonStockholdersBasic"],
    "cfo": ["NetCashProvidedByUsedInOperatingActivities",
            "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations"],
    "capex": ["PaymentsToAcquirePropertyPlantAndEquipment", "PaymentsToAcquireProductiveAssets",
              "PaymentsForCapitalImprovements"],
    "cfi": ["NetCashProvidedByUsedInInvestingActivities",
            "NetCashProvidedByUsedInInvestingActivitiesContinuingOperations"],
    "cff": ["NetCashProvidedByUsedInFinancingActivities",
            "NetCashProvidedByUsedInFinancingActivitiesContinuingOperations"],
    # 額外欄位（EXTRA_FLOW_FIELDS）：股票酬勞、利息、折舊攤銷、現金股利、買回庫藏股
    "sbc": ["ShareBasedCompensation", "AllocatedShareBasedCompensationExpense"],
    "interest": ["InterestExpense", "InterestExpenseNonoperating", "InterestExpenseDebt", "InterestAndDebtExpense"],
    "da": ["DepreciationDepletionAndAmortization", "DepreciationAndAmortization",
           "DepreciationAmortizationAndAccretionNet", "Depreciation"],
    "div_paid": ["PaymentsOfDividendsCommonStock", "PaymentsOfDividends"],
    "buyback": ["PaymentsForRepurchaseOfCommonStock"],
}
INSTANT_CONCEPTS = {
    "cash": ["CashAndCashEquivalentsAtCarryingValue",
             "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents", "Cash"],
    "st_inv": ["ShortTermInvestments", "MarketableSecuritiesCurrent",
               "AvailableForSaleSecuritiesDebtSecuritiesCurrent", "AvailableForSaleSecuritiesCurrent"],
    "receivables": ["AccountsReceivableNetCurrent", "ReceivablesNetCurrent"],
    "inventory": ["InventoryNet", "InventoryGross"],
    "current_assets": ["AssetsCurrent"],
    "total_assets": ["Assets"],
    "current_liab": ["LiabilitiesCurrent"],
    "total_liab": ["Liabilities"],
    "liab_and_equity": ["LiabilitiesAndStockholdersEquity"],
    "equity": ["StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
               "StockholdersEquity"],
    "goodwill": ["Goodwill"],
    "intangibles": ["IntangibleAssetsNetExcludingGoodwill", "FiniteLivedIntangibleAssetsNet"],
    "eq_method": ["EquityMethodInvestments"],
    "lt_investments": ["LongTermInvestments"],
    "debt_st": ["ShortTermBorrowings", "CommercialPaper"],
    "debt_cur": ["LongTermDebtCurrent", "DebtCurrent"],
    "debt_lt": ["LongTermDebtNoncurrent", "LongTermDebtAndCapitalLeaseObligations", "LongTermDebt"],
    "shares_bs": ["CommonStockSharesOutstanding"],
}

SIC_MAJOR = {
    "01": "Agriculture", "02": "Agriculture", "07": "Agriculture", "08": "Forestry", "09": "Fishing",
    "10": "Metal Mining", "12": "Coal Mining", "13": "Oil & Gas Extraction", "14": "Mining (Non-metal)",
    "15": "Construction", "16": "Construction", "17": "Construction",
    "20": "Food Products", "21": "Tobacco", "22": "Textiles", "23": "Apparel", "24": "Lumber & Wood",
    "25": "Furniture", "26": "Paper", "27": "Printing & Publishing", "28": "Chemicals & Pharma",
    "29": "Petroleum Refining", "30": "Rubber & Plastics", "31": "Leather", "32": "Stone, Clay & Glass",
    "33": "Primary Metals", "34": "Fabricated Metals", "35": "Machinery & Computers",
    "36": "Electronics & Semis", "37": "Transportation Equipment", "38": "Instruments & Medical",
    "39": "Misc Manufacturing", "40": "Railroads", "41": "Transit", "42": "Trucking", "44": "Water Transport",
    "45": "Air Transport", "46": "Pipelines", "47": "Transportation Services", "48": "Communications",
    "49": "Utilities", "50": "Wholesale (Durable)", "51": "Wholesale (Nondurable)", "52": "Retail",
    "53": "Retail", "54": "Retail (Food)", "55": "Retail (Auto)", "56": "Retail (Apparel)",
    "57": "Retail (Furniture)", "58": "Restaurants", "59": "Retail (Misc)",
    "60": "Banks", "61": "Credit", "62": "Brokers", "63": "Insurance", "64": "Insurance Agents",
    "65": "Real Estate", "67": "Holding & Investment",
    "70": "Hotels", "72": "Personal Services", "73": "Software & Business Services", "75": "Auto Services",
    "76": "Repair Services", "78": "Motion Pictures", "79": "Recreation", "80": "Health Services",
    "81": "Legal Services", "82": "Education", "83": "Social Services", "87": "Engineering & Mgmt Services",
    "99": "Nonclassifiable",
}


def _download(url: str, dest: Path) -> Path:
    ensure_dir(dest.parent)
    log.info("downloading %s", url)
    with requests.get(url, headers={"User-Agent": UA}, stream=True, timeout=600) as r:
        r.raise_for_status()
        with open(dest, "wb") as f:
            for chunk in r.iter_content(1 << 20):
                f.write(chunk)
    log.info("  -> %s (%.0f MB)", dest, dest.stat().st_size / 1e6)
    return dest


# ----------------------------------------------------------------------------
def build_universe(download: bool = True) -> pd.DataFrame:
    tick_path = RAW_DIR / "company_tickers_exchange.json"
    subs_path = RAW_DIR / "submissions.zip"
    if download or not tick_path.exists():
        _download(TICKERS_URL, tick_path)
    js = json.loads(tick_path.read_text())
    df = pd.DataFrame(js["data"], columns=js["fields"])
    df = df[df["exchange"].isin(["Nasdaq", "NYSE"])].copy()
    df["ticker"] = df["ticker"].astype(str)
    df["has_dash"] = df["ticker"].str.contains("-")
    df["tlen"] = df["ticker"].str.len()
    df = df.sort_values(["cik", "has_dash", "tlen"]).drop_duplicates("cik", keep="first")
    df = df[~df["has_dash"]]

    if download or not subs_path.exists():
        _download(SUBS_URL, subs_path)
    sic = {}
    events = []
    want = {f"CIK{int(c):010d}": int(c) for c in df["cik"]}

    def _collect(cik, block):
        forms, dates, items = block.get("form", []), block.get("filingDate", []), block.get("items", [])
        for f_, d_, i_ in zip(forms, dates, items):
            if f_ in ("8-K", "8-K/A") and i_:
                events.append((cik, d_, i_))

    with zipfile.ZipFile(subs_path) as z:
        for name in z.namelist():
            base = name.split(".json")[0]
            key = base.split("-submissions")[0]
            if key not in want:
                continue
            try:
                d = json.loads(z.read(name))
            except Exception:  # noqa
                continue
            cik = want[key]
            if "-submissions-" in base:      # 較舊的申報資料分頁
                _collect(cik, d)
            else:
                sic[cik] = (str(d.get("sic") or ""), d.get("sicDescription") or "", d.get("entityType") or "")
                _collect(cik, d.get("filings", {}).get("recent", {}))
    if events:
        ev = pd.DataFrame(events, columns=["cik", "date", "items"]).drop_duplicates()
        ev.to_csv(US_EVENTS_PATH, index=False, compression="gzip")
        log.info("US 8-K events: %d", len(ev))
    df["sic"] = df["cik"].map(lambda c: sic.get(int(c), ("", "", ""))[0])
    df["sic_desc"] = df["cik"].map(lambda c: sic.get(int(c), ("", "", ""))[1])
    df["sic2"] = df["sic"].str.zfill(4).str[:2]
    df["industry"] = df["sic2"].map(SIC_MAJOR).fillna("Other")
    sicn = pd.to_numeric(df["sic"], errors="coerce")
    # 細分幾個對景氣循環分析重要的產業
    for lo, hi, label in ((2833, 2836, "Pharma & Biotech"), (3841, 3851, "Medical Devices"),
                          (2840, 2844, "Household & Personal Products"), (4810, 4899, "Telecom"),
                          (4900, 4991, "Utilities"), (5400, 5499, "Retail (Food)"),
                          (3570, 3579, "Computers & Hardware"), (3670, 3679, "Electronics & Semis"),
                          (7370, 7379, "Software & IT Services")):
        df.loc[sicn.between(lo, hi), "industry"] = label
    df["is_financial"] = sicn.between(6000, 6799)
    df["yahoo"] = df["ticker"].str.replace(".", "-", regex=False)
    out = df[["cik", "ticker", "name", "exchange", "sic", "sic_desc", "industry",
              "is_financial", "yahoo"]].reset_index(drop=True)
    ensure_dir(US_UNIVERSE_PATH.parent)
    out.to_csv(US_UNIVERSE_PATH, index=False)
    log.info("US universe: %d companies", len(out))
    return out


# ----------------------------------------------------------------------------
def _facts(gaap: dict, concept: str, unit_pref=("USD", "shares")) -> list[dict]:
    c = gaap.get(concept)
    if not c:
        return []
    units = c.get("units", {})
    for u in unit_pref:
        if u in units:
            return [f for f in units[u] if f.get("form") in FORMS]
    return []


def duration_to_quarters(facts: list[dict]) -> pd.DataFrame:
    """把（季、半年、九個月、全年）的期間數值換算成單季值。"""
    if not facts:
        return pd.DataFrame(columns=["end", "val", "filed"])
    df = pd.DataFrame(facts)
    if "start" not in df:
        return pd.DataFrame(columns=["end", "val", "filed"])
    df = df.dropna(subset=["start", "end", "val"])
    df["start"] = pd.to_datetime(df["start"])
    df["end"] = pd.to_datetime(df["end"])
    df["filed"] = pd.to_datetime(df["filed"])
    df = df.sort_values("filed").drop_duplicates(["start", "end"], keep="first")
    df["days"] = (df["end"] - df["start"]).dt.days
    df = df[df["days"].between(60, 400)]
    out = {}
    q = df[df["days"].between(60, 110)]
    for r in q.itertuples():
        out[r.end] = (r.val, r.filed)
    for start, g in df.groupby("start"):
        g = g.sort_values("end")
        prev_end, prev_val = None, None
        for r in g.itertuples():
            if prev_end is not None and 60 <= (r.end - prev_end).days <= 110 \
                    and r.end not in out:
                out[r.end] = (r.val - prev_val, r.filed)
            prev_end, prev_val = r.end, r.val
    # 損益表常見格式：10-Q 給單季、10-K 給全年 → Q4 = 全年 − 前三季
    fy = df[df["days"].between(330, 400)]
    for r in fy.itertuples():
        if r.end in out:
            continue
        prior = sorted(e for e in out if r.start < e < r.end)
        prior = [e for e in prior if (r.end - e).days >= 60][-3:]
        if len(prior) == 3 and 60 <= (r.end - prior[-1]).days <= 110 \
                and (prior[0] - r.start).days <= 110:
            out[r.end] = (r.val - sum(out[e][0] for e in prior), r.filed)
    if not out:
        return pd.DataFrame(columns=["end", "val", "filed"])
    res = pd.DataFrame([(k, v[0], v[1]) for k, v in out.items()], columns=["end", "val", "filed"])
    return res.sort_values("end")


def instant_series(facts: list[dict]) -> pd.DataFrame:
    if not facts:
        return pd.DataFrame(columns=["end", "val", "filed"])
    df = pd.DataFrame(facts).dropna(subset=["end", "val"])
    df["end"] = pd.to_datetime(df["end"])
    df["filed"] = pd.to_datetime(df["filed"])
    return df.sort_values("filed").drop_duplicates("end", keep="first")[["end", "val", "filed"]]


def _merge_concepts(gaap: dict, concepts: list[str], kind: str) -> pd.DataFrame:
    """依序合併多個候選科目：同一期間以排序在前的科目優先。"""
    merged = None
    for c in concepts:
        f = _facts(gaap, c)
        s = duration_to_quarters(f) if kind == "flow" else instant_series(f)
        if s.empty:
            continue
        s = s.set_index("end")
        merged = s if merged is None else merged.combine_first(s)
    if merged is None:
        return pd.DataFrame(columns=["val", "filed"])
    return merged


WAVG_SHARES = ["WeightedAverageNumberOfDilutedSharesOutstanding", "WeightedAverageNumberOfShareOutstandingBasicAndDiluted",
               "WeightedAverageNumberOfSharesOutstandingBasic"]


def weighted_shares(gaap: dict) -> pd.Series:
    """各期末的加權平均股數（優先稀釋、優先單季；第 4 季只有全年數字時用全年）。index＝期末日。"""
    merged = None
    for c in WAVG_SHARES:
        f = _facts(gaap, c, ("shares",))
        if not f:
            continue
        d = pd.DataFrame(f)
        if not {"start", "end", "val"} <= set(d.columns):
            continue
        d = d.dropna(subset=["start", "end", "val"])
        d["start"], d["end"] = pd.to_datetime(d["start"]), pd.to_datetime(d["end"])
        d["filed"] = pd.to_datetime(d.get("filed"))
        d["days"] = (d["end"] - d["start"]).dt.days
        d = d[d["days"].between(60, 400) & (d["val"] > 0)].sort_values(["end", "days", "filed"])
        if d.empty:
            continue
        s_ = d.groupby("end")["val"].first()
        merged = s_ if merged is None else merged.combine_first(s_)
    return merged if merged is not None else pd.Series(dtype=float)


def company_quarters(cf: dict, cik: int) -> pd.DataFrame:
    facts = cf.get("facts", {})
    gaap = facts.get("us-gaap", {})
    if not gaap:
        return pd.DataFrame()
    series = {}
    filed = {}
    for k, cs in FLOW_CONCEPTS.items():
        m = _merge_concepts(gaap, cs, "flow")
        series[k], filed[k] = m["val"], m["filed"]
    for k, cs in INSTANT_CONCEPTS.items():
        m = _merge_concepts(gaap, cs, "instant")
        series[k], filed[k] = m["val"], m["filed"]

    base = series["net_income"].index.union(series["revenue"].index)
    base = base.intersection(series["total_assets"].index)
    if len(base) == 0:
        return pd.DataFrame()
    df = pd.DataFrame(index=base.sort_values())
    for k, s in series.items():
        df[k] = s.reindex(df.index)
    f_ni = filed["net_income"].reindex(df.index)
    f_ta = filed["total_assets"].reindex(df.index)
    df["available_date"] = pd.concat([f_ni, f_ta], axis=1).max(axis=1)

    df["gross_profit"] = df["gross_profit"].fillna(df["revenue"] - df["cogs"])
    df["total_liab"] = df["total_liab"].fillna(df["liab_and_equity"] - df["equity"])
    df["inventory"] = df["inventory"].fillna(0.0)
    df["goodwill"] = df["goodwill"].fillna(0.0)
    df["intangibles"] = df["intangibles"].fillna(0.0)
    df["st_inv"] = df["st_inv"].fillna(0.0)
    df["lt_inv"] = df[["eq_method", "lt_investments"]].sum(axis=1, min_count=1).fillna(0.0)
    df["debt"] = df[["debt_st", "debt_cur", "debt_lt"]].sum(axis=1, min_count=1).fillna(0.0)
    df["pretax"] = df["pretax"]
    df["nonop"] = df["nonop"].fillna(df["pretax"] - df["op_income"])
    df["capex"] = df["capex"].abs()
    for k in ("sbc", "interest", "da", "div_paid", "buyback"):
        df[k] = df[k].abs()

    # 流通股數：封面頁 dei:EntityCommonStockSharesOutstanding（多類股加總）
    dei = facts.get("dei", {}).get("EntityCommonStockSharesOutstanding", {}).get("units", {}).get("shares", [])
    shares = pd.Series(np.nan, index=df.index)
    shares_date = pd.Series(df.index, index=df.index)
    if dei:
        d = pd.DataFrame(dei).dropna(subset=["end", "val"])
        d["end"] = pd.to_datetime(d["end"])
        d["filed"] = pd.to_datetime(d["filed"])
        d = d.sort_values("filed").drop_duplicates(["end", "val"], keep="first")
        tot = d.groupby("end").agg(val=("val", "sum"), filed=("filed", "min")).sort_index()
        for pe, av in zip(df.index, df["available_date"]):
            if pd.isna(av):
                continue
            # 封面股數的日期可能就是季底（JPM、PG 等），也要採用
            cand = tot[(tot.index >= pe) & (tot.index <= av + pd.Timedelta(days=5))]
            if not cand.empty:
                shares[pe] = cand["val"].iloc[-1]
                shares_date[pe] = cand.index[-1]
    shares = shares.fillna(df["shares_bs"])
    # 多類股公司（META、MA 等：封面股數按類別標示，companyfacts 不收）：改用當季稀釋加權平均股數
    wavg = weighted_shares(gaap)
    if len(wavg):
        shares = shares.fillna(wavg.reindex(df.index))
    df["shares"] = shares
    df["shares_date"] = shares_date
    df.index.name = "period_end"
    df = df.reset_index()
    df["market"] = "us"
    df["id"] = str(cik)
    return df[["market", "id", "period_end", "available_date"] + ALL_FIELDS + EXTRA_FLOW_FIELDS + ["shares_date"]]


def build_fundamentals(universe: pd.DataFrame, download: bool = True,
                       min_year: int = 2010) -> pd.DataFrame:
    zpath = RAW_DIR / "companyfacts.zip"
    if download or not zpath.exists():
        _download(FACTS_URL, zpath)
    want = {f"CIK{int(c):010d}.json": int(c) for c in universe["cik"]}
    frames = []
    with zipfile.ZipFile(zpath) as z:
        names = [n for n in z.namelist() if n in want]
        log.info("parsing %d companyfacts files", len(names))
        for i, name in enumerate(names):
            try:
                q = company_quarters(json.loads(z.read(name)), want[name])
            except Exception as e:  # noqa
                log.debug("skip %s: %s", name, e)
                continue
            if not q.empty:
                frames.append(q[q["period_end"].dt.year >= min_year])
            if (i + 1) % 1000 == 0:
                log.info("  ... %d/%d", i + 1, len(names))
    df = pd.concat(frames, ignore_index=True)
    # 以 ticker 作為對外 id
    cmap = dict(zip(universe["cik"].astype(int).astype(str), universe["ticker"]))
    df["cik"] = df["id"]
    df["id"] = df["id"].map(cmap)
    ensure_dir(US_FUND_PATH.parent)
    df.to_csv(US_FUND_PATH, index=False, compression="gzip")
    log.info("US fundamentals: %d rows, %d companies", len(df), df["id"].nunique())
    return df


def load_fundamentals() -> pd.DataFrame:
    if not US_FUND_PATH.exists():
        return pd.DataFrame()
    return pd.read_csv(US_FUND_PATH, parse_dates=["period_end", "available_date", "shares_date"],
                       dtype={"id": str, "cik": str})


def load_universe() -> pd.DataFrame:
    return pd.read_csv(US_UNIVERSE_PATH, dtype={"ticker": str, "sic": str})
