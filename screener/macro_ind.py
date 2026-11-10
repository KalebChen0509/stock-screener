"""總經指標：台灣、美國、中國、日本、歐洲的「領先／同時／落後」指標，與轉折訊號。

用法（「市場事件偵測」workflow 每 30 分鐘呼叫 check()；Mac 常駐程式在公布時段每 2 分鐘呼叫）：
    events, site = check(store, state, now)
    events → 交給 market_events 合併、通知；site → 網站「總經指標」頁（store/macro.json）

資料來源（都免費）：
    國發會「景氣指標及燈號」（政府資料開放平臺 6099）：台灣領先／同時／落後指數、燈號、各構成項目
    FRED（聖路易聯準銀行）：美國各項指標、歐元區 HICP、中國出口、日本失業率
    OECD 官方 API：各國綜合領先指標（CLI）、中國 CPI、日本／歐元區工業生產、德國失業率
    Google 新聞：各國製造業 PMI（PMI 資料有版權、國發會網站有機器人防護 → 從新聞標題讀數字）

轉折訊號（state 函式把整段歷史算成「狀態」序列，最新一筆的狀態跟前一筆不同＝轉折）：
    cli      綜合領先指標：位置（≥100／<100）× 方向（連 2 個月同方向才改）→ 擴張／趨緩／衰退／復甦
    curve    殖利率曲線：5 日平均 < −0.05 → 倒掛；> +0.05 → 恢復正常（解除倒掛後常是衰退開始）
    claims   初領失業金 4 週平均 ≥ 一年最低 ×1.20 → 警訊；< ×1.10 解除
    sahm     薩姆規則（失業率 3 個月平均比一年低點高 ≥0.5 個百分點）→ 觸發；< 0.3 解除
    cfnai    芝加哥 Fed 全國活動指數 3 個月平均 < −0.7 → 衰退警訊；> −0.5 解除
    nfci     芝加哥 Fed 金融情勢指數 > 0.05 → 偏緊；< −0.05 → 寬鬆
    credit   公司債利差 3 個月擴大 ≥0.5 個百分點 → 擴大；< 0.25 解除（Moody's 有版權，只顯示狀態）
    level    擴散指數（PMI、外銷訂單動向、費城 Fed）跨過門檻（50 或 0），連 2 個月才改
    yoy      年增率 3 個月平均正負翻轉，連 2 個月才改
    light    國發會景氣燈號換顏色
只有「重大」等級的轉折立刻通知；「注意」等級進每日彙整；指標公布本身只記錄、不通知（使用者要求）。
這些訊號還沒回測過（research/macro_signals.py），只顯示＋通知，不影響選股與進場。
"""
from __future__ import annotations

import io
import json
import os
import re
import time
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd
import requests

from .common import log

UA = {"User-Agent": "Mozilla/5.0 (stock-screener; personal research)"}
FRED_API = "https://api.stlouisfed.org/fred/series/observations"
FRED_CSV = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={sid}"
OECD = "https://sdmx.oecd.org/public/rest/data/"
NDC_API = "https://data.gov.tw/api/v2/rest/dataset/6099"
COUNTRIES = {"tw": "台灣", "us": "美國", "cn": "中國", "jp": "日本", "eu": "歐洲", "gl": "全球"}
KINDS = {"lead": "領先", "coin": "同時", "lag": "落後"}

