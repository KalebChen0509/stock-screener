"""【2026/11/07】總經指標的轉折訊號有沒有用？（使用者：先顯示＋通知，回測有用才用在進場）

規則事先定好、不調參數（跟網站／通知用同一套 screener/macro_ind.states）：
A. 進出場：月底看訊號，「狀態好」就持有該市場指數、「狀態差」放現金（0%），下個月生效；扣 ETF 進出成本。
   跟「永遠持有」和「大盤趨勢（站上 10 個月均線）」比，也測「趨勢＋指標都好才持有」與「趨勢或指標任一好」。
B. 提前預警：每次大跌（月收盤從高點跌 ≥20%）之前 12 個月內有沒有出現「轉差」訊號、提前幾個月；
   以及「假警報」：轉差之後 12 個月內沒有跌超過 15%。
公布時間差（資料月份的月底之後幾個月底才知道）：國發會 1、OECD CLI 2、FRED 月資料 1、週／日資料 0。
限制：(1) 國發會與 OECD 指標每月會回溯修正，這裡用的是「現在的版本」→ 結果偏樂觀；
      (2) 指數不含股息、現金沒算利息（兩邊都少算，對「常放現金」的方案略不利）；(3) PMI 只有新聞（沒有歷史），不測。

資料：
    國發會、OECD：直接下載
    美國（FRED）：events 分支的 macro_series.csv.gz（「市場事件偵測」跑過才有）；或 --us 指定本機檔案
    指數：yfinance 月資料（^TWII 1997 起、^GSPC、^N225、000001.SS、^GDAXI）
執行：.venv/bin/python research/macro_signals.py
"""
import argparse
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _paths import OUT, ROOT  # noqa: E402,F401

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import requests  # noqa: E402

from screener import macro_ind as M  # noqa: E402

COST_IN, COST_OUT = 0.001425, 0.002425          # 買 ETF 手續費；賣＋證交稅 0.1%
BAD = {"趨緩", "衰退", "倒掛", "上升警訊", "觸發", "衰退警訊", "偏緊", "擴大", "收縮", "負成長", "藍", "黃藍"}
TESTS = [  # key, 市場指數, 公布時間差（月）
    ("tw_lead", "^TWII", 1), ("tw_coin", "^TWII", 1), ("tw_light", "^TWII", 1), ("tw_orders", "^TWII", 1), ("tw_export", "^TWII", 1),
    ("us_cli", "^GSPC", 2), ("us_curve", "^GSPC", 0), ("us_curve2", "^GSPC", 0), ("us_claims", "^GSPC", 0), ("us_sahm", "^GSPC", 1),
    ("us_cfnai", "^GSPC", 1), ("us_nfci", "^GSPC", 0), ("us_permit", "^GSPC", 1), ("us_orders", "^GSPC", 1), ("us_philly", "^GSPC", 0),
    ("cn_cli", "000001.SS", 2), ("jp_cli", "^N225", 2), ("eu_cli", "^GDAXI", 2), ("eu_de_cli", "^GDAXI", 2), ("gl_cli", "^GSPC", 2),
]
RAW_EVENTS = "https://raw.githubusercontent.com/KalebChen0509/stock-screener/events/macro_series.csv.gz"


def load_indicators(us_path: str | None) -> tuple[dict, dict]:
    raw, extra = {}, {}
    nd = M.fetch_ndc()
    for c in M.CATALOG:
        if c["src"] == "ndc":
            col = c["sid"] if c["sid"] in nd else next((x for x in nd.columns if c["sid"] in x), None)
            if col:
                raw[c["key"]] = nd[col].dropna()
    extra["tw_light"] = nd["燈號"].dropna()
    got = M.fetch_oecd("oecd_cli", [c["sid"] for c in M.CATALOG if c["src"] == "oecd_cli"], start="1985-01")
    for c in M.CATALOG:
        if c["src"] == "oecd_cli" and c["sid"] in got:
            raw[c["key"]] = got[c["sid"]]
    try:
        if us_path:
            db = pd.read_csv(us_path, parse_dates=["date"])
        else:
            r = requests.get(RAW_EVENTS, timeout=60)
            r.raise_for_status()
            db = pd.read_csv(io.BytesIO(r.content), compression="gzip", parse_dates=["date"])
        for k, g in db.groupby("key"):
            if k.startswith("us_"):
                raw[k] = pd.Series(g["value"].values, index=g["date"].values).sort_index()
    except Exception as e:  # noqa
        print("美國資料還沒有（events 分支的 macro_series.csv.gz）：", e)
    return raw, extra


