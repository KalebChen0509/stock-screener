"""【2026/11/08】用證交所、櫃買中心官方數據推估「融資維持率」（上市、上櫃），2009 年起每日。

推估公式（民間平台常用的算法）：
    融資維持率 ≈ Σ(各股融資餘額張數 × 1000 × 當日收盤價) ÷ 融資金額合計
    （融資金額＝投資人跟券商借的錢；分子＝擔保品現值。沒有成交的股票沿用最近一次收盤價。）
限制：證交所說明實際維持率是「整戶」計算（含融券、其他擔保品），這只是推估值；也不含融資自備款以外的資產。

資料（每個交易日 4 次請求，間隔 SLEEP 秒）：
    證交所 MI_MARGN（各股融資餘額＋「融資金額(仟元)」今日餘額）、MI_INDEX（每日收盤行情）
    櫃買 margin/balance（各股資餘額＋「融資金(仟元)」合計）、afterTrading/otc（每日收盤行情）
輸出：data/margin_ratio/twse.csv、tpex.csv（date, ratio, 分子 value, 分母 loan；data/ 不上傳）；可中斷、重跑會接續。
上市、上櫃分開跑（不同網站，可以同時跑）：
    .venv/bin/python research/margin_ratio.py --market tpex
    .venv/bin/python research/margin_ratio.py --market twse
驗證：--check 2026-11-06（MacroMicro 當天：上市 151.92%、上櫃 143.86%）
"""
import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _paths import ROOT  # noqa: E402

import pandas as pd  # noqa: E402
import requests  # noqa: E402

OUTDIR = ROOT / "data" / "margin_ratio"
SLEEP = 1.5
H = {"User-Agent": "Mozilla/5.0 (personal research)"}
TWSE_MARGIN = "https://www.twse.com.tw/rwd/zh/marginTrading/MI_MARGN?date={d}&selectType=ALL&response=json"
TWSE_CLOSE = "https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX?date={d}&type=ALLBUT0999&response=json"
TPEX_MARGIN = "https://www.tpex.org.tw/www/zh-tw/margin/balance?date={s}&response=json"
TPEX_CLOSE = "https://www.tpex.org.tw/www/zh-tw/afterTrading/otc?date={s}&type=EW&response=json"


def num(x):
    try:
        return float(str(x).replace(",", "").strip())
    except ValueError:
        return None


def get(url, tries=3):
    for i in range(tries):
        try:
            r = requests.get(url, headers=H, timeout=40)
            if r.status_code == 200:
                return r.json()
        except Exception:  # noqa
            pass
        time.sleep(5 * (i + 1))
    raise RuntimeError(f"下載失敗：{url}")


def twse_day(day: pd.Timestamp):
    """→ ({代號: 融資餘額張數}, 融資金額元) ；非交易日回傳 None。"""
    js = get(TWSE_MARGIN.format(d=day.strftime("%Y%m%d")))
    if js.get("stat") != "OK":
        return None
    lots, amount = {}, None
    for t in js.get("tables", []):
        f = t.get("fields") or []
        if "項目" in f:
            for row in t.get("data", []):
                if "融資金額" in row[0]:
                    amount = num(row[f.index("今日餘額")]) * 1000
        elif "代號" in f:
            # 欄位：代號、名稱、(融資)買進、賣出、現金償還、前日餘額、今日餘額 …（前半段是融資）
            i = f.index("今日餘額")
            for row in t.get("data", []):
                v = num(row[i])
                if v:
                    lots[row[0].strip()] = v
    return (lots, amount) if lots and amount else None


def tpex_day(day: pd.Timestamp):
    js = get(TPEX_MARGIN.format(s=day.strftime("%Y/%m/%d")))
    tabs = js.get("tables") or []
    if not tabs or not tabs[0].get("data"):
        return None
    t = tabs[0]
    roc = t.get("date", "")
    if roc and int(roc.split("/")[0]) + 1911 != day.year:       # 回傳的不是那一天（例如非交易日回最新）
        return None
    f = t["fields"]
    i = f.index("資餘額")
    lots = {row[0].strip(): num(row[i]) for row in t["data"] if num(row[i])}
    amount = None
    for row in t.get("summary") or []:
        if any("融資金" in str(c) for c in row):
            amount = num(row[i]) * 1000
    return (lots, amount) if lots and amount else None