# ---------------------------------------------------------------- 指標清單
# key, 國家, 類別, 名稱, 來源, 代碼, 轉換, 規則, 通知等級, 可公開原始數值, 單位／說明
#   轉換：level 原值、yoy 年增率 %、chg 月變動、ma4 4 週平均（千人）
#   規則：None＝只顯示；通知等級：重大＝立刻、注意＝每日彙整
I = lambda *a: dict(zip(("key", "country", "kind", "name", "src", "sid", "tf", "rule", "level", "public", "unit"), a))  # noqa: E731
CATALOG = [
    # ---- 台灣（國發會）
    I("tw_lead", "tw", "lead", "領先指標（不含趨勢）", "ndc", "領先指標不含趨勢指數", "level", "cli", "重大", True, "100＝長期趨勢"),
    I("tw_light", "tw", "coin", "景氣對策信號（燈號分數）", "ndc", "景氣對策信號綜合分數", "level", "light", "重大", True, "分"),
    I("tw_coin", "tw", "coin", "同時指標（不含趨勢）", "ndc", "同時指標不含趨勢指數", "level", "cli", "重大", True, "100＝長期趨勢"),
    I("tw_lag", "tw", "lag", "落後指標（不含趨勢）", "ndc", "落後指標不含趨勢指數", "level", "cli", "注意", True, "100＝長期趨勢"),
    I("tw_orders", "tw", "lead", "外銷訂單動向指數", "ndc", "外銷訂單動向指數", "level", "level50", "注意", True, "50＝持平"),
    I("tw_m1b", "tw", "lead", "M1B 貨幣總計數", "ndc", "貨幣總計數M1B", "yoy", "yoy", "注意", True, "年增率"),
    I("tw_stock", "tw", "lead", "股價指數", "ndc", "股價指數", "yoy", None, "", True, "年增率"),
    I("tw_hire", "tw", "lead", "受僱員工淨進入率", "ndc", "受僱員工淨進入率", "level", None, "", True, "%"),
    I("tw_build", "tw", "lead", "建築物開工面積", "ndc", "建築物開工樓地板面積", "yoy", None, "", True, "年增率（轉折看 3 個月平均）"),
    I("tw_semi", "tw", "lead", "半導體設備進口", "ndc", "名目半導體設備進口", "yoy", "yoy", "注意", True, "年增率（轉折看 3 個月平均）"),
    I("tw_pmi", "tw", "lead", "製造業 PMI（中經院）", "news", "tw", "level", "level50", "重大", True, "50＝榮枯線"),
    I("tw_ip", "tw", "coin", "工業生產指數", "ndc", "工業生產指數", "yoy", None, "", True, "年增率"),
    I("tw_power", "tw", "coin", "企業總用電量", "ndc", "電力(企業)總用電量", "yoy", None, "", True, "年增率"),
    I("tw_sales", "tw", "coin", "製造業銷售量指數", "ndc", "製造業銷售量指數", "yoy", None, "", True, "年增率"),
    I("tw_retail", "tw", "coin", "批發零售餐飲營業額", "ndc", "批發、零售及餐飲業營業額", "yoy", None, "", True, "年增率"),
    I("tw_ot", "tw", "coin", "加班工時", "ndc", "工業及服務業加班工時", "level", None, "", True, "小時"),
    I("tw_export", "tw", "coin", "海關出口值", "ndc", "海關出口值", "yoy", "yoy", "注意", True, "年增率（轉折看 3 個月平均）"),
    I("tw_mach", "tw", "coin", "機械及電機設備進口", "ndc", "機械及電機設備進口值", "yoy", None, "", True, "年增率"),
    I("tw_unemp", "tw", "lag", "失業率", "ndc", "失業率", "level", None, "", True, "%"),
    I("tw_ulc", "tw", "lag", "製造業單位產出勞動成本", "ndc", "製造業單位產出勞動成本指數", "yoy", None, "", True, "年增率"),
    I("tw_rate", "tw", "lag", "五大銀行新承做放款利率", "ndc", "五大銀行新承做放款平均利率", "level", None, "", True, "%"),
    I("tw_policy", "tw", "lag", "央行重貼現率（政策利率）", "cbc", "policy", "level", "policy", "重大", True, "%"),
    I("tw_onrate", "tw", "lag", "金融業隔夜拆款利率", "cbc", "overnight", "level", None, "", True, "%（資金鬆緊；利率低時只跟政策利率小幅連動）"),
    I("tw_loans", "tw", "lag", "金融機構放款與投資", "ndc", "全體金融機構放款與投資", "yoy", None, "", True, "年增率"),
    I("tw_inv", "tw", "lag", "製造業存貨價值", "ndc", "製造業存貨價值", "yoy", None, "", True, "年增率"),
    # ---- 美國
    I("us_cli", "us", "lead", "OECD 綜合領先指標", "oecd_cli", "USA", "level", "cli", "重大", True, "100＝長期趨勢"),
    I("us_curve", "us", "lead", "殖利率曲線 10 年－3 個月", "fred", "T10Y3M", "level", "curve", "重大", True, "百分點"),
    I("us_curve2", "us", "lead", "殖利率曲線 10 年－2 年", "fred", "T10Y2Y", "level", "curve", "注意", True, "百分點"),
    I("us_claims", "us", "lead", "初領失業救濟金（4 週平均）", "fred", "ICSA", "ma4", "claims", "重大", True, "千人"),
    I("us_pmi", "us", "lead", "ISM 製造業 PMI", "news", "us", "level", "level50", "重大", True, "50＝榮枯線"),
    I("us_permit", "us", "lead", "建築許可", "fred", "PERMIT", "yoy", "yoy", "注意", True, "年增率（轉折看 3 個月平均）"),
    I("us_houst", "us", "lead", "新屋開工", "fred", "HOUST", "yoy", None, "", True, "年增率"),
    I("us_orders", "us", "lead", "核心資本財新訂單", "fred", "NEWORDER", "yoy", "yoy", "注意", True, "年增率（轉折看 3 個月平均）"),
    I("us_philly", "us", "lead", "費城 Fed 製造業新訂單", "fred", "NOCDFSA066MSFRBPHI", "level", "level0", "注意", True, "0＝持平"),
    I("us_hours", "us", "lead", "製造業每週工時", "fred", "AWHMAN", "level", None, "", True, "小時"),
    I("us_m2", "us", "lead", "M2 貨幣供給", "fred", "M2SL", "yoy", None, "", True, "年增率"),
    I("us_sent", "us", "lead", "密大消費者信心", "fred", "UMCSENT", "level", None, "", False, "指數（有版權，只顯示變化）"),
    I("us_credit", "us", "lead", "公司債信用利差（Baa－10 年）", "fred", "BAA10Y", "level", "credit", "重大", False, "有版權，只顯示狀態"),
    I("us_nfci", "us", "lead", "芝加哥 Fed 金融情勢指數", "fred", "NFCI", "level", "nfci", "重大", True, "0＝平均，正＝偏緊"),
    I("us_payems", "us", "coin", "非農就業（月增）", "fred", "PAYEMS", "chg", None, "", True, "千人"),
    I("us_ip", "us", "coin", "工業生產", "fred", "INDPRO", "yoy", None, "", True, "年增率"),
    I("us_income", "us", "coin", "實質個人所得（扣移轉）", "fred", "W875RX1", "yoy", None, "", True, "年增率"),
    I("us_retail", "us", "coin", "實質零售銷售", "fred", "RRSFS", "yoy", None, "", True, "年增率"),
    I("us_cfnai", "us", "coin", "芝加哥 Fed 全國活動指數", "fred", "CFNAI", "level", "cfnai", "重大", True, "0＝趨勢成長"),
    I("us_unrate", "us", "lag", "失業率", "fred", "UNRATE", "level", None, "", True, "%"),
    I("us_sahm", "us", "lag", "薩姆規則（失業率）", "fred", "SAHMREALTIME", "level", "sahm", "重大", True, "百分點，≥0.5 觸發"),
    I("us_cpi", "us", "lag", "CPI", "fred", "CPIAUCSL", "yoy", None, "", True, "年增率"),
    I("us_core", "us", "lag", "核心 CPI", "fred", "CPILFESL", "yoy", None, "", True, "年增率"),
    I("us_ulc", "us", "lag", "單位勞動成本", "fred", "ULCNFB", "yoy", None, "", True, "年增率（季）"),
    I("us_prime", "us", "lag", "基本放款利率", "fred", "DPRIME", "level", None, "", True, "%"),
    I("us_fedtarget", "us", "lag", "聯準會目標利率（區間上限）", "nyfed", "unsecured/effr#target", "level", "policy", "重大", True, "%"),
    I("us_effr", "us", "lag", "聯邦資金有效利率（EFFR，隔夜）", "nyfed", "unsecured/effr", "level", None, "", True, "%（實際隔夜拆款利率）"),
    I("us_sofr", "us", "lag", "擔保隔夜融資利率（SOFR）", "nyfed", "secured/sofr", "level", None, "", True, "%"),
    I("us_loans", "us", "lag", "企業放款", "fred", "BUSLOANS", "yoy", None, "", True, "年增率"),
    I("us_dur", "us", "lag", "平均失業週數", "fred", "UEMPMEAN", "level", None, "", True, "週"),
    I("us_invr", "us", "lag", "存貨銷售比", "fred", "ISRATIO", "level", None, "", True, "倍"),
    I("us_cclaims", "us", "lag", "續領失業救濟金", "fred", "CCSA", "k", None, "", True, "千人"),
    # ---- 中國
    I("cn_cli", "cn", "lead", "OECD 綜合領先指標", "oecd_cli", "CHN", "level", "cli", "重大", True, "100＝長期趨勢"),
    I("cn_pmi", "cn", "lead", "官方製造業 PMI（統計局）", "news", "cn", "level", "level50", "重大", True, "50＝榮枯線"),
    I("cn_export", "cn", "coin", "出口值", "fred", "XTEXVA01CNM667S", "yoy", None, "", True, "年增率"),
    I("cn_cpi", "cn", "lag", "CPI", "oecd_cpi", "CHN", "level", None, "", True, "年增率"),
    # ---- 日本
    I("jp_cli", "jp", "lead", "OECD 綜合領先指標", "oecd_cli", "JPN", "level", "cli", "重大", True, "100＝長期趨勢"),
    I("jp_pmi", "jp", "lead", "製造業 PMI", "news", "jp", "level", "level50", "注意", True, "50＝榮枯線"),
    I("jp_ip", "jp", "coin", "工業生產", "oecd_ip", "JPN", "yoy", None, "", True, "年增率"),
    I("jp_unemp", "jp", "lag", "失業率", "fred", "LRUN64TTJPM156S", "level", None, "", True, "%"),
    # ---- 歐洲
    I("eu_cli", "eu", "lead", "OECD 綜合領先指標（歐洲四大國）", "oecd_cli", "G4E", "level", "cli", "重大", True, "100＝長期趨勢"),
    I("eu_de_cli", "eu", "lead", "OECD 綜合領先指標（德國）", "oecd_cli", "DEU", "level", "cli", "注意", True, "100＝長期趨勢"),
    I("eu_pmi", "eu", "lead", "歐元區製造業 PMI", "news", "eu", "level", "level50", "注意", True, "50＝榮枯線"),
    I("eu_ip", "eu", "coin", "歐元區工業生產", "oecd_ip", "EA20", "yoy", None, "", True, "年增率"),
    I("eu_hicp", "eu", "lag", "歐元區 HICP 物價", "fred", "CP0000EZ19M086NEST", "yoy", None, "", True, "年增率"),
    I("eu_unemp", "eu", "lag", "德國失業率", "oecd_une", "DEU", "level", None, "", True, "%"),
    # ---- 全球
    I("gl_cli", "gl", "lead", "OECD 綜合領先指標（G7）", "oecd_cli", "G7", "level", "cli", "注意", True, "100＝長期趨勢"),
    I("gl_copper", "gl", "lead", "銅價", "fred", "PCOPPUSDM", "yoy", None, "", True, "年增率"),
]
BY_KEY = {c["key"]: c for c in CATALOG}