def load_index(tickers) -> pd.DataFrame:
    import yfinance as yf
    out = {}
    for t in tickers:
        d = yf.download(t, start="1985-01-01", interval="1mo", progress=False, auto_adjust=True)["Close"]
        s = d.iloc[:, 0] if isinstance(d, pd.DataFrame) else d
        s.index = s.index.to_period("M").to_timestamp("M")
        out[t] = s.groupby(level=0).last()
    px = pd.DataFrame(out)
    return px[px.index < pd.Timestamp.today().to_period("M").to_timestamp("M")]   # 不用還沒結束的這個月


def monthly_good(key: str, raw: dict, extra: dict, lag: int, idx: pd.DatetimeIndex) -> pd.Series:
    """每個月底「當時已知」的狀態是不是好的（True／False／NaN＝還沒有資料）。"""
    c = M.BY_KEY[key]
    x = M.transform(raw[key], c["tf"])
    st = M.states(c["rule"], x, extra.get(key)).dropna()
    if st.empty:
        return pd.Series(np.nan, index=idx)
    st.index = pd.DatetimeIndex(st.index)
    gap = np.median(np.diff(st.index[-30:].values).astype("timedelta64[D]").astype(float)) if len(st) > 30 else 30
    if gap < 20:                                     # 日／週資料：月底最後一筆
        m = st.groupby(st.index.to_period("M")).last()
    else:                                            # 月資料：資料月份
        m = st.groupby(st.index.to_period("M")).last()
    m.index = m.index.to_timestamp("M")
    m = m.shift(lag, freq="ME") if lag else m       # 公布時間差：資料月份 + lag 個月的月底才知道
    good = (~m.isin(BAD)).astype(float)
    return good.reindex(idx).ffill(limit=3)


def run_strategy(on: pd.Series, r: pd.Series) -> pd.Series:
    """on＝月底訊號（1 持有、0 現金），下個月生效。"""
    pos = on.shift(1).fillna(0)
    d = pos.diff().fillna(pos.iloc[0])
    return pos * r.fillna(0) - np.where(d > 0, d * COST_IN, -d * COST_OUT)


def stats(ret: pd.Series, pos: pd.Series | None = None) -> dict:
    nav = (1 + ret).cumprod()
    yrs = len(ret) / 12
    out = {"年化": nav.iloc[-1] ** (1 / yrs) - 1, "最大跌幅": float((nav / nav.cummax() - 1).min())}
    if pos is not None:
        p = pos.shift(1).fillna(0)
        out["持有時間"] = float(p.mean())
        out["進出次數"] = int((p.diff().abs() > 0).sum())
    return out


