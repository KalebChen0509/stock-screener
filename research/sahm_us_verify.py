"""【2026/11/14】獨立重驗美股「大盤趨勢＋薩姆規則」（11/07 的結論：1990～2026 10.4%／−25.6% vs 永遠持有 8.9%／−52.6%）。

跟原本 research/macro_signals.py 不同的地方（規則本身完全不改）：
  1. 報酬含股息：Ken French 資料庫的美股市場報酬（CRSP 全市場市值加權，含息），現金＝1 個月國庫券（RF），不是 0%。
  2. 期間從 1960 起（薩姆即時版 SAHMREALTIME 從 1959-12 開始）→ 空頭次數從 4 次變成約 10 次。
     **1960～1989 是原本沒看過的資料**（規則是看 1990～2026 的結果挑出來的）＝真正的樣本外檢驗。
  3. 運氣檢定：把薩姆「觸發期間」隨機搬到別的時間（次數、長度不變）5,000 次，看真的薩姆排第幾。
  4. 另外用原本的做法（S&P 500 價格指數、現金 0%）重現 1990～2026 的數字，確認程式一致。
規則（事先定好）：月底判斷、下個月生效。
  趨勢＝S&P 500 月底收盤 > 近 10 個月月收盤均價
  薩姆＝即時版數值 > 0.5 觸發、< 0.3 解除（screener/macro_ind.states("sahm")）；資料月份的下個月月底才知道（公布時間差 1 個月）
  「趨勢或薩姆」＝只有「趨勢跌破而且薩姆觸發」才放現金
成本：買 0.1425%、賣 0.2425%（跟原本一樣，對 SPY 偏保守）。

    .venv/bin/python research/sahm_us_verify.py      （FRED 金鑰從鑰匙圈 stock-fred 讀，不會印出來）
"""
import io
import os
import sys
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _paths import OUT, ROOT  # noqa: E402

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import requests  # noqa: E402

sys.path.insert(0, str(ROOT / "scripts"))
from screener import macro_ind as M  # noqa: E402

COST_IN, COST_OUT = 0.001425, 0.002425
FF_URL = "https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/ftp/F-F_Research_Data_Factors_CSV.zip"
D = OUT / "sahm_us"
D.mkdir(exist_ok=True)


def load_ff() -> pd.DataFrame:
    r = requests.get(FF_URL, timeout=60, headers={"User-Agent": "Mozilla/5.0"})
    r.raise_for_status()
    txt = zipfile.ZipFile(io.BytesIO(r.content)).read("F-F_Research_Data_Factors.csv").decode("latin-1")
    rows = []
    for ln in txt.splitlines():
        p = [x.strip() for x in ln.split(",")]
        if len(p) >= 5 and len(p[0]) == 6 and p[0].isdigit():       # 月資料 yyyymm（年資料是 4 碼）
            rows.append((pd.Timestamp(f"{p[0][:4]}-{p[0][4:]}-01") + pd.offsets.MonthEnd(0), float(p[1]), float(p[4])))
    d = pd.DataFrame(rows, columns=["m", "mkt_rf", "rf"]).set_index("m") / 100
    d["mkt"] = d["mkt_rf"] + d["rf"]
    return d


def load_spx() -> pd.Series:
    import yfinance as yf
    # 坑：Yahoo 的 ^GSPC「月線」只從 1985 開始（更早是空的）；「日線」從 1950 起 → 用日線取月底收盤
    d = yf.download("^GSPC", start="1950-01-01", interval="1d", progress=False, auto_adjust=True)["Close"]
    s = (d.iloc[:, 0] if isinstance(d, pd.DataFrame) else d).dropna().resample("ME").last()
    s.index = s.index.normalize()
    s = s[s.index < pd.Timestamp.today().to_period("M").to_timestamp("M")]
    assert s.loc["1958":].notna().all(), "指數月底收盤有缺"
    return s


