"""台股籌碼：三大法人買賣超、融資融券餘額（證交所、櫃買中心每日公布的官方資料）。

快取 cache/tw_chips.csv.gz：date, id, foreign, trust, dealer, total（買賣超股數）, margin, short（融資、融券餘額，張）
每天抓最新一天；證交所可指定日期，第一次往回補約 60 個交易日（每次最多 backfill 天）。
只用在網站顯示與 K 線副圖，不影響選股。
"""
from __future__ import annotations

import json
import re
import time

import numpy as np
import pandas as pd
import requests

from .common import CACHE_DIR, log

PATH = CACHE_DIR / "tw_chips.csv.gz"
STATE = CACHE_DIR / "tw_chips_state.json"
COLS = ["date", "id", "foreign", "trust", "dealer", "total", "margin", "short"]
UA = {"User-Agent": "Mozilla/5.0 (stock-screener; personal research)"}

TWSE_T86 = "https://www.twse.com.tw/rwd/zh/fund/T86?date={d}&selectType=ALLBUT0999&response=json"
TWSE_MARGIN = "https://www.twse.com.tw/rwd/zh/marginTrading/MI_MARGN?date={d}&selectType=ALL&response=json"
# 櫃買中心：新舊網址都試（2025 年改版後網址可能變動）
TPEX_INSTI = ["https://www.tpex.org.tw/web/stock/3insti/daily_trade/3itrade_hedge_result.php?l=zh-tw&o=json&se=EW&t=D&d={roc}",
              "https://www.tpex.org.tw/www/zh-tw/insti/dailyTrade?type=Daily&sect=EW&date={slash}&response=json",
              "https://www.tpex.org.tw/openapi/v1/tpex_3insti_daily_trading"]
TPEX_MARGIN = ["https://www.tpex.org.tw/web/stock/margin_trading/margin_balance/margin_bal_result.php?l=zh-tw&o=json&d={roc}",
               "https://www.tpex.org.tw/www/zh-tw/margin/balance?date={slash}&response=json",
               "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_margin_balance"]


def _num(x):
    try:
        return float(str(x).replace(",", "").strip() or "nan")
    except ValueError:
        return np.nan


def _get(url: str, http=None):
    http = http or requests
    try:
        r = http.get(url, timeout=40, headers=UA)
        if r.status_code != 200:
            return None
        return r.json()
    except Exception as e:  # noqa
        log.debug("chips %s: %s", url, e)
        return None


def _tables(js) -> list[tuple[list, list]]:
    """把各種回傳格式整理成 [(欄位, 資料列)]。"""
    out = []
    if isinstance(js, list) and js and isinstance(js[0], dict):        # OpenAPI：[{欄位: 值}]
        keys = list(js[0].keys())
        out.append((keys, [[r.get(k) for k in keys] for r in js]))
    elif isinstance(js, dict):
        if js.get("fields") and js.get("data"):
            out.append((js["fields"], js["data"]))
        for t in js.get("tables") or []:
            if t.get("fields") and t.get("data"):
                out.append((t["fields"], t["data"]))
        if js.get("aaData"):
            out.append(([], js["aaData"]))
    return out


def _find(fields: list, *must, avoid=()) -> int | None:
    for i, f in enumerate(fields):
        s = str(f)
        if all(m in s for m in must) and not any(a in s for a in avoid):
            return i
    return None


def _first(*xs):
    return next((x for x in xs if x is not None), None)


def parse_insti(js) -> pd.DataFrame:
    """三大法人買賣超（股）。"""
    for fields, data in _tables(js):
        if not data:
            continue
        if fields and len(fields) >= 24 and str(fields[0]).strip() == "代號" and _find(fields, "外資") is None \
                and _find(fields, "Foreign") is None:
            # 櫃買新版（2026）：欄位名稱只剩「買進股數／賣出股數／買賣超股數」重複，外資、投信、自營的分組標題不見了，
            # 只能照位置對：外資(不含外資自營商) 2-4、外資自營商 5-7、外資合計 8-10、投信 11-13、
            # 自營商(自行) 14-16、自營商(避險) 17-19、自營商合計 20-22、三大法人合計 23（跟舊版 aaData 同順序）
            ic, fi, ti, di, to = 0, 4, 13, 22, 23
        elif fields:
            ic = _first(_find(fields, "代號"), _find(fields, "Code"))
            fi = _first(_find(fields, "外陸資買賣超"), _find(fields, "外資及陸資", "買賣超"),
                        _find(fields, "外資", "買賣超", avoid=("外資自營商買", "自營商買賣超")),
                        _find(fields, "Foreign Dealers excluded", "Difference"),       # 櫃買 OpenAPI（英文欄位）
                        _find(fields, "外資及陸資", "淨買"))                             # 櫃買 2018 年以前：「淨買股數」
            ti = _first(_find(fields, "投信", "買賣超"), _find(fields, "InvestmentTrust", "Difference"), _find(fields, "投信", "淨買"))
            di = _first(_find(fields, "自營商買賣超", avoid=("外資自營商", "自行", "避險")),
                        _find(fields, "Dealers-Difference", avoid=("Foreign",)), _find(fields, "自營淨買"))
            to = _first(_find(fields, "三大法人", "買賣超"), _find(fields, "合計", "買賣超"), _find(fields, "TotalDifference"))
        else:                                   # 櫃買舊版 aaData：代號、名稱、外資買進、賣出、買賣超、…
            ic, fi, ti, di, to = 0, 4, 13, 22, 23
        if ic is None or fi is None:
            continue
        rows = []
        for r in data:
            try:
                sid = str(r[ic]).strip()
                if not re.fullmatch(r"\d{4}[A-Z]?", sid):
                    continue
                rows.append({"id": sid, "foreign": _num(r[fi]), "trust": _num(r[ti]) if ti is not None else np.nan,
                             "dealer": _num(r[di]) if di is not None else np.nan, "total": _num(r[to]) if to is not None else np.nan})
            except (IndexError, TypeError):
                continue
        if rows:
            d = pd.DataFrame(rows)
            d["total"] = d["total"].fillna(d[["foreign", "trust", "dealer"]].sum(axis=1, min_count=1))
            return d
    return pd.DataFrame(columns=["id", "foreign", "trust", "dealer", "total"])


