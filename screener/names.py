"""公司中文名稱與一句話說明（通知與網站顯示用，不影響選股）。

來源優先順序：
1. config/names_zh.yaml 手動整理的中文名＋說明（美股大型股、台股大型股與曾入選的公司）
2. ETF 清單（config/etf_universe.yaml）的名稱與類型
3. 股票清單快取：美股用英文簡稱＋產業中文，台股用中文簡稱＋產業別
"""
from __future__ import annotations

import re
from functools import lru_cache

import pandas as pd
import yaml

from . import common

SEP = "｜"

# 美股產業分類（data_us.SIC_MAJOR 與 industries 細分）→ 中文
IND_ZH = {
    "Pharma & Biotech": "製藥與生技", "Holding & Investment": "控股與投資公司", "Software & IT Services": "軟體與資訊服務",
    "Other": "", "Banks": "銀行", "Electronics & Semis": "電子與半導體", "Credit": "信用與融資",
    "Brokers": "券商與投資服務", "Software & Business Services": "軟體與商業服務", "Medical Devices": "醫療器材",
    "Utilities": "公用事業", "Transportation Equipment": "運輸設備（汽車、航太）", "Metal Mining": "金屬礦業",
    "Machinery & Computers": "機械與電腦設備", "Oil & Gas Extraction": "油氣開採", "Insurance": "保險",
    "Telecom": "電信", "Communications": "通訊", "Food Products": "食品", "Instruments & Medical": "儀器與醫療設備",
    "Chemicals & Pharma": "化學與製藥", "Health Services": "醫療服務", "Retail (Misc)": "零售",
    "Construction": "營建工程", "Engineering & Mgmt Services": "工程與管理顧問", "Water Transport": "航運",
    "Real Estate": "不動產", "Wholesale (Durable)": "批發（耐久財）", "Fabricated Metals": "金屬製品",
    "Computers & Hardware": "電腦硬體", "Restaurants": "餐廳", "Recreation": "休閒娛樂", "Primary Metals": "鋼鐵與金屬",
    "Wholesale (Nondurable)": "批發（民生用品）", "Education": "教育", "Transportation Services": "運輸服務",
    "Hotels": "飯店", "Air Transport": "航空", "Insurance Agents": "保險經紀", "Misc Manufacturing": "其他製造",
    "Household & Personal Products": "家用與個人用品", "Retail (Apparel)": "服飾零售", "Apparel": "服飾",
    "Retail (Auto)": "汽車零售", "Retail": "零售", "Mining (Non-metal)": "非金屬礦業", "Trucking": "卡車貨運",
    "Printing & Publishing": "印刷出版", "Agriculture": "農業", "Rubber & Plastics": "橡膠與塑膠製品", "Paper": "造紙",
    "Petroleum Refining": "煉油", "Stone, Clay & Glass": "水泥、玻璃與陶瓷", "Motion Pictures": "影視製作",
    "Furniture": "家具", "Retail (Food)": "超市與食品零售", "Auto Services": "汽車服務", "Personal Services": "個人服務",
    "Lumber & Wood": "木材", "Retail (Furniture)": "家具零售", "Leather": "皮革", "Coal Mining": "煤礦",
    "Tobacco": "菸草", "Pipelines": "管線運輸", "Railroads": "鐵路", "Textiles": "紡織", "Repair Services": "維修服務",
    "Social Services": "社會服務", "Legal Services": "法律服務", "Fishing": "漁業", "Transit": "大眾運輸",
    "Forestry": "林業", "Nonclassifiable": "",
}
_NO_DESC = {"", "其他", "其他業", "其他類", "nan", "None"}
_SUFFIX = re.compile(r"[,.]?\s+(INC|INCORPORATED|CORP|CORPORATION|CO|COMPANY|LTD|LIMITED|PLC|LLC|L\.?P|N\.?V|S\.?A|AG|SE|"
                     r"S\.?A\.?B\.? DE C\.?V)\.?$", re.I)