def fred(sid: str) -> pd.Series:
    from mac_events import keychain                    # 只在記憶體裡用，不印出來
    key = keychain("stock-fred", "api_key")
    if not key:
        sys.exit("鑰匙圈沒有 stock-fred／api_key")
    return M.fetch_fred(sid, start="1948-01-01", key=key)


def known_at_month_end(state_by_data_month: pd.Series, lag: int, idx) -> pd.Series:
    """資料月份 → lag 個月後的月底才知道。"""
    m = state_by_data_month.copy()
    m.index = pd.DatetimeIndex(m.index).to_period("M").to_timestamp("M")
    m = m.shift(lag, freq="ME")
    return m.reindex(idx).ffill(limit=3)


def run(on: pd.Series, r: pd.Series, cash: pd.Series) -> tuple[pd.Series, pd.Series]:
    pos = on.shift(1).fillna(1.0)                       # 月底訊號、下個月生效
    d = pos.diff().fillna(0.0)
    ret = pos * r + (1 - pos) * cash - np.where(d > 0, d * COST_IN, -d * COST_OUT)
    return ret, pos


def stats(ret: pd.Series, rf: pd.Series, pos: pd.Series | None = None) -> dict:
    nav = (1 + ret).cumprod()
    yrs = len(ret) / 12
    ex = ret - rf.reindex(ret.index).fillna(0)
    out = {"年化": nav.iloc[-1] ** (1 / yrs) - 1, "最大跌幅": float((nav / nav.cummax() - 1).min()),
           "波動": float(ret.std() * np.sqrt(12)), "夏普": float(ex.mean() / ex.std() * np.sqrt(12)) if ex.std() > 0 else np.nan}
    if pos is not None:
        out["持有時間"] = float(pos.mean())
        out["進出次數"] = int((pos.diff().abs() > 0).sum())
    return out


def episodes(flag: pd.Series) -> list[tuple[int, int]]:
    """True 的連續區間 → [(起點位置, 長度)]。"""
    out, start = [], None
    v = flag.values.astype(bool)
    for i, x in enumerate(v):
        if x and start is None:
            start = i
        if (not x or i == len(v) - 1) and start is not None:
            end = i if not x else i + 1
            out.append((start, end - start))
            start = None
    return out


def random_flags(n: int, eps: list[tuple[int, int]], rng) -> np.ndarray:
    """把同樣次數、同樣長度的區間隨機放到別的位置（不重疊）。"""
    for _ in range(200):
        f = np.zeros(n, dtype=bool)
        ok = True
        for _, ln in rng.permutation(np.array(eps, dtype=int)):
            for _try in range(50):
                s = int(rng.integers(0, n - ln + 1))
                if not f[max(0, s - 1):s + ln + 1].any():
                    f[s:s + ln] = True
                    break
            else:
                ok = False
                break
        if ok:
            return f
    raise RuntimeError("放不下")


def fmt(d: dict) -> str:
    s = f"{d['年化']:+6.1%}  最大跌 {d['最大跌幅']:+6.1%}  夏普 {d['夏普']:.2f}"
    if "持有時間" in d:
        s += f"  持有 {d['持有時間']:.0%}  進出 {d['進出次數']}"
    return s


