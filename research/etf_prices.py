"""【2026/11/08】0050、00631L 官方每日行情與除息（證交所），自己做含息還原並檢查分割。

為什麼不用 Yahoo：00631L 早期價格錯（2015-01 官方 ÷ Yahoo ＝ 24.6～25.7，2020 年後才是分割比 22），
而且 Yahoo 沒有記錄 0050 的 1 拆 4、00631L 的 1 拆 22 分割事件。
資料：證交所 STOCK_DAY（每月一次請求：日期、開盤、收盤…，未還原）、TWT49U（除權息計算結果，每年一次請求）。
輸出：data/etf/{代號}_daily.csv、data/etf/dividends.csv（data/ 不上傳）；可中斷、重跑會接續。
執行：.venv/bin/python research/etf_prices.py
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _paths import ROOT  # noqa: E402

import pandas as pd  # noqa: E402
import requests  # noqa: E402

OUT = ROOT / "data" / "etf"
CODES = {"0050": "2009-01", "00631L": "2014-10"}
SLEEP = 4.0
H = {"User-Agent": "Mozilla/5.0 (personal research)"}
DAY = "https://www.twse.com.tw/rwd/zh/afterTrading/STOCK_DAY?date={d}&stockNo={c}&response=json"
DIV = "https://www.twse.com.tw/rwd/zh/exRight/TWT49U?startDate={a}&endDate={b}&response=json"
DAILY_ALL = "https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX?date={d}&type=ALLBUT0999&response=json"
MONTHLY_FROM = pd.Period("2010-01", "M")      # STOCK_DAY 只能查 2010-01-04 以後；更早用每日全部行情


def roc(s: str) -> pd.Timestamp:
    y, m, d = s.replace("年", "/").replace("月", "/").replace("日", "").split("/")
    return pd.Timestamp(int(y) + 1911, int(m), int(d))


def num(x):
    try:
        return float(str(x).replace(",", ""))
    except ValueError:
        return None


def get(url):
    for i in range(4):
        try:
            r = requests.get(url, headers=H, timeout=40)
            if r.status_code == 200:
                return r.json()
        except Exception:  # noqa
            pass
        time.sleep(10 * (i + 1))
    raise RuntimeError(url)


def fetch_prices(code: str, start: str):
    path = OUT / f"{code}_daily.csv"
    old = pd.read_csv(path, parse_dates=["date"]) if path.exists() else pd.DataFrame(columns=["date"])
    have = set(old["date"].dt.to_period("M")) if len(old) else set()
    months = pd.period_range(start, pd.Timestamp.today().to_period("M"), freq="M")
    rows = []
    for m in months:
        if m in have and m != months[-1]:
            continue
        if m < MONTHLY_FROM:
            rows += daily_all(code, m)
            save(path, old, rows)
            continue
        js = get(DAY.format(d=f"{m.year}{m.month:02d}01", c=code))
        time.sleep(SLEEP)
        f = js.get("fields") or []
        for r in js.get("data") or []:
            rows.append({"date": roc(r[0]), "open": num(r[f.index("開盤價")]), "close": num(r[f.index("收盤價")]),
                         "volume": num(r[f.index("成交股數")])})
        print(code, m, len(js.get("data") or []), flush=True)
        save(path, old, rows)
    return save(path, old, rows)


def save(path, old, rows):
    """每抓完一個月就存檔：斷線重跑時從下一個月接續。"""
    al = pd.concat([old, pd.DataFrame(rows)], ignore_index=True) if rows else old
    if len(al):
        al = al.drop_duplicates("date", keep="last").sort_values("date")
        al.to_csv(path, index=False)
    return al


def daily_all(code: str, m: pd.Period) -> list[dict]:
    """2009 年：逐日抓證交所全部收盤行情，取出這一檔。"""
    out = []
    for day in pd.bdate_range(m.start_time, m.end_time):
        js = get(DAILY_ALL.format(d=day.strftime("%Y%m%d")))
        time.sleep(SLEEP)
        if js.get("stat") != "OK":
            continue
        for t in js.get("tables", []):
            f = [str(x).strip() for x in (t.get("fields") or [])]
            if "證券代號" in f and "收盤價" in f:
                for r in t.get("data", []):
                    if str(r[f.index("證券代號")]).strip() == code:
                        out.append({"date": day, "open": num(r[f.index("開盤價")]), "close": num(r[f.index("收盤價")]),
                                    "volume": num(r[f.index("成交股數")])})
    print(code, m, "（逐日）", len(out), flush=True)
    return out


def fetch_dividends():
    rows = []
    for y in range(2009, pd.Timestamp.today().year + 1):
        js = get(DIV.format(a=f"{y}0101", b=f"{y}1231"))
        time.sleep(SLEEP)
        for r in js.get("data") or []:
            if r[1].strip() in CODES:
                rows.append({"code": r[1].strip(), "date": roc(r[0]), "prev_close": num(r[3]), "ref": num(r[4]),
                             "cash": num(r[5]), "type": r[6]})
        print("dividends", y, flush=True)
    d = pd.DataFrame(rows)
    d.to_csv(OUT / "dividends.csv", index=False)
    return d


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    for c, s in CODES.items():
        fetch_prices(c, s)
    fetch_dividends()
    print("完成")


if __name__ == "__main__":
    main()