def drawdowns(px: pd.Series, thr=-0.20, rebound=0.20) -> list[tuple]:
    """空頭（月收盤）：從高點跌 ≥20% 開始，從低點反彈 ≥20% 結束 → [(高點月份, 低點月份, 跌幅)]。"""
    out, peak, pk_t, trough, tr_t, bear = [], px.iloc[0], px.index[0], px.iloc[0], px.index[0], False
    for t, v in px.items():
        if not bear:
            if v >= peak:
                peak, pk_t = v, t
            elif v / peak - 1 <= thr:
                bear, trough, tr_t = True, v, t
        else:
            if v < trough:
                trough, tr_t = v, t
            elif v / trough - 1 >= rebound:
                out.append((pk_t, tr_t, trough / peak - 1))
                bear, peak, pk_t = False, v, t
    if bear:
        out.append((pk_t, tr_t, trough / peak - 1))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--us", help="本機的 macro_series.csv.gz（預設從 events 分支下載）")
    args = ap.parse_args()
    raw, extra = load_indicators(args.us)
    px = load_index(sorted({t for _, t, _ in TESTS}))
    rets = px.pct_change()
    rows, warn = [], []
    for key, tk, lag in TESTS:
        if key not in raw:
            continue
        p, r = px[tk].dropna(), rets[tk]
        good = monthly_good(key, raw, extra, lag, p.index)
        start = good.first_valid_index()
        if start is None:
            continue
        idx = p.index[p.index >= start]
        if len(idx) < 60:
            continue
        g = good.reindex(idx).fillna(1)
        trend = (p > p.rolling(10).mean()).reindex(idx).astype(float)
        rr = r.reindex(idx)
        base = {"指標": M.BY_KEY[key]["name"], "國家": M.COUNTRIES[M.BY_KEY[key]["country"]], "市場": tk,
                "期間": f"{idx[0]:%Y-%m}～{idx[-1]:%Y-%m}"}
        h = len(idx) // 2
        for name, on in (("永遠持有", pd.Series(1.0, index=idx)), ("只看趨勢", trend), ("只看指標", g),
                         ("趨勢且指標", trend * g), ("趨勢或指標", ((trend + g) > 0).astype(float))):
            ret = run_strategy(on, rr)
            s = stats(ret, on)
            s["前半年化"], s["後半年化"] = stats(ret.iloc[:h])["年化"], stats(ret.iloc[h:])["年化"]
            rows.append({**base, "方案": name, **s})
        # B. 提前預警
        bad_turn = idx[(g.diff() < 0).values]
        dds = drawdowns(p.reindex(idx))
        hits = []
        for pk, tr, dd in dds:
            pre = [t for t in bad_turn if pk - pd.DateOffset(months=12) <= t <= tr]
            lead = (pk.to_period("M") - pre[0].to_period("M")).n if pre else None
            hits.append((pk, dd, lead))
        false = sum(1 for t in bad_turn if t <= idx[-1] - pd.DateOffset(months=12)
                    and (p.loc[t:t + pd.DateOffset(months=12)].min() / p.loc[t] - 1) > -0.15)
        warn.append({**base, "大跌次數": len(dds), "事前有轉差": sum(1 for x in hits if x[2] is not None),
                     "提前月數（負＝高點之後才出現）": ", ".join(f"{x[0]:%Y-%m}:{x[2] if x[2] is not None else '無'}" for x in hits),
                     "轉差次數": len(bad_turn), "假警報": false})
    res = pd.DataFrame(rows)
    pd.set_option("display.width", 250)
    pd.set_option("display.max_colwidth", 80)
    f = res.copy()
    for c in ("年化", "最大跌幅", "前半年化", "後半年化"):
        f[c] = f[c].map(lambda v: f"{v:+.1%}")
    f["持有時間"] = f["持有時間"].map(lambda v: f"{v:.0%}")
    print("==== A. 進出場（月底看訊號、下個月生效；指數不含息、現金 0%）====")
    for (ind, tk), g_ in f.groupby(["指標", "市場"], sort=False):
        print(f"\n【{g_['國家'].iloc[0]}】{ind} → {tk}（{g_['期間'].iloc[0]}）")
        print(g_[["方案", "年化", "最大跌幅", "持有時間", "進出次數", "前半年化", "後半年化"]].to_string(index=False))
    print("\n==== B. 提前預警（大跌＝月收盤從高點跌 ≥20%；假警報＝轉差後 12 個月內沒跌超過 15%）====")
    W = pd.DataFrame(warn)
    print(W[["國家", "指標", "市場", "期間", "大跌次數", "事前有轉差", "轉差次數", "假警報"]].to_string(index=False))
    print()
    for _, w in W.iterrows():
        print(f"{w['指標']}：{w['提前月數（負＝高點之後才出現）']}")
    res.to_csv(OUT / "macro_signals_timing.csv", index=False)
    W.to_csv(OUT / "macro_signals_warning.csv", index=False)


if __name__ == "__main__":
    main()