# 公布時段（台北時間）：這段時間每次都重抓；其他時間隔 REFRESH_H 小時
US_WINDOW = (20, 24)          # 美國 8:30／10:00（美東）＝台北 20:30～23:00（夏令／冬令）
TW_WINDOW = (15, 19)          # 國發會景氣燈號約 16:00，月底前後
REFRESH_H = {"fred": 6, "ndc": 12, "oecd": 12, "news": 0, "cbc": 12, "nyfed": 6}
NYFED = "https://markets.newyorkfed.org/api/rates/{path}/search.json?startDate={start}&endDate={end}"   # 紐約聯準銀行（免金鑰；EFFR 2000-07 起、SOFR 2018-04 起）
CBC_RATES = "https://www.cbc.gov.tw/public/data/OpenData/經研處/EG37D01.csv"    # 央行「金融業拆款利率」（1991-10 起每日，每月更新）
CBC_POLICY = "https://www.cbc.gov.tw/Public/Data/opendata/webF1.csv"             # 央行「貼放利率」調整紀錄（2000-12 起）


# ---------------------------------------------------------------- 下載
def fetch_fred(sid: str, start: str = "1990-01-01", key: str | None = None, http=None) -> pd.Series:
    """FRED：有金鑰走官方 API（GitHub Secrets FRED_API_KEY；Mac 這邊網頁 CSV 會被擋），沒有就用 CSV。"""
    http = http or requests
    key = key if key is not None else os.environ.get("FRED_API_KEY")
    if key:
        r = http.get(FRED_API, params={"series_id": sid, "api_key": key, "file_type": "json", "observation_start": start},
                     timeout=(10, 60))
        r.raise_for_status()
        obs = r.json().get("observations", [])
        s = pd.Series({o["date"]: o["value"] for o in obs})
    else:
        r = http.get(FRED_CSV.format(sid=sid) + f"&cosd={start}", timeout=(10, 60), headers=UA)
        r.raise_for_status()
        d = pd.read_csv(io.StringIO(r.text))
        s = pd.Series(d.iloc[:, 1].values, index=d.iloc[:, 0].values)
    s = pd.to_numeric(s, errors="coerce").dropna()
    s.index = pd.to_datetime(s.index)
    return s.sort_index()


OECD_FLOWS = {
    "oecd_cli": ("OECD.SDD.STES,DSD_STES@DF_CLI,4.1", "{a}.M.LI...AA...H"),
    "oecd_cpi": ("OECD.SDD.TPS,DSD_PRICES@DF_PRICES_ALL,1.0", "{a}.M.N.CPI.PA._T.N.GY"),
    "oecd_ip": ("OECD.SDD.STES,DSD_KEI@DF_KEI,4.0", "{a}.M.PRVM.IX.BTE.."),
    "oecd_une": ("OECD.SDD.TPS,DSD_LFS@DF_IALFS_UNE_M,1.0", "{a}..._Z.Y._T.Y_GE15..M"),
}


def fetch_oecd(src: str, areas: list[str], start: str = "1990-01", http=None) -> dict[str, pd.Series]:
    """OECD 官方 SDMX API（一次抓多個國家）。回傳 {國家代碼: 月資料}。"""
    flow, key = OECD_FLOWS[src]
    url = f"{OECD}{flow}/{key.format(a='+'.join(areas))}?startPeriod={start}&format=csvfile"
    r = (http or requests).get(url, timeout=(10, 120), headers=UA)
    r.raise_for_status()
    return parse_oecd(r.text)