def parse_margin(js) -> pd.DataFrame:
    """融資、融券今日餘額（張）。證交所的個股表：代號、名稱、融資 買進…今日餘額（第 7 欄）…融券 今日餘額（第 13 欄）。"""
    for fields, data in _tables(js):
        if not data:
            continue
        if fields and len(fields) >= 13 and str(fields[6]).startswith("今日餘額") and str(fields[12]).startswith("今日餘額"):
            ic, mi, si = 0, 6, 12
        elif fields:
            ic = _first(_find(fields, "代號"), _find(fields, "Code"))
            # 證交所：「融資…餘額」；櫃買新版：「資餘額」「券餘額」（要避開「前資餘額」）；櫃買 OpenAPI：英文欄位
            mi = _first(_find(fields, "融資", "餘額", avoid=("前日", "限額")), _find(fields, "資餘額", avoid=("前",)),
                        _find(fields, "MarginPurchaseBalance", avoid=("Previous", "Belong")))
            si = _first(_find(fields, "融券", "餘額", avoid=("前日", "限額")), _find(fields, "券餘額", avoid=("前",)),
                        _find(fields, "ShortSaleBalance", avoid=("Previous", "Belong")))
            if mi is None or si is None:
                continue
        else:                                   # 櫃買舊版 aaData：代號、名稱、前資餘額、資買、資賣、現償、資餘額…
            ic, mi, si = 0, 6, 14
        if ic is None:
            continue
        rows = []
        for r in data:
            try:
                sid = str(r[ic]).strip()
                if re.fullmatch(r"\d{4}[A-Z]?", sid):
                    rows.append({"id": sid, "margin": _num(r[mi]), "short": _num(r[si])})
            except (IndexError, TypeError):
                continue
        if rows:
            return pd.DataFrame(rows)
    return pd.DataFrame(columns=["id", "margin", "short"])


NO_DATA = "no_data"


def fetch_day(day: pd.Timestamp, http=None, sleep: float = 3.0):
    """抓某一天的上市＋上櫃籌碼。回傳 DataFrame；證交所說這天沒有資料（假日或還沒公布）回傳 NO_DATA；連不上回傳 None。"""
    d8, roc = day.strftime("%Y%m%d"), f"{day.year - 1911}/{day:%m/%d}"
    slash = day.strftime("%Y/%m/%d")
    js = _get(TWSE_T86.format(d=d8), http)
    if not js:
        return None
    if isinstance(js, dict) and js.get("stat") not in (None, "OK"):
        return NO_DATA
    ins = [parse_insti(js)]
    time.sleep(sleep)
    mar = [parse_margin(_get(TWSE_MARGIN.format(d=d8), http))]
    for urls, parser, out in ((TPEX_INSTI, parse_insti, ins), (TPEX_MARGIN, parse_margin, mar)):
        for u in urls:
            if "openapi" in u and day.normalize() != pd.Timestamp.today().normalize():
                continue                         # OpenAPI 只有最新一天
            time.sleep(sleep)
            d = parser(_get(u.format(roc=roc, slash=slash), http))
            if len(d):
                out.append(d)
                break
    a = pd.concat([x for x in ins if len(x)], ignore_index=True) if any(len(x) for x in ins) else pd.DataFrame(columns=["id"])
    b = pd.concat([x for x in mar if len(x)], ignore_index=True) if any(len(x) for x in mar) else pd.DataFrame(columns=["id"])
    if a.empty and b.empty:
        return None
    m = a.drop_duplicates("id").merge(b.drop_duplicates("id"), on="id", how="outer")
    m.insert(0, "date", day.normalize())
    for c in COLS:
        if c not in m:
            m[c] = np.nan
    return m[COLS]