def short_en(name) -> str:
    """'MICROSOFT CORP' → 'Microsoft'；'AGILENT TECHNOLOGIES, INC.' → 'Agilent Technologies'。"""
    s = str(name or "").strip()
    if not s or s.lower() == "nan":
        return ""
    s = re.sub(r"\s*[/\\][A-Z]{2,5}[/\\]?\s*$", "", s)          # /DE/、\DE\、/NEW
    for _ in range(3):
        t = _SUFFIX.sub("", s).strip(" ,")
        if t == s or not t:
            break
        s = t
    if s.isupper():                                             # 全大寫 → 首字大寫，3 個字母以內的縮寫保留
        s = " ".join(w if (len(w) <= 3 and w not in {"THE", "AND", "OF", "NEW"}) or not w.isalpha() else w.capitalize()
                     for w in s.split() if w != "COM")
    return s


@lru_cache(maxsize=1)
def curated() -> dict:
    p = common.CONFIG_DIR / "names_zh.yaml"
    d = yaml.safe_load(p.read_text(encoding="utf-8")) if p.exists() else {}
    return {m: {str(k): str(v).strip() for k, v in ((d or {}).get(m) or {}).items() if v} for m in ("us", "tw")}


@lru_cache(maxsize=1)
def universe() -> dict:
    """代號 → (名稱, 產業)：股票清單快取（含 2016 年後下市的台股）與 ETF 清單。"""
    out = {"us": {}, "tw": {}, "etf": {}}
    p = common.CACHE_DIR / "us_universe.csv"
    if p.exists():
        u = pd.read_csv(p, dtype=str)
        for t, n, i in zip(u["ticker"], u["name"], u.get("industry", pd.Series([""] * len(u)))):
            out["us"].setdefault(str(t), (n, i))
    for f in ("tw_universe.csv", "tw_delisted.csv"):
        p = common.CACHE_DIR / f
        if p.exists():
            u = pd.read_csv(p, dtype=str)
            if {"stock_id", "stock_name"} <= set(u.columns):
                ind = u["industry_category"] if "industry_category" in u else pd.Series([""] * len(u))
                for s, n, i in zip(u["stock_id"], u["stock_name"], ind):
                    out["tw"].setdefault(str(s), (n, i))
    try:
        for e in common.load_etf_universe():
            out["etf"][str(e["ticker"])] = (e.get("name", ""), e.get("style", ""))
    except Exception:  # noqa
        pass
    return out


def _clean(x) -> str:
    s = "" if x is None else str(x).strip()
    return "" if s in _NO_DESC else s


def info(mkt: str, sid, name=None) -> tuple[str, str]:
    """回傳 (顯示名稱, 一句話說明)。mkt：tw／us／etf（ETF、大盤補位、核心部位都可用 etf）。"""
    sid = str(sid)
    cur, uni = curated(), universe()
    m = mkt if mkt in ("tw", "us") else ("tw" if sid.endswith((".TW", ".TWO")) else "us")
    base = re.sub(r"\.TWO?$", "", sid) if m == "tw" else sid
    if m == "us" and base in cur["us"]:
        n, _, d = cur["us"][base].partition(SEP)
        return n.strip(), d.strip()
    if sid in uni["etf"]:
        n, st = uni["etf"][sid]
        return _clean(n) or _clean(name) or sid, _clean(st)
    if m == "tw":
        n0, ind = uni["tw"].get(base, ("", ""))
        n = _clean(name) or _clean(n0) or base
        return n, cur["tw"].get(base) or _clean(ind)
    n0, ind = uni["us"].get(base, ("", ""))
    return short_en(name or n0) or base, IND_ZH.get(_clean(ind), _clean(ind))


def label(mkt: str, sid, name=None) -> str:
    """'開拓重工｜工程機械'（不含代號）。"""
    n, d = info(mkt, sid, name)
    return f"{n}{SEP}{d}" if d else n


def tag(mkt: str, sid, name=None) -> str:
    """'CAT 開拓重工｜工程機械'、'2330 台積電｜晶圓代工龍頭'（通知用）。"""
    sid_s = re.sub(r"\.TWO?$", "", str(sid))
    return f"{sid_s} {label(mkt, sid, name)}"


def site_table() -> dict:
    """網站用：{市場: {代號: [顯示名稱, 說明]}}，涵蓋股票清單（含下市台股）、手動整理的公司與 ETF。"""
    cur, uni = curated(), universe()
    out = {"us": {}, "tw": {}, "etf": {}}
    for m in ("us", "tw"):
        for sid in set(uni[m]) | set(cur[m]):
            n, d = info(m, sid)
            out[m][sid] = [n, d] if d else [n]
    for sid in uni["etf"]:
        n, d = info("etf", sid)
        out["etf"][sid] = [n, d] if d else [n]
    return out