def parse_oecd(txt: str) -> dict[str, pd.Series]:
    d = pd.read_csv(io.StringIO(txt))
    d["v"] = pd.to_numeric(d["OBS_VALUE"], errors="coerce")
    d["t"] = pd.to_datetime(d["TIME_PERIOD"].astype(str) + "-01", errors="coerce")
    d = d.dropna(subset=["v", "t"])
    # 同一國同一月有多列時（例如不同季調方式），取第一列
    return {a: g.drop_duplicates("t").set_index("t")["v"].sort_index() for a, g in d.groupby("REF_AREA")}


def fetch_ndc(http=None) -> pd.DataFrame:
    """國發會資料集 ZIP（景氣指標與燈號＋三種構成項目）→ 月資料寬表（欄名＝去掉單位的中文名稱）。"""
    http = http or requests
    meta = http.get(NDC_API, timeout=30).json()
    url = meta["result"]["distribution"][0]["resourceDownloadUrl"]
    r = http.get(url, timeout=60, headers=UA)
    r.raise_for_status()
    return parse_ndc_zip(r.content)


def fetch_cbc_overnight(http=None) -> pd.Series:
    """央行金融業拆款利率 CSV → 每日隔夜拆款加權平均利率（%）。日期是 yyyymmdd；「yyyymm00」是月平均，不要。"""
    r = (http or requests).get(CBC_RATES, timeout=60, headers=UA)
    r.raise_for_status()
    return parse_cbc_overnight(r.content.decode("utf-8-sig", errors="replace"))


def fetch_cbc_policy(http=None, today=None) -> pd.Series:
    r = (http or requests).get(CBC_POLICY, timeout=60, headers=UA)
    r.raise_for_status()
    return parse_cbc_policy(r.content.decode("cp950", errors="replace"), today)


def parse_cbc_policy(txt: str, today=None) -> pd.Series:
    """調整紀錄（調整日期、重貼現率…）→ 每個營業日的重貼現率（沿用到今天）。"""
    d = pd.read_csv(io.StringIO(txt))
    d.columns = [c.strip() for c in d.columns]
    s = pd.Series(pd.to_numeric(d["重貼現率"], errors="coerce").values, index=pd.to_datetime(d["調整日期"])).dropna().sort_index()
    idx = pd.bdate_range(s.index.min(), pd.Timestamp(today or pd.Timestamp.today()).normalize())
    return s.reindex(idx.union(s.index)).ffill().reindex(idx)


def fetch_nyfed(path: str, start: str = "2000-01-01", http=None) -> pd.Series:
    """紐約聯準銀行參考利率（EFFR、SOFR）每日 %。path 加「#target」＝聯準會目標利率
    （2008-12 起是區間上限 targetRateTo，之前是單一目標 targetRateFrom）。"""
    path, _, want = path.partition("#")
    url = NYFED.format(path=path, start=start, end=pd.Timestamp.today().strftime("%Y-%m-%d"))
    r = (http or requests).get(url, timeout=(10, 90), headers=UA)
    r.raise_for_status()
    rows = r.json().get("refRates", [])
    if want == "target":
        s = pd.Series({x["effectiveDate"]: x.get("targetRateTo", x.get("targetRateFrom")) for x in rows}, dtype=float)
    else:
        s = pd.Series({x["effectiveDate"]: x.get("percentRate") for x in rows}, dtype=float)
    s.index = pd.to_datetime(s.index)
    return s.dropna().sort_index()


def parse_cbc_overnight(txt: str) -> pd.Series:
    d = pd.read_csv(io.StringIO(txt), dtype=str)
    d.columns = [c.strip().strip('"') for c in d.columns]
    day = d.columns[0]
    col = next(c for c in d.columns if c.startswith("隔夜-加權平均"))
    d = d[d[day].str.fullmatch(r"\d{6}(?!00)\d{2}", na=False)]
    s = pd.to_numeric(d[col], errors="coerce")
    s.index = pd.to_datetime(d[day], format="%Y%m%d")
    return s.dropna().sort_index()


def _clean_col(c: str) -> str:
    """去掉最後面的單位括號：「電力(企業)總用電量(十億度)」→「電力(企業)總用電量」。"""
    return re.sub(r"[\(（][^\(（]*[\)）]\s*$", "", c.strip().strip('"').lstrip("﻿")).strip()


def parse_ndc_zip(content: bytes) -> pd.DataFrame:
    z = zipfile.ZipFile(io.BytesIO(content))
    frames = []
    for n in z.namelist():
        if n.startswith("schema") or n.startswith("manifest") or not n.endswith(".csv"):
            continue
        raw = z.read(n)
        try:
            txt = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            txt = raw.decode("cp950")
        if "Date" not in txt[:200]:
            continue
        frames.append(parse_ndc_csv(txt))
    if not frames:
        return pd.DataFrame()
    out = pd.concat(frames, axis=1)
    return out.loc[:, ~out.columns.duplicated()].sort_index()


def parse_ndc_csv(txt: str) -> pd.DataFrame:
    d = pd.read_csv(io.StringIO(txt), dtype=str)
    d.columns = [_clean_col(c) for c in d.columns]
    d = d[d["Date"].str.fullmatch(r"\d{6}", na=False)]
    idx = pd.to_datetime(d["Date"] + "01", format="%Y%m%d")
    out = pd.DataFrame(index=idx)
    for c in d.columns:
        if c == "Date":
            continue
        if c == "景氣對策信號":
            out["燈號"] = d[c].str.strip().replace({"-": None}).values
        else:
            out[c] = pd.to_numeric(d[c].str.replace(",", ""), errors="coerce").values
    return out


# ---------------------------------------------------------------- PMI（新聞標題）
PMI_NEWS = {
    # 搜尋字, 語言, 標題一定要有其中一個字, 標題有就不要（另一個國家、初值、別家的 PMI）
    "tw": ("台灣 製造業 PMI 中經院", "zh", ["台灣", "臺灣", "中經院", "國發會"], ["中國", "大陸", "陸製造", "美國", "日本", "歐"]),
    "cn": ("中國 統計局 製造業 PMI", "zh", ["中國", "大陸", "陸製造", "統計局", "官方"], ["財新", "RatingDog", "台灣", "臺灣", "美國", "日本", "歐"]),
    "us": ("ISM manufacturing PMI", "en", ["ism"], ["flash", "s&p global"]),
    "jp": ("Japan manufacturing PMI", "en", ["japan"], ["flash"]),
    "eu": ("eurozone manufacturing PMI", "en", ["euro"], ["flash"]),
}
_PMI_AT = re.compile(r"PMI|採購經理人指數", re.I)
_NUM = re.compile(r"(\d{2}\.\d)(?!\d)")
_NOT_MFG = ("非製造", "服務", "綜合", "services", "composite", "non-manufacturing")