def main():
    ff = load_ff()
    spx = load_spx()
    sahm_rt = fred("SAHMREALTIME")
    unrate = fred("UNRATE")
    ma3 = unrate.rolling(3).mean()
    sahm_rev = (ma3 - ma3.shift(1).rolling(12).min()).dropna()       # 修正後失業率算的薩姆（對照：有偷看修正值的版本）
    idx = ff.index[(ff.index >= "1960-01-31") & (ff.index <= spx.index[-1])]
    r, rf = ff["mkt"].reindex(idx), ff["rf"].reindex(idx)
    p = spx.reindex(spx.index.union(idx)).sort_index()
    ma = p.rolling(10).mean()
    assert ma.reindex(idx).notna().all(), "趨勢訊號有缺（均線算不出來）"
    trend = (p > ma).astype(float).reindex(idx)
    st_rt = M.states("sahm", sahm_rt)
    st_rev = M.states("sahm", sahm_rev)
    trig = (known_at_month_end(st_rt, 1, idx) == "觸發").astype(float)          # 月底已知「薩姆觸發」
    trig_rev = (known_at_month_end(st_rev, 1, idx) == "觸發").astype(float)
    trig_simple = (known_at_month_end(sahm_rt, 1, idx) >= 0.5).astype(float)     # 不用遲滯：數值 ≥0.5 就算
    print(f"資料：{idx[0]:%Y-%m}～{idx[-1]:%Y-%m}（{len(idx)} 個月）；薩姆即時版 {sahm_rt.index[0]:%Y-%m} 起")
    eps = episodes(trig.astype(bool))
    print("薩姆觸發期間（月底已知的月份）：" + "、".join(f"{idx[s]:%Y-%m}（{n} 個月）" for s, n in eps))

    plans = {
        "永遠持有": pd.Series(1.0, index=idx),
        "只看趨勢": trend,
        "只看薩姆（觸發就現金）": 1 - trig,
        "趨勢或薩姆（兩者都壞才現金）": ((trend + (1 - trig)) > 0).astype(float),
        "趨勢且薩姆（任一壞就現金）": trend * (1 - trig),
        "〔對照〕趨勢或薩姆・修正後失業率": ((trend + (1 - trig_rev)) > 0).astype(float),
        "〔對照〕趨勢或薩姆・不用遲滯": ((trend + (1 - trig_simple)) > 0).astype(float),
    }
    spans = {"全部 1960～2026": (idx[0], idx[-1]), "樣本外 1960～1989（原本沒看過）": (idx[0], pd.Timestamp("1989-12-31")),
             "樣本內 1990～2026（原本的期間）": (pd.Timestamp("1990-01-31"), idx[-1])}
    rets, rows = {}, []
    for name, on in plans.items():
        ret, pos = run(on, r, rf)
        rets[name] = (ret, pos)
    for sp, (a, b) in spans.items():
        print(f"\n==== {sp}（含息、現金＝國庫券）====")
        for name in plans:
            ret, pos = rets[name]
            s = stats(ret.loc[a:b], rf, pos.loc[a:b])
            rows.append({"期間": sp, "方案": name, **s})
            print(f"{name:<22}{fmt(s)}")
    pd.DataFrame(rows).to_csv(D / "summary.csv", index=False)

    # ---- 重現原本的做法（價格指數、現金 0%）
    a = pd.Timestamp("1990-02-28")
    pr = p.pct_change().reindex(idx)
    zero = pd.Series(0.0, index=idx)
    print("\n==== 重現 11/07 的做法（S&P 500 價格指數、不含息、現金 0%，1990-02 起）====")
    for name in ("永遠持有", "只看趨勢", "趨勢或薩姆（兩者都壞才現金）"):
        ret, pos = run(plans[name].loc[a:], pr.loc[a:], zero.loc[a:])
        print(f"{name:<22}{fmt(stats(ret, zero, pos))}")

    # ---- 每次空頭（含息淨值，跌 15% 以上）規則做了什麼
    nav = (1 + r).cumprod()
    ret_rule, pos_rule = rets["趨勢或薩姆（兩者都壞才現金）"]
    ret_tr, pos_tr = rets["只看趨勢"]
    print("\n==== 每次大跌（含息市場淨值從高點跌 ≥15%）：高點→低點，各方案同期報酬 ====")
    peak, pk_t, bears, in_b, tr_v, tr_t = nav.iloc[0], idx[0], [], False, None, None
    for t, v in nav.items():
        if v >= peak:
            if in_b:
                bears.append((pk_t, tr_t))
                in_b = False
            peak, pk_t, tr_v = v, t, None
        else:
            if tr_v is None or v < tr_v:
                tr_v, tr_t = v, t
            if v / peak - 1 <= -0.15:
                in_b = True
    if in_b:
        bears.append((pk_t, tr_t))
    brow = []
    for a_, b_ in bears:
        seg = idx[(idx > a_) & (idx <= b_)]
        f = lambda x: float((1 + x.reindex(seg)).prod() - 1)  # noqa: E731
        brow.append({"高點": f"{a_:%Y-%m}", "低點": f"{b_:%Y-%m}", "市場": f(r), "只看趨勢": f(ret_tr), "趨勢或薩姆": f(ret_rule),
                     "規則放現金月數": int((1 - pos_rule.reindex(seg)).sum()), "期間薩姆觸發": bool(trig.reindex(seg).any())})
    B = pd.DataFrame(brow)
    Bf = B.copy()
    for c in ("市場", "只看趨勢", "趨勢或薩姆"):
        Bf[c] = Bf[c].map(lambda v: f"{v:+.1%}")
    print(Bf.to_string(index=False))
    B.to_csv(D / "bears.csv", index=False)

    # ---- 運氣檢定：把薩姆觸發期間隨機搬位置
    rng = np.random.default_rng(20261114)
    for sp, (a, b) in spans.items():
        seg = idx[(idx >= a) & (idx <= b)]
        tg = trig.reindex(seg)
        e = episodes(tg.astype(bool))
        if not e:
            continue
        base = stats(run(((trend.reindex(seg) + (1 - tg)) > 0).astype(float), r.reindex(seg), rf.reindex(seg))[0], rf)
        only_tr = stats(run(trend.reindex(seg), r.reindex(seg), rf.reindex(seg))[0], rf)
        hold = stats(r.reindex(seg), rf)
        sims = []
        for _ in range(5000):
            fl = pd.Series(random_flags(len(seg), e, rng).astype(float), index=seg)
            s = stats(run(((trend.reindex(seg) + (1 - fl)) > 0).astype(float), r.reindex(seg), rf.reindex(seg))[0], rf)
            sims.append((s["年化"], s["最大跌幅"], s["夏普"]))
        sims = np.array(sims)
        print(f"\n==== 運氣檢定｜{sp}：薩姆觸發 {len(e)} 次、共 {int(tg.sum())} 個月，隨機搬位置 5,000 次 ====")
        print(f"真的薩姆：年化 {base['年化']:+.1%}、最大跌 {base['最大跌幅']:+.1%}、夏普 {base['夏普']:.2f}"
              f"（永遠持有 {hold['年化']:+.1%}／{hold['最大跌幅']:+.1%}／{hold['夏普']:.2f}；只看趨勢 {only_tr['年化']:+.1%}／{only_tr['最大跌幅']:+.1%}／{only_tr['夏普']:.2f}）")
        print(f"隨機位置：年化中位數 {np.median(sims[:, 0]):+.1%}、最大跌中位數 {np.median(sims[:, 1]):+.1%}、夏普中位數 {np.median(sims[:, 2]):.2f}")
        print(f"隨機位置贏過真薩姆的比例：年化 {np.mean(sims[:, 0] >= base['年化']):.1%}、最大跌幅 {np.mean(sims[:, 1] >= base['最大跌幅']):.1%}、"
              f"夏普 {np.mean(sims[:, 2] >= base['夏普']):.1%}、年化與最大跌都贏 {np.mean((sims[:, 0] >= base['年化']) & (sims[:, 1] >= base['最大跌幅'])):.1%}")
    pd.DataFrame({"mkt": r, "rf": rf, "trend": trend, "sahm_trig": trig, "pos_rule": pos_rule, "ret_rule": ret_rule,
                  "ret_trend": ret_tr}).to_csv(D / "monthly.csv")


if __name__ == "__main__":
    main()
