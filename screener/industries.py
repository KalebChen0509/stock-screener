"""市場 × 產業別 → 產業模板、同業比較群組。

模板（8 種）決定這檔股票用哪一套篩選條件與估值方式：
  general            一般（製造、電子、服務、非必需消費）
  cyclical           循環（原物料、航運、半導體庫存循環、汽車、能源）
  financial          金融（銀行、保險、證券、信貸）
  construction       營建（台股建材營造、美股建商與不動產）
  utility            公用（電力、燃氣、電信三雄、管線、REITs）
  defensive          防禦（食品、日用品、已獲利的醫療）
  growth_watch       成長觀察（美股套裝軟體、台股數位雲端與電商）：只估值、不自動買
  biotech_research   新藥研究（還沒獲利的製藥與生技）：不自動篩，用研究工具
  exclude            排除（創新板、空殼公司）

同業比較群組（peer_group）：同市場、同產業別的公司數 ≥ min_peers 時用產業別；
不足時改跟同模板的公司比；模板也不足時跟全市場比。
"""
from __future__ import annotations

import pandas as pd

TEMPLATES = ["general", "cyclical", "financial", "construction", "utility", "defensive",
             "growth_watch", "biotech_research", "exclude"]
TEMPLATE_LABEL = {
    "general": "一般", "cyclical": "循環", "financial": "金融", "construction": "營建",
    "utility": "公用", "defensive": "防禦", "growth_watch": "成長觀察",
    "biotech_research": "新藥研究", "exclude": "排除",
}
NOT_BUYABLE = {"growth_watch", "biotech_research", "exclude"}

# ---------------- 台股：證交所／櫃買產業別 ----------------
TW_SYNONYMS = {
    "金融業": "金融保險", "金融保險業": "金融保險",
    "綠能環保類": "綠能環保", "數位雲端類": "數位雲端", "居家生活類": "居家生活",
    "其他電子類": "其他電子業", "電子工業": "其他電子業",
    "觀光事業": "觀光餐旅", "貿易百貨業": "貿易百貨", "建材營造業": "建材營造",
    "生技醫療": "生技醫療業",
}
TW_TEMPLATE = {
    "半導體業": "cyclical", "光電業": "cyclical",
    "電子零組件業": "general", "電腦及週邊設備業": "general", "通信網路業": "general",
    "電子通路業": "general", "資訊服務業": "general", "其他電子業": "general",
    "數位雲端": "growth_watch", "電子商務業": "growth_watch",
    "建材營造": "construction",
    "鋼鐵工業": "cyclical", "塑膠工業": "cyclical", "化學工業": "cyclical", "水泥工業": "cyclical",
    "玻璃陶瓷": "cyclical", "造紙工業": "cyclical", "橡膠工業": "cyclical", "電器電纜": "cyclical",
    "汽車工業": "cyclical", "航運業": "cyclical",
    "紡織纖維": "general", "電機機械": "general", "觀光餐旅": "general", "貿易百貨": "general",
    "居家生活": "general", "運動休閒": "general", "文化創意業": "general",
    "食品工業": "defensive", "農業科技業": "defensive",
    "生技醫療業": "pharma", "化學生技醫療": "pharma",        # pharma：依是否獲利分成防禦或新藥研究
    "油電燃氣業": "utility", "綠能環保": "utility",
    "金融保險": "financial",
    "創新板股票": "exclude",
}
TW_TELECOM_IDS = {"2412", "3045", "4904"}                 # 中華電、台灣大、遠傳 → 公用