def pmi_from_title(area: str, title: str) -> float | None:
    """從新聞標題讀出製造業 PMI 數值；讀不到或不是這個國家的製造業 PMI → None。
    「PMI」前面 6 個字內有非製造／服務／綜合的不算；取「PMI」後面 30 個字內的第一個 xx.x。"""
    _, lang, must, bad = PMI_NEWS[area]
    low = title.lower()
    if not any(w.lower() in low for w in must) or any(w.lower() in low for w in bad):
        return None
    if lang == "en" and "manufactur" not in low and area != "us":
        return None
    for m in _PMI_AT.finditer(title):
        before = low[max(0, m.start() - (6 if lang == "zh" else 20)):m.start()]
        if any(w in before for w in _NOT_MFG):
            continue
        after = title[m.end():m.end() + 30]
        cut = re.search(r"非製造|服務|綜合|services|composite", after, re.I)
        n = _NUM.search(after[:cut.start()] if cut else after)
        if n:
            v = float(n.group(1))
            if 30 <= v <= 70:
                return v
    return None


def news_pmi(now: pd.Timestamp, lookback_days: int = 10, fetch=None) -> dict[str, dict]:
    """各國最新月份的製造業 PMI（新聞標題多數決）→ {area: {value, month, n, title, url, source, time}}。
    month＝資料所屬月份（新聞在月初公布上個月；15 號以後的新聞當成本月，例如中國在月底公布）。
    同一個月有不同數字時取出現最多次的（一則標題讀錯不會造成假的轉折）。"""
    from collections import Counter
    from .events import google_news
    fetch = fetch or google_news
    since = (now.tz_convert("UTC") - pd.Timedelta(days=lookback_days)).tz_localize(None)
    out = {}
    for area, (q, lang, _, _) in PMI_NEWS.items():
        try:
            items = fetch(q, lang="zh-TW" if lang == "zh" else "en", since=since, limit=20)
        except Exception as e:  # noqa
            log.warning("pmi news %s: %s", area, e)
            continue
        got = []
        for it in items:
            v = pmi_from_title(area, it.get("title") or "")
            t = pd.to_datetime(it.get("date"), errors="coerce")
            if v is None or t is None or pd.isna(t):
                continue
            t = t.tz_localize("UTC").tz_convert("Asia/Taipei")
            ref = (t.tz_localize(None).to_period("M") - (1 if t.day < 15 else 0)).to_timestamp()
            got.append((ref, v, t, it))
        if not got:
            continue
        month = max(g[0] for g in got)
        cand = [g for g in got if g[0] == month]
        votes = Counter(g[1] for g in cand)
        value, n = votes.most_common(1)[0]
        if n < 2 and len(cand) > 1:          # 只有一則、而且跟其他則不同 → 不採用
            continue
        ref, v, t, it = max((g for g in cand if g[1] == value), key=lambda g: g[2])
        out[area] = {"value": value, "month": month.strftime("%Y-%m-%d"), "n": n, "title": it.get("title"),
                     "url": it.get("url") or "", "source": it.get("source") or "", "time": t.strftime("%Y-%m-%d %H:%M")}
    return out


# ---------------------------------------------------------------- 轉換
def transform(s: pd.Series, tf: str) -> pd.Series:
    s = s.dropna()
    if s.empty:
        return s
    if tf == "yoy":
        # 月資料 12 期、季資料 4 期（看日期間隔）
        step = 4 if len(s) > 3 and (s.index[-1] - s.index[-2]).days > 70 else 12
        return (s / s.shift(step) - 1).mul(100).dropna()
    if tf == "chg":
        return s.diff().dropna()
    if tf == "k":
        return s / 1000
    if tf == "ma4":
        return (s.rolling(4).mean() / 1000).dropna()
    return s


def _hyst(x: pd.Series, hi: float, lo: float, on: str, off: str, start: str | None = None) -> pd.Series:
    """遲滯：x > hi → on；x < lo → off；中間維持原狀態。"""
    st, out = start, []
    for v in x.values:
        if np.isnan(v):
            out.append(st)
            continue
        if v > hi:
            st = on
        elif v < lo:
            st = off
        elif st is None:
            st = on if v >= (hi + lo) / 2 else off
        out.append(st)
    return pd.Series(out, index=x.index)


def _confirm(raw: pd.Series, n: int = 2) -> pd.Series:
    """連 n 筆都是新狀態才改變。"""
    st, run, cand, out = None, 0, None, []
    for v in raw.values:
        if v is None:
            out.append(st)
            continue
        if st is None:
            st = v
        elif v != st:
            run = run + 1 if v == cand else 1
            cand = v
            if run >= n:
                st, run, cand = v, 0, None
        else:
            run, cand = 0, None
        out.append(st)
    return pd.Series(out, index=raw.index)