def closes(url, day_key):
    js = get(url)
    out = {}
    for t in js.get("tables", []):
        f = [str(x).strip() for x in (t.get("fields") or [])]
        if "收盤價" in f or "收盤" in f:
            ci = f.index("收盤價") if "收盤價" in f else f.index("收盤")
            ki = f.index("證券代號") if "證券代號" in f else f.index("代號")
            for row in t.get("data", []):
                v = num(row[ci])
                if v and v > 0:
                    out[str(row[ki]).strip()] = v
    return out


def ratio(lots: dict, amount: float, px: dict, last: dict):
    val, miss = 0.0, 0
    for k, n in lots.items():
        p = px.get(k, last.get(k))
        if p is None:
            miss += 1
            continue
        val += n * 1000 * p
    return val / amount * 100, val, miss


def run_day(day: pd.Timestamp, last: dict, market: str) -> dict | None:
    """market＝twse（上市）或 tpex（上櫃）；非交易日回傳 None。"""
    if market == "twse":
        m = twse_day(day)
        time.sleep(SLEEP)
        if m is None:
            return None
        px = closes(TWSE_CLOSE.format(d=day.strftime("%Y%m%d")), day)
    else:
        m = tpex_day(day)
        time.sleep(SLEEP)
        if m is None:
            return None
        px = closes(TPEX_CLOSE.format(s=day.strftime("%Y/%m/%d")), day)
    time.sleep(SLEEP)
    last.update(px)
    r, v, miss = ratio(m[0], m[1], px, last)
    return {"date": day.strftime("%Y-%m-%d"), "ratio": round(r, 3), "value": round(v), "loan": round(m[1]),
            "n": len(m[0]), "missing_px": miss, "n_px": len(px)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--market", choices=["twse", "tpex"], required=True)
    ap.add_argument("--start", default="2009-01-01")
    ap.add_argument("--end", default=None)
    ap.add_argument("--check", help="只算這一天、不存檔（驗證算法用）")
    a = ap.parse_args()
    if a.check:
        print(run_day(pd.Timestamp(a.check), {}, a.market))
        return
    OUTDIR.mkdir(parents=True, exist_ok=True)
    out, cpath = OUTDIR / f"{a.market}.csv", OUTDIR / f"{a.market}_last_close.csv"
    done = pd.read_csv(out) if out.exists() else pd.DataFrame(columns=["date"])
    last = dict(pd.read_csv(cpath, dtype={"id": str}).values) if cpath.exists() else {}
    start = pd.Timestamp(done["date"].max()) + pd.Timedelta(days=1) if len(done) else pd.Timestamp(a.start)
    end = pd.Timestamp(a.end) if a.end else pd.Timestamp.today().normalize()
    rows, t0 = [], time.time()

    def save():
        nonlocal done, rows
        if rows:
            done = pd.concat([done, pd.DataFrame(rows)], ignore_index=True)
            done.to_csv(out, index=False)
            pd.DataFrame(list(last.items()), columns=["id", "close"]).to_csv(cpath, index=False)
            rows = []

    for day in pd.bdate_range(start, end):
        try:
            row = run_day(day, last, a.market)
        except Exception as e:  # noqa
            print(f"{day:%Y-%m-%d} 失敗，存檔後停止（重跑會接續）：{e}", flush=True)
            save()
            sys.exit(1)
        if row:
            rows.append(row)
            if len(rows) >= 20:
                print(f"{a.market} {row['date']} {row['ratio']:.1f}%（{time.time() - t0:.0f}s）", flush=True)
                save()
    save()
    print("完成", out, len(done), "天")


if __name__ == "__main__":
    main()