# ---------------- 美股：SEC SIC 產業別（data_us 的 industry 欄） ----------------
US_TEMPLATE = {
    "Pharma & Biotech": "pharma",
    "Medical Devices": "defensive", "Instruments & Medical": "defensive", "Health Services": "defensive",
    "Banks": "financial", "Insurance": "financial", "Brokers": "financial", "Credit": "financial",
    "Insurance Agents": "general",
    "Holding & Investment": "holding",                      # 依 SIC 再分
    "Real Estate": "construction", "Construction": "construction",
    "Software & IT Services": "software",                   # 依 SIC 再分
    "Software & Business Services": "general",
    "Electronics & Semis": "semis",                         # 依 SIC 再分
    "Computers & Hardware": "general", "Machinery & Computers": "general",
    "Utilities": "utility", "Telecom": "utility", "Pipelines": "utility", "Communications": "utility",
    "Oil & Gas Extraction": "cyclical", "Petroleum Refining": "cyclical", "Coal Mining": "cyclical",
    "Metal Mining": "cyclical", "Mining (Non-metal)": "cyclical", "Primary Metals": "cyclical",
    "Chemicals & Pharma": "cyclical", "Paper": "cyclical", "Rubber & Plastics": "cyclical",
    "Stone, Clay & Glass": "cyclical", "Lumber & Wood": "cyclical", "Forestry": "cyclical",
    "Transportation Equipment": "cyclical",
    "Air Transport": "cyclical", "Water Transport": "cyclical", "Railroads": "cyclical",
    "Trucking": "cyclical", "Transportation Services": "cyclical", "Transit": "cyclical",
    "Fabricated Metals": "general", "Misc Manufacturing": "general", "Engineering & Mgmt Services": "general",
    "Furniture": "general", "Textiles": "general", "Leather": "general", "Apparel": "general",
    "Food Products": "defensive", "Household & Personal Products": "defensive", "Tobacco": "defensive",
    "Retail (Food)": "defensive", "Agriculture": "defensive", "Fishing": "defensive",
}
# 美股其餘（零售、餐飲、旅館、娛樂、批發、其他服務）→ general


def normalize_industry(name, market: str) -> str:
    s = "" if name is None or (isinstance(name, float) and pd.isna(name)) else str(name).strip()
    if market == "tw":
        return TW_SYNONYMS.get(s, s) or "未分類"
    return s or "Other"


def _sic(x) -> int | None:
    try:
        return int(float(x))
    except (TypeError, ValueError):
        return None


def template_of(market: str, industry_norm: str, sid: str, sic=None, profitable=None) -> str:
    """回傳模板名稱。profitable：近 3 年每年獲利（製藥、生技用來分流）。"""
    if market == "tw":
        if str(sid) in TW_TELECOM_IDS:
            return "utility"
        t = TW_TEMPLATE.get(industry_norm, "general")
    else:
        t = US_TEMPLATE.get(industry_norm, "general")
        code = _sic(sic)
        if t == "holding":
            if code == 6770:
                return "exclude"              # 空殼公司（SPAC）
            t = "utility" if code == 6798 else "financial"   # REITs 歸公用（穩定、高配息、槓桿高）
        elif t == "software":
            t = "growth_watch" if code == 7372 else "general"  # 套裝軟體＝成長觀察；IT 服務＝一般
        elif t == "semis":
            t = "cyclical" if code is not None and 3670 <= code <= 3679 else "general"
        if code == 8731 and not profitable:      # 商業研究服務（多為早期生技）
            return "biotech_research"
    if t == "pharma":
        t = "defensive" if bool(profitable) else "biotech_research"
    return t


def assign(df: pd.DataFrame, market: str, min_peers: int = 15) -> pd.DataFrame:
    """在 df 加上 industry_norm、template、template_label、peer_group、peer_n。
    df 需要 id、industry；美股另用 sic；製藥分流用 profitable_3y。"""
    df = df.copy()
    df["industry_norm"] = [normalize_industry(x, market) for x in df["industry"]]
    sic = df["sic"] if "sic" in df else pd.Series(None, index=df.index)
    prof = df["profitable_3y"] if "profitable_3y" in df else pd.Series(False, index=df.index)
    df["template"] = [template_of(market, ind, sid, s, p) for ind, sid, s, p in
                      zip(df["industry_norm"], df["id"].astype(str), sic, prof)]
    df["template_label"] = df["template"].map(TEMPLATE_LABEL)
    n_ind = df.groupby("industry_norm")["id"].transform("count")
    n_tpl = df.groupby("template")["id"].transform("count")
    df["peer_group"] = df["industry_norm"].where(n_ind >= min_peers,
                                                 ("模板：" + df["template_label"]).where(n_tpl >= min_peers, "全市場"))
    df["peer_n"] = df.groupby("peer_group")["id"].transform("count")
    return df