def states(rule: str, x: pd.Series, extra: pd.Series | None = None) -> pd.Series:
    """把整段（轉換後的）歷史算成狀態序列。"""
    x = x.dropna().astype(float)
    if x.empty:
        return pd.Series(dtype=object)
    if rule == "cli":
        d = x.diff()
        sign = pd.Series(np.where(d > 0, "上升", np.where(d < 0, "下降", None)), index=x.index)
        direction = _confirm(sign, 2)
        pos = np.where(x >= 100, "hi", "lo")
        lab = {("hi", "上升"): "擴張", ("hi", "下降"): "趨緩", ("lo", "下降"): "衰退", ("lo", "上升"): "復甦"}
        return pd.Series([lab.get((p, dr)) for p, dr in zip(pos, direction.values)], index=x.index)
    if rule == "curve":
        return _hyst(x.rolling(5, min_periods=1).mean(), 0.05, -0.05, "正常", "倒掛")
    if rule == "claims":
        ratio = x / x.rolling(52, min_periods=26).min()
        return _hyst(ratio, 1.20, 1.10, "上升警訊", "正常").where(ratio.notna())
    if rule == "sahm":
        return _hyst(x, 0.5, 0.3, "觸發", "正常")
    if rule == "cfnai":
        return _hyst(-x.rolling(3).mean(), 0.7, 0.5, "衰退警訊", "正常").where(x.rolling(3).mean().notna())
    if rule == "policy":
        # 政策利率方向：10 日中位數跟 126 個交易日（約 6 個月）前比；中間地帶沿用前一個狀態
        # 6 個月：台灣央行每季才調一次，用 3 個月會在兩次升息之間誤判成「持平」又跳回「升息」
        # 用中位數：2008 年以前 EFFR 每天跳動大（例如雷曼倒閉當天暴衝），平均數會造成假轉折
        m = x.rolling(10, min_periods=1).median()
        chg = m - m.shift(126)
        out, st = [], None
        for v in chg.values:
            if not np.isnan(v):
                if v >= 0.10:
                    st = "升息循環"
                elif v <= -0.10:
                    st = "降息循環"
                elif abs(v) <= 0.03:
                    st = "持平"
            out.append(st)
        return pd.Series(out, index=x.index)
    if rule == "nfci":
        return _hyst(x, 0.05, -0.05, "偏緊", "寬鬆")
    if rule == "credit":
        m = x.resample("D").last().ffill()
        chg = (m - m.shift(91)).reindex(x.index)
        return _hyst(chg, 0.5, 0.25, "擴大", "正常").where(chg.notna())
    if rule in ("level50", "level0"):
        thr = 50.0 if rule == "level50" else 0.0
        raw = pd.Series(np.where(x >= thr, "擴張", "收縮"), index=x.index)
        return _confirm(raw, 1 if rule == "level50" else 2)
    if rule == "yoy":
        ma = x.rolling(3).mean()
        raw = pd.Series(np.where(ma.isna(), None, np.where(ma >= 0, "正成長", "負成長")), index=x.index)
        return _confirm(raw, 2)
    if rule == "light":
        return extra.reindex(x.index) if extra is not None else pd.Series(dtype=object)
    return pd.Series(dtype=object)


TURN_TEXT = {
    "cli": {"擴張": "回到擴張（高於趨勢且上升）", "趨緩": "見頂轉弱（高於趨勢但下降）", "衰退": "跌破趨勢且下降",
            "復甦": "觸底回升（低於趨勢但上升）"},
    "curve": {"倒掛": "殖利率曲線倒掛（歷史上常領先衰退 6～18 個月）", "正常": "解除倒掛（過去常在衰退開始前後）"},
    "claims": {"上升警訊": "比一年低點高 20% 以上（就業轉弱）", "正常": "回落，就業警訊解除"},
    "sahm": {"觸發": "薩姆規則觸發（歷史上表示衰退已開始）", "正常": "薩姆規則解除"},
    "cfnai": {"衰退警訊": "3 個月平均低於 −0.7（歷史上表示衰退可能已開始）", "正常": "回到 −0.5 以上"},
    "nfci": {"偏緊": "金融情勢轉為比平均緊", "寬鬆": "金融情勢轉為比平均寬鬆"},
    "policy": {"升息循環": "政策利率 6 個月內上升 ≥0.10 個百分點（升息）", "降息循環": "政策利率 6 個月內下降 ≥0.10 個百分點（降息）",
               "持平": "政策利率 6 個月內變動在 0.03 以內（政策暫停）"},
    "credit": {"擴大": "3 個月擴大 0.5 個百分點以上（信用市場轉差）", "正常": "利差擴大警訊解除"},
    "level50": {"擴張": "站上 50（擴張）", "收縮": "跌破 50（收縮）"},
    "level0": {"擴張": "連 2 個月轉正", "收縮": "連 2 個月轉負"},
    "yoy": {"正成長": "年增率轉正（3 個月平均，連 2 個月）", "負成長": "年增率轉負（3 個月平均，連 2 個月）"},
}
LIGHT_ORDER = ["藍", "黃藍", "綠", "黃紅", "紅"]


def turn_text(rule: str, prev, cur) -> str:
    if rule == "light":
        a, b = (LIGHT_ORDER.index(prev) if prev in LIGHT_ORDER else -1), (LIGHT_ORDER.index(cur) if cur in LIGHT_ORDER else -1)
        return f"燈號由{prev}燈轉為{cur}燈（景氣{'轉熱' if b > a else '降溫'}）"
    return TURN_TEXT.get(rule, {}).get(cur, f"{prev} → {cur}")


# ---------------------------------------------------------------- 主流程
def _due(src: str, key: str, now: pd.Timestamp, state: dict) -> bool:
    last = (state.get("fetched") or {}).get(key)
    if last is None:
        return True
    age_h = (now.tz_localize(None) - pd.Timestamp(last)).total_seconds() / 3600 if now.tz else (now - pd.Timestamp(last)).total_seconds() / 3600
    if src == "fred" and now.weekday() < 5 and US_WINDOW[0] <= now.hour < US_WINDOW[1]:
        return True
    if src == "ndc" and now.day >= 20 and TW_WINDOW[0] <= now.hour < TW_WINDOW[1]:
        return True
    return age_h >= REFRESH_H.get(src, 6)


def load_store(store: Path) -> pd.DataFrame:
    p = Path(store) / "macro_series.csv.gz"
    if not p.exists():
        return pd.DataFrame(columns=["key", "date", "value"])
    d = pd.read_csv(p, parse_dates=["date"])
    return d


def _series(db: pd.DataFrame, key: str) -> pd.Series:
    d = db[db["key"] == key]
    return pd.Series(d["value"].values, index=pd.to_datetime(d["date"].values)).sort_index()