def load() -> pd.DataFrame:
    if not PATH.exists():
        return pd.DataFrame(columns=COLS)
    return pd.read_csv(PATH, parse_dates=["date"], dtype={"id": str})


def update(today=None, keep_days: int = 130, backfill: int = 8, http=None, sleep: float = 3.0) -> dict:
    """抓最新一天＋往回補缺的交易日（每次最多 backfill 天）。"""
    today = pd.Timestamp(today or pd.Timestamp.now(tz="Asia/Taipei").tz_localize(None)).normalize()
    df = load()
    st = json.loads(STATE.read_text()) if STATE.exists() else {"holiday": []}
    have = set(df["date"].dt.strftime("%Y-%m-%d")) if len(df) else set()
    hol = set(st.get("holiday", []))
    # 收盤後才會有當天資料（約 15:00～21:00 公布）；早上跑就抓前一個交易日
    days = [d for d in pd.bdate_range(today - pd.Timedelta(days=90), today)[::-1]
            if d.strftime("%Y-%m-%d") not in have and d.strftime("%Y-%m-%d") not in hol]
    got, tried, fails = [], 0, 0
    for d in days:
        if tried >= backfill or fails >= 3:      # 連續連不上 3 次就先停，明天再試
            break
        tried += 1
        x = fetch_day(d, http, sleep)
        if x is None:
            fails += 1
            continue
        fails = 0
        if isinstance(x, str):
            if d < today - pd.Timedelta(days=3):     # 證交所說三天前沒有資料 → 假日（連不上的日子不算，之後會再試）
                hol.add(d.strftime("%Y-%m-%d"))
            continue
        got.append(x)
        time.sleep(sleep)
    if got:
        df = pd.concat([df] + got, ignore_index=True).drop_duplicates(["date", "id"], keep="last")
    df = df[df["date"] >= today - pd.Timedelta(days=keep_days * 7 // 5 + 10)]
    PATH.parent.mkdir(parents=True, exist_ok=True)
    df.sort_values(["date", "id"]).to_csv(PATH, index=False, compression="gzip")
    STATE.write_text(json.dumps({"holiday": sorted(hol)[-200:]}))
    out = {"days_fetched": [str(x["date"].iloc[0].date()) for x in got], "days_total": int(df["date"].nunique()) if len(df) else 0,
           "stocks": int(df["id"].nunique()) if len(df) else 0}
    log.info("chips: %s", out)
    return out


def summary(df: pd.DataFrame | None = None) -> dict:
    """每檔近 5／20 個交易日的法人買賣超（張）與融資券變化。"""
    df = load() if df is None else df
    if df.empty:
        return {"as_of": None, "rows": {}}
    days = sorted(df["date"].unique())
    last = days[-1]
    d5, d20 = set(days[-5:]), set(days[-20:])
    out = {}
    for sid, g in df.groupby("id"):
        g = g.sort_values("date")
        s5, s20 = g[g["date"].isin(d5)], g[g["date"].isin(d20)]
        r = {k + "5": _lots(s5[k].sum(min_count=1)) for k in ("foreign", "trust", "dealer", "total")}
        r.update({k + "20": _lots(s20[k].sum(min_count=1)) for k in ("foreign", "trust", "dealer", "total")})
        cur = g[g["date"] == last]
        if len(cur):
            m, s = cur["margin"].iloc[0], cur["short"].iloc[0]
            r["margin"] = None if pd.isna(m) else int(m)
            r["short"] = None if pd.isna(s) else int(s)
            first = s20.dropna(subset=["margin"])
            if len(first) and first["margin"].iloc[0] and not pd.isna(m):
                r["margin_chg20"] = round(float(m / first["margin"].iloc[0] - 1), 4)
            r["short_ratio"] = round(float(s / m), 4) if m and not pd.isna(s) else None
        out[sid] = {k: v for k, v in r.items() if v is not None}
    return {"as_of": str(pd.Timestamp(last).date()), "days": len(days), "rows": out}


def _lots(x):
    return None if x is None or pd.isna(x) else int(round(float(x) / 1000))


def series(df: pd.DataFrame | None = None, days: int = 130) -> dict:
    """K 線副圖用：每檔每天的法人買賣超（張）與融資、融券餘額。"""
    df = load() if df is None else df
    if df.empty:
        return {}
    keep = sorted(df["date"].unique())[-days:]
    df = df[df["date"].isin(keep)]
    out = {}
    for sid, g in df.groupby("id"):
        g = g.sort_values("date")
        t = g["date"].values.astype("datetime64[D]").astype(np.int64).tolist()
        lots = lambda c: [None if pd.isna(x) else int(round(x / 1000)) for x in g[c]]  # noqa: E731
        ints = lambda c: [None if pd.isna(x) else int(x) for x in g[c]]  # noqa: E731
        out[sid] = {"t": t, "f": lots("foreign"), "i": lots("trust"), "d": lots("dealer"), "tot": lots("total"),
                    "mb": ints("margin"), "sb": ints("short")}
    return out
