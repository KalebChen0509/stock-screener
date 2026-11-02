"""檢查外部免費資料來源還能不能用（證交所、櫃買中心、Yahoo 分析師預估、Google 新聞、Yahoo 盤中報價）。

GitHub → Actions →「除錯」→ Run workflow → mode 選 probe。結果印在執行紀錄裡，也存到 debug 分支的 probe.txt。
只抓公開市場資料，不需要任何金鑰。
"""
import os
import sys
import time
import traceback
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd  # noqa: E402

from screener import chips, estimates, events  # noqa: E402

OUT = []


def say(s=""):
    print(s, flush=True)
    OUT.append(str(s))


def check(name, fn):
    t0 = time.time()
    try:
        r = fn()
        bad = r in (None, [], {}) or (isinstance(r, str) and (r.startswith("連不上") or r == chips.NO_DATA or " 解析 0 檔" in r))
        say(f"{'⚠️' if bad else '✔'} {name}（{time.time() - t0:.1f}s）：{r}")
    except Exception as e:  # noqa
        say(f"✘ {name}：{e.__class__.__name__}: {e}")
        say("   " + traceback.format_exc().strip().splitlines()[-2][:200])


def last_bday():
    d = pd.Timestamp.now(tz="Asia/Taipei").tz_localize(None).normalize()
    if pd.Timestamp.now(tz="Asia/Taipei").hour < 17:
        d -= pd.Timedelta(days=1)
    while d.weekday() >= 5:
        d -= pd.Timedelta(days=1)
    return d


def main():
    day = last_bday()
    d8, roc, slash = day.strftime("%Y%m%d"), f"{day.year - 1911}/{day:%m/%d}", day.strftime("%Y/%m/%d")
    say(f"== 台股籌碼（{day.date()}）==")
    for name, url, parser in [("證交所 三大法人", chips.TWSE_T86.format(d=d8), chips.parse_insti),
                              ("證交所 融資融券", chips.TWSE_MARGIN.format(d=d8), chips.parse_margin)] + \
                             [(f"櫃買 三大法人 #{i}", u.format(roc=roc, slash=slash), chips.parse_insti) for i, u in enumerate(chips.TPEX_INSTI)] + \
                             [(f"櫃買 融資融券 #{i}", u.format(roc=roc, slash=slash), chips.parse_margin) for i, u in enumerate(chips.TPEX_MARGIN)]:
        def f(url=url, parser=parser):
            js = chips._get(url)
            if js is None:
                return "連不上或不是 JSON"
            d = parser(js)
            stat = js.get("stat") if isinstance(js, dict) else f"list[{len(js)}]"
            keys = list(js.keys())[:8] if isinstance(js, dict) else list(js[0].keys())[:8] if js else []
            sample = d.iloc[0].to_dict() if len(d) else None
            return f"stat={stat} 解析 {len(d)} 檔；欄位 {keys}；第一筆 {sample}"
        check(name, f)
        time.sleep(2)
    check("fetch_day 合併", lambda: (lambda x: x if isinstance(x, str) or x is None else
                                     f"{len(x)} 檔；2330 {x[x.id == '2330'].to_dict('records')[:1]}；6488 {x[x.id == '6488'].to_dict('records')[:1]}")(chips.fetch_day(day, sleep=2)))

    say("\n== 美股分析師預估（Yahoo）==")
    check("NVDA", lambda: {k: (v if k != "eps" else list(v)) for k, v in (estimates.one("NVDA") or {}).items()})

    say("\n== Google 新聞 ==")
    check("NVIDIA（英文）", lambda: [x["title"][:40] for x in events.google_news("NVIDIA", limit=3)])
    check("台積電（中文）", lambda: [x["title"][:30] for x in events.google_news("台積電", lang="zh", limit=3)])

    say("\n== 盤中報價（Yahoo，提醒用）==")
    def yq():
        import yfinance as yf
        raw = yf.download(["2330.TW", "6488.TWO", "NVDA"], period="5d", interval="1d", group_by="ticker", progress=False)
        return {t: round(float(raw[t]["Close"].dropna().iloc[-1]), 2) for t in ("2330.TW", "6488.TWO", "NVDA") if len(raw[t]["Close"].dropna())}
    check("2330.TW / 6488.TWO / NVDA", yq)

    Path("debug").mkdir(exist_ok=True)
    Path("debug/probe.txt").write_text("\n".join(OUT), encoding="utf-8")


if __name__ == "__main__":
    main()