def refresh(store: Path, state: dict, now: pd.Timestamp, http=None, fetch_news=None, fred_key=None,
            sources: tuple = ("ndc", "fred", "oecd", "news"), only: set | None = None) -> tuple[dict[str, pd.Series], dict, set]:
    """下載到期的資料。回傳 ({key: 原始序列}, {key: 燈號序列等額外資料}, 這次有更新的 key)。
    有版權的序列（public=False）不寫進公開的 events 分支，只在記憶體裡用。"""
    db = load_store(store)
    raw = {k: _series(db, k) for k in db["key"].unique()} if len(db) else {}
    extra, updated = {}, set()
    fetched = state.setdefault("fetched", {})
    stamp = now.strftime("%Y-%m-%d %H:%M")

    if "ndc" in sources and _due("ndc", "ndc", now, state):
        try:
            nd = fetch_ndc(http)
            for c in CATALOG:
                col = c["sid"] if c["sid"] in nd else next((x for x in nd.columns if c["sid"] in x), None)
                if c["src"] == "ndc" and col is not None:
                    raw[c["key"]] = nd[col].dropna()
            if "燈號" in nd:
                extra["tw_light"] = nd["燈號"].dropna()
                state["tw_light_colors"] = {d.strftime("%Y-%m-%d"): v for d, v in nd["燈號"].dropna().iloc[-60:].items()}
            fetched["ndc"] = stamp
            updated |= {c["key"] for c in CATALOG if c["src"] == "ndc"}
        except Exception as e:  # noqa
            log.warning("NDC indicators failed: %s", e)
    # 央行隔夜拆款利率：跟國發會一樣是台灣官方月更新資料 → 跟著 "ndc" 來源一起抓（不另外加來源名稱）
    if "ndc" in sources and _due("cbc", "cbc", now, state):
        try:
            raw["tw_onrate"] = fetch_cbc_overnight(http)
            raw["tw_policy"] = fetch_cbc_policy(http, now.tz_localize(None) if now.tz else now)
            fetched["cbc"] = stamp
            updated |= {"tw_onrate", "tw_policy"}
        except Exception as e:  # noqa
            log.warning("CBC overnight rate failed: %s", e)
    # 紐約聯準銀行（EFFR、SOFR）：免金鑰，完整檢查時抓（Mac 沒有 FRED 金鑰也抓得到）
    if "oecd" in sources or "fred" in sources:
        for c in CATALOG:
            if c["src"] != "nyfed" or not _due("nyfed", c["key"], now, state):
                continue
            try:
                raw[c["key"]] = fetch_nyfed(c["sid"], http=http)
                fetched[c["key"]] = stamp
                updated.add(c["key"])
            except Exception as e:  # noqa
                log.warning("NY Fed %s failed: %s", c["sid"], e)
    if "tw_light" not in extra and state.get("tw_light_colors"):
        lc = state["tw_light_colors"]
        extra["tw_light"] = pd.Series(list(lc.values()), index=pd.to_datetime(list(lc.keys())))

    if "fred" in sources:
        fails = 0
        for c in CATALOG:
            # 有版權的不存檔 → 每次都抓（只抓最近 500 天）
            if c["src"] != "fred" or (only and c["key"] not in only) or (c["public"] and not _due("fred", c["key"], now, state)):
                continue
            try:
                start = "1990-01-01" if c["public"] else str((now - pd.Timedelta(days=500)).date())
                raw[c["key"]] = fetch_fred(c["sid"], start=start, key=fred_key, http=http)
                fetched[c["key"]] = stamp
                updated.add(c["key"])
                fails = 0
            except Exception as e:  # noqa
                log.warning("FRED %s failed: %s", c["sid"], e)
                fails += 1
                if fails >= 3 or "403" in str(e) or "Forbidden" in str(e) or "400" in str(e):
                    log.warning("FRED: stop this run (blocked, bad key, or down)")
                    break                                  # 被擋、金鑰錯或連續失敗：這次不再一檔一檔試

    if "oecd" in sources:
        for src in ("oecd_cli", "oecd_cpi", "oecd_ip", "oecd_une"):
            items = [c for c in CATALOG if c["src"] == src]
            if not items or not _due("oecd", src, now, state):
                continue
            try:
                got = fetch_oecd(src, [c["sid"] for c in items], http=http)
                for c in items:
                    if c["sid"] in got:
                        raw[c["key"]] = got[c["sid"]]
                        updated.add(c["key"])
                fetched[src] = stamp
            except Exception as e:  # noqa
                log.warning("OECD %s failed: %s", src, e)

    if "news" in sources:
        try:
            got = news_pmi(now, fetch=fetch_news)
        except Exception as e:  # noqa
            log.warning("PMI news failed: %s", e)
            got = {}
        pmi = state.setdefault("pmi", {})
        for area, it in got.items():
            key = f"{area}_pmi"
            hist = pmi.setdefault(area, {})
            if hist.get(it["month"]) != it["value"]:
                hist[it["month"]] = it["value"]
                state.setdefault("pmi_src", {})[area] = {k: it[k] for k in ("title", "url", "source", "time")}
                updated.add(key)
            # 只留最近 36 個月
            for m in sorted(hist)[:-36]:
                hist.pop(m)
        for area, hist in pmi.items():
            if hist:
                raw[f"{area}_pmi"] = pd.Series(list(hist.values()), index=pd.to_datetime(list(hist.keys()))).sort_index()

    # 存回（只存可公開的、從 1990 年起）
    keep = []
    for k, s in raw.items():
        c = BY_KEY.get(k)
        if c is None or not c["public"] or s is None or s.empty:
            continue
        s = s[s.index >= "1990-01-01"]
        keep.append(pd.DataFrame({"key": k, "date": s.index, "value": s.values}))
    if keep:
        Path(store).mkdir(parents=True, exist_ok=True)
        pd.concat(keep, ignore_index=True).to_csv(Path(store) / "macro_series.csv.gz", index=False, compression="gzip")
    return raw, extra, updated


def evaluate(raw: dict[str, pd.Series], extra: dict, updated: set, state: dict, now: pd.Timestamp,
             pages_url: str = "") -> tuple[list[dict], list[dict]]:
    """回傳 (事件清單, 網站列)。只有「這次有更新、而且最新一筆的狀態跟前一筆不同」才產生轉折事件。
    第一次（state 裡沒有 last）只建立基準。"""
    last = state.setdefault("last", {})
    baseline = not state.get("baseline_done")
    events, rows = [], []
    stamp = now.strftime("%Y-%m-%d %H:%M")
    for c in CATALOG:
        k = c["key"]
        s = raw.get(k)
        row = {k2: c[k2] for k2 in ("key", "country", "kind", "name", "unit", "level", "public", "rule", "tf")}
        row.update({"country_name": COUNTRIES[c["country"]], "kind_name": KINDS[c["kind"]], "src": c["src"]})
        if s is None or s.empty:
            row.update(state.get("private", {}).get(k, {}))      # 有版權、這次沒抓：沿用上次的狀態
            rows.append(row)
            continue
        x = transform(s, c["tf"])
        if x.empty:
            rows.append(row)
            continue
        st = states(c["rule"], x, extra.get(k)) if c["rule"] else pd.Series(dtype=object)
        st = st.dropna() if len(st) else st
        d_last = x.index[-1]
        row.update({"date": d_last.strftime("%Y-%m-%d"), "value": round(float(x.iloc[-1]), 3) if c["public"] else None,
                    "prev": round(float(x.iloc[-2]), 3) if c["public"] and len(x) > 1 else None,
                    "up": bool(x.iloc[-1] > x.iloc[-2]) if len(x) > 1 else None})
        if c["public"]:
            gap = float(np.median(np.diff(x.index[-30:].values).astype("timedelta64[D]").astype(float))) if len(x) > 30 else 30
            sp = x.resample("W").last().dropna().iloc[-104:] if gap < 5 else x.iloc[-104:] if gap < 20 else x.iloc[-60:]
            row["spark"] = [[d.strftime("%Y-%m-%d"), round(float(v), 3)] for d, v in sp.items()]
        if len(st):
            row["state"] = st.iloc[-1]
            ch = st[st != st.shift(1)].iloc[1:]
            row["turns"] = [{"date": d.strftime("%Y-%m-%d"), "to": v} for d, v in ch.iloc[-6:].items()]
            if c["rule"] == "light":
                row["state"] = f"{st.iloc[-1]}燈"
        if not c["public"]:                                      # 有版權的不存數值，只記狀態（給下次沒抓到時用）
            state.setdefault("private", {})[k] = {x: row[x] for x in ("date", "state", "turns", "up") if x in row}
        rows.append(row)

        prev_last = last.get(k)
        last[k] = row["date"]
        if baseline or k not in updated or prev_last == row["date"]:
            continue
        # 重要指標公布新的一期 →「注意」（每日彙整＋網站；使用者要求公布本身不立刻通知）
        if c["level"] == "重大" and c["rule"] not in ("curve", "credit", "nfci"):
            pv = f"（前值 {_fmt(x.iloc[-2])}）" if c["public"] and len(x) > 1 else ""
            now_v = f"{_fmt(x.iloc[-1])}" if c["public"] else "已更新"
            events.append({"kind": "macro_rel", "topic": f"macrorel_{k}_{row['date']}", "label": f"指標公布：{c['name']}",
                           "category": "總經指標", "level": "注意", "time": stamp,
                           "title": f"{COUNTRIES[c['country']]} {c['name']} 公布 {d_last.strftime('%Y/%m')}：{now_v}{pv}"
                                    + (f"，狀態：{row['state']}" if row.get("state") else ""),
                           "source": {"ndc": "國發會", "fred": "FRED", "news": "新聞"}.get(c["src"], "OECD"),
                           "url": f"{pages_url}/#macro" if pages_url else ""})
        if len(st) < 2:
            continue
        if st.index[-1] != d_last or st.iloc[-1] == st.iloc[-2]:
            continue
        msg = turn_text(c["rule"], st.iloc[-2], st.iloc[-1])
        val = f"（{_fmt(x.iloc[-1])} {c['unit'].split('，')[0]}）" if c["public"] else ""
        daily = c["rule"] in ("curve", "credit", "nfci", "claims", "policy")
        when = d_last.strftime("%Y/%m/%d" if daily else "%Y/%m")
        title = f"{COUNTRIES[c['country']]}{KINDS[c['kind']]}指標轉折：{c['name']} {msg}{val}，資料月份 {when}"
        src = state.get("pmi_src", {}).get(c["sid"]) if c["src"] == "news" else None
        events.append({"kind": "macro", "topic": f"macro_{k}_{row['date']}_{st.iloc[-1]}", "label": f"總經轉折：{c['name']}",
                       "category": "總經指標", "level": c["level"] or "注意", "time": stamp, "title": title,
                       "source": (src or {}).get("source") or {"ndc": "國發會", "fred": "FRED", "news": "新聞"}.get(c["src"], "OECD"),
                       "url": (src or {}).get("url") or (f"{pages_url}/#macro" if pages_url else "")})
    state["baseline_done"] = True
    return events, rows


def _fmt(v: float) -> str:
    return f"{v:,.0f}" if abs(v) >= 1000 else f"{v:.2f}" if abs(v) < 10 else f"{v:.1f}"


def summary(rows: list[dict]) -> dict:
    """每個國家：領先指標裡「轉弱」的比例（只算有規則的）。給網站總覽用。"""
    bad = {"趨緩", "衰退", "倒掛", "上升警訊", "觸發", "衰退警訊", "偏緊", "擴大", "收縮", "負成長", "藍燈", "黃藍燈"}
    out = {}
    for cc, name in COUNTRIES.items():
        r = [x for x in rows if x["country"] == cc and x.get("state") and x["kind"] == "lead"]
        if r:
            out[cc] = {"name": name, "weak": sum(x["state"] in bad for x in r), "n": len(r)}
    return out


def check(store: Path, state: dict, now: pd.Timestamp, pages_url: str = "", http=None, fetch_news=None,
          fred_key=None, sources: tuple = ("ndc", "fred", "oecd", "news"),
          only: set | None = None) -> tuple[list[dict], dict]:
    """下載＋判斷＋寫網站資料（store/macro.json）。state＝market_events state 裡的 "macro" 子字典。"""
    t0 = time.time()
    raw, extra, updated = refresh(store, state, now, http=http, fetch_news=fetch_news, fred_key=fred_key, sources=sources, only=only)
    events, rows = evaluate(raw, extra, updated, state, now, pages_url)
    site = {"updated": now.strftime("%Y-%m-%d %H:%M"), "summary": summary(rows), "rows": rows,
            "pmi_src": state.get("pmi_src", {}),
            "note": "轉折訊號還沒回測過，只顯示、不影響選股與進場。PMI 從新聞標題讀數字，可能有誤。"}
    Path(store).mkdir(parents=True, exist_ok=True)
    (Path(store) / "macro.json").write_text(json.dumps(site, ensure_ascii=False, default=str))
    log.info("macro indicators: %d updated, %d turning events, %.0fs", len(updated), len(events), time.time() - t0)
    return events, site
