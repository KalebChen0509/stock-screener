"""【2026/11/14】衍生性商品 D 階段：槓桿、賣選擇權「只回測」（使用者沒有期貨帳戶、沒有經驗；不開戶、不下單）。

規則事先寫死、不看結果調整。資料：data 分支 cache/derivs（期交所 TX 期貨、TXO 月選擇權 ±10%、2007 起）、
本機 data/etf（證交所官方 0050／00631L，自己還原）、events 分支的台灣隔夜拆款利率、Yahoo（SPY／SSO／UPRO／^PUT／^SP500TR）。

A. 台股槓桿
   A1 台指期近月連續 1×／1.5×／2×／3×：全部資金放保證金帳戶（現金賺隔夜利率），「每天調回目標倍數」與「每月調一次」。
      1× 期貨＋利息 ≈ 持有大盤（期貨價格已反映股息）。成本：每月換倉 0.01% × 倍數（手續費＋期交稅，粗估）。
   A2 元大台灣50正2（00631L，官方價格還原）vs 0050 含息，2014-11 起。
B. 台股賣選擇權（賣出賣權 put）：每個月結算日收盤後，賣下個月到期、履約價在期貨價下方 5%／10% 的賣權（選「不高於目標的最高履約價」），抱到結算。
   權利金用當天結算價打 95 折（買賣價差）、再扣手續費 0.4 點（20 元）與交易稅 0.1%；結算價用到期月期貨最後一天的收盤價（近似）。
   B1 現金擔保（每口準備「履約價 × 50 元」的現金，槓桿 1 倍）；B2／B3 名目金額＝資金的 3 倍／5 倍（保證金交易）。
   每天用選擇權結算價重估（沒有報價時用內含價值），算最大跌幅；
   追繳＝帳戶權益 < 期交所「賣出選擇權」維持保證金（權利金市值＋max(A−價外值, B)；A、B 用 2026/08 的金額占指數的比例套回歷史，是近似）。
C. 美股：SSO（2 倍）、UPRO（3 倍）vs SPY；CBOE PutWrite 指數（^PUT：每月賣價平賣權、現金擔保）vs S&P 500 含息。
   免費資料沒有美股價外賣權的歷史報價 → 只能用 CBOE 的現成指數，不自己編。
    SCREENER_CACHE=<data 分支>/cache .venv/bin/python research/derivs_d.py
"""
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _paths import CACHE, OUT  # noqa: E402

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import requests  # noqa: E402

from screener import derivs  # noqa: E402

D = OUT / "derivs_d"
D.mkdir(exist_ok=True)
MULT = {"TX": 200, "MTX": 50, "TMF": 10}
# 期交所保證金一覽表（2026/08/12 更新；新台幣）
MARGIN = {"TX": (701_000, 538_000), "MTX": (175_250, 134_500), "TMF": (35_050, 26_900)}       # (原始, 維持)
OPT_A, OPT_B = (187_000, 143_000), (94_000, 72_000)                                           # (原始, 維持)
SLIP, FEE_PT, TAX = 0.05, 0.4, 0.001


def stats(nav: pd.Series) -> dict:
    nav = nav.dropna()
    yrs = (nav.index[-1] - nav.index[0]).days / 365.25
    m = nav.resample("ME").last().pct_change().dropna()
    d = nav.pct_change().dropna()
    return {"期間": f"{nav.index[0]:%Y-%m}～{nav.index[-1]:%Y-%m}", "年化": (nav.iloc[-1] / nav.iloc[0]) ** (1 / yrs) - 1,
            "最大跌幅": float((nav / nav.cummax() - 1).min()), "最差單月": float(m.min()), "最差單日": float(d.min()),
            "年波動": float(d.std() * np.sqrt(252))}


def show(title: str, rows: list[dict]):
    df = pd.DataFrame(rows)
    f = df.copy()
    for c in ("年化", "最大跌幅", "最差單月", "最差單日", "年波動"):
        if c in f:
            f[c] = f[c].map(lambda v: "" if pd.isna(v) else f"{v:+.1%}")
    print(f"\n==== {title} ====")
    print(f.to_string(index=False))
    return df


def load_rate(idx: pd.DatetimeIndex) -> pd.Series:
    """台灣金融業隔夜拆款利率（年率 %）→ 每個交易日的日利率。抓不到就當 0。"""
    try:
        r = requests.get("https://raw.githubusercontent.com/KalebChen0509/stock-screener/events/macro_series.csv.gz", timeout=60)
        r.raise_for_status()
        db = pd.read_csv(io.BytesIO(r.content), compression="gzip", parse_dates=["date"])
        s = db[db["key"] == "tw_onrate"].set_index("date")["value"].sort_index()
        return (s.reindex(idx, method="ffill").fillna(s.iloc[0]) / 100 / 252)
    except Exception as e:  # noqa
        print("隔夜利率抓不到，現金當 0%：", e)
        return pd.Series(0.0, index=idx)


def lev_daily(r: pd.Series, rf: pd.Series, lev: float) -> pd.Series:
    cost = 0.0001 * lev / 21
    return (1 + lev * r + rf - cost).cumprod()


def lev_monthly(r: pd.Series, rf: pd.Series, lev: float) -> pd.Series:
    """每月第一個交易日把曝險調回 lev 倍，月中口數不動。"""
    nav, out = 1.0, {}
    for _, g in r.groupby(r.index.to_period("M")):
        cum = (1 + g).cumprod() - 1
        intr = rf.reindex(g.index).cumsum()
        path = nav * (1 + lev * cum + intr - 0.0001 * lev)
        path = path.clip(lower=0)
        for d, v in path.items():
            out[d] = v
        nav = float(path.iloc[-1])
        if nav <= 0:
            break
    return pd.Series(out)


# ---------------------------------------------------------------- B. 賣出賣權
def put_write(fut: pd.DataFrame, opt: pd.DataFrame, rf: pd.Series, otm: float, lam: float, a_pct, b_pct) -> tuple[pd.Series, pd.DataFrame]:
    tx = fut[fut["contract"] == "TX"]
    last_day = tx.groupby("expiry")["date"].max()                      # 每個到期月的最後交易日（結算日）
    last_day = last_day[last_day < tx["date"].max() - pd.Timedelta(days=3)]
    exps = sorted(last_day.index)
    fset = tx.pivot_table(index="date", columns="expiry", values="settle", aggfunc="last")
    fclose = tx.pivot_table(index="date", columns="expiry", values="close", aggfunc="last")
    P = opt[opt["cp"] == "P"]
    nav, navs, trades = 1.0, {}, []
    for i in range(len(exps) - 1):
        e0, e1 = exps[i], exps[i + 1]
        d0, d1 = last_day[e0], last_day[e1]
        F0 = fset.at[d0, e1] if (d0 in fset.index and e1 in fset) else np.nan
        cand = P[(P["date"] == d0) & (P["expiry"] == e1) & (P["settle"] > 0)]
        if not np.isfinite(F0) or cand.empty:
            continue
        ok = cand[cand["strike"] <= F0 * (1 - otm)]
        row = ok.sort_values("strike").iloc[-1] if len(ok) else cand.sort_values("strike").iloc[0]
        K, p0 = float(row["strike"]), float(row["settle"])
        prem = p0 * (1 - SLIP) - FEE_PT - TAX * p0
        eq0 = K / lam                                                  # 每一點名目金額準備的資金
        days = fset.index[(fset.index > d0) & (fset.index <= d1)]
        path = P[(P["expiry"] == e1) & (P["strike"] == K) & (P["date"] > d0) & (P["date"] <= d1)].set_index("date")["settle"]
        call, bust, worst = False, False, 0.0
        for d in days:
            Ft = fset.at[d, e1] if np.isfinite(fset.at[d, e1]) else fclose.at[d, e1]
            if d == d1:
                Ft = fclose.at[d, e1] if np.isfinite(fclose.at[d, e1]) else Ft
                val = max(K - Ft, 0.0)
            else:
                v = path.get(d, np.nan)
                val = float(v) if np.isfinite(v) and v > 0 else max(K - Ft, 0.0)
            eq = eq0 + prem - val - (FEE_PT if d == d1 else 0.0)
            req = val + max(a_pct * Ft - max(Ft - K, 0.0), b_pct * Ft)
            call |= (d != d1) and eq < req
            bust |= eq <= 0
            navs[d] = nav * max(eq, 0.0) / eq0
            worst = min(worst, eq / eq0 - 1)
        ST = fclose.at[d1, e1] if np.isfinite(fclose.at[d1, e1]) else fset.at[days[-2], e1]
        payoff = max(K - ST, 0.0)
        intr = float(rf.reindex(days).sum())
        ret = max((prem - payoff - FEE_PT) / eq0 + intr, -1.0)
        trades.append({"賣出日": d0, "到期": d1, "期貨": F0, "履約價": K, "價外": 1 - K / F0, "權利金": p0, "權利金占履約價": prem / K,
                       "結算": ST, "賠付": payoff, "報酬": ret, "期間最低": worst, "追繳": call, "賠光": bust})
        nav *= 1 + ret
        navs[d1] = nav
        if nav <= 0:
            break
    return pd.Series(navs).sort_index(), pd.DataFrame(trades)


def main():
    fut = pd.read_csv(os.path.join(CACHE, "derivs", "fut_daily.csv.gz"), dtype={"expiry": str}, parse_dates=["date"])
    fut.loc[fut["settle"] <= 0, "settle"] = np.nan
    opt = pd.read_csv(os.path.join(CACHE, "derivs", "opt_daily.csv.gz"), dtype={"expiry": str}, parse_dates=["date"])
    r = derivs.front_returns(fut, "TX")
    r.index = pd.DatetimeIndex(r.index)
    rf = load_rate(r.index)
    F_now = float(fut[(fut["contract"] == "TX")].sort_values(["date", "expiry"]).dropna(subset=["settle"]).groupby("date").first()["settle"].iloc[-1])
    print(f"台指期近月連續日報酬：{r.index[0].date()}～{r.index[-1].date()}（{len(r)} 天）；最新期貨價 {F_now:,.0f}；隔夜利率平均 {rf.mean() * 252:.2%}")

    # ---------------- A1 期貨槓桿
    rows, navs = [], {}
    for lev in (1.0, 1.5, 2.0, 3.0):
        navs[f"期貨 {lev:g} 倍（每天調整）"] = lev_daily(r, rf, lev)
        navs[f"期貨 {lev:g} 倍（每月調整）"] = lev_monthly(r, rf, lev)
    for k, v in navs.items():
        rows.append({"方案": k, **stats(v)})
    A1 = show("A1. 台指期槓桿，2007 起（1 倍＋利息 ≈ 持有大盤含息）", rows)
    for nm, a, b in (("2008 金融海嘯", "2007-10-31", "2008-11-20"), ("2020 疫情", "2020-01-20", "2020-03-19"),
                     ("2022 升息", "2022-01-05", "2022-10-25"), ("2025-04 關稅", "2025-03-25", "2025-04-09")):
        print(f"{nm}：" + "、".join(f"{k.split('（')[0]} {navs[k].loc[:b].iloc[-1] / navs[k].loc[:a].iloc[-1] - 1:+.0%}" for k in navs if "每天" in k))

    # ---------------- A2 正2 vs 0050（官方價格）
    try:
        import margin_timing as MT
        tr = {c: MT.total_return_close(MT.load_etf(c)) for c in ("0050", "00631L")}
        a = tr["00631L"].index[1]
        rows = [{"方案": "0050 含息（官方價格）", **stats(tr["0050"].loc[a:])}, {"方案": "正2 00631L（官方價格還原）", **stats(tr["00631L"].loc[a:])},
                {"方案": "期貨 2 倍（每天調整）同期", **stats(navs["期貨 2 倍（每天調整）"].loc[a:])},
                {"方案": "期貨 1 倍 同期", **stats(navs["期貨 1 倍（每天調整）"].loc[a:])},
                {"方案": "0050 含息 2009 起", **stats(tr["0050"])}]
        show("A2. 正2（00631L）vs 0050，正2 上市後", rows)
        x, y = tr["00631L"].loc[a:], tr["0050"].loc[a:]
        for nm, s, e in (("2015 股災", "2015-04-27", "2015-08-24"), ("2018Q4", "2018-10-01", "2019-01-03"), ("2020 疫情", "2020-01-20", "2020-03-19"),
                         ("2022 升息", "2022-01-05", "2022-10-25"), ("2025-04 關稅", "2025-03-25", "2025-04-09")):
            print(f"{nm}：0050 {y.loc[:e].iloc[-1] / y.loc[:s].iloc[-1] - 1:+.0%}、正2 {x.loc[:e].iloc[-1] / x.loc[:s].iloc[-1] - 1:+.0%}")
    except Exception as e:  # noqa
        print("A2 跳過（本機沒有 data/etf 官方價格）：", str(e)[:120])

    # ---------------- B 賣出賣權
    a_pct, b_pct = OPT_A[1] / 50 / F_now, OPT_B[1] / 50 / F_now          # 維持保證金占指數比例（近似套回歷史）
    rows, allt = [], {}
    for otm in (0.05, 0.10):
        for lam, nm in ((1, "現金擔保（1 倍）"), (3, "名目 3 倍"), (5, "名目 5 倍")):
            nav, tr_ = put_write(fut, opt, rf, otm, lam, a_pct, b_pct)
            key = f"賣價外 {otm:.0%} 賣權｜{nm}"
            allt[key] = tr_
            s = stats(nav) if len(nav) > 30 and nav.iloc[-1] > 0 else {"期間": f"{nav.index[0]:%Y-%m}～{nav.index[-1]:%Y-%m}", "年化": np.nan,
                                                                        "最大跌幅": -1.0, "最差單月": np.nan, "最差單日": np.nan, "年波動": np.nan}
            rows.append({"方案": key, **s, "筆數": len(tr_), "賠錢筆數": int((tr_["報酬"] < 0).sum()), "被追繳筆數": int(tr_["追繳"].sum()),
                         "賠光": bool(tr_["賠光"].any()) or nav.iloc[-1] <= 0, "平均實際價外": f"{tr_['價外'].mean():.1%}"})
            tr_.to_csv(D / f"put_{int(otm * 100)}_{lam}x.csv", index=False)
    rows.append({"方案": "〔對照〕期貨 1 倍（≈持有大盤）", **stats(navs["期貨 1 倍（每天調整）"])})
    show("B. 台指選擇權每月賣出賣權，2007 起", rows)
    t = allt["賣價外 5% 賣權｜現金擔保（1 倍）"]
    print("現金擔保、價外 5%：每筆平均收權利金占履約價 "
          f"{t['權利金占履約價'].mean():.2%}；賺錢的月份平均 {t.loc[t['報酬'] > 0, '報酬'].mean():+.2%}、賠錢的月份平均 {t.loc[t['報酬'] < 0, '報酬'].mean():+.2%}")
    print("最慘的 6 筆（現金擔保、價外 5%）：")
    w = t.sort_values("報酬").head(6)
    for _, x in w.iterrows():
        print(f"  {x['賣出日']:%Y-%m-%d} 賣 {x['履約價']:,.0f}（期貨 {x['期貨']:,.0f}）→ 結算 {x['結算']:,.0f}：{x['報酬']:+.1%}（期間最低 {x['期間最低']:+.1%}）")

    # ---------------- C 美股
    import yfinance as yf
    us = {}
    for tk in ("SPY", "SSO", "UPRO", "^PUT", "^SP500TR"):
        d = yf.download(tk, start="1990-01-01", interval="1d", progress=False, auto_adjust=True)["Close"]
        us[tk] = (d.iloc[:, 0] if isinstance(d, pd.DataFrame) else d).dropna()
    rows = []
    for a, names in ((us["SSO"].index[0], ("SPY", "SSO")), (us["UPRO"].index[0], ("SPY", "SSO", "UPRO")), (us["^PUT"].index[0], ("^SP500TR", "^PUT"))):
        for n in names:
            lab = {"SPY": "SPY（含息）", "SSO": "SSO 2 倍", "UPRO": "UPRO 3 倍", "^PUT": "CBOE PutWrite 指數（賣價平賣權、現金擔保）", "^SP500TR": "S&P 500 含息"}[n]
            rows.append({"方案": lab, **stats(us[n].loc[a:])})
    show("C. 美股：槓桿 ETF 與賣權指數（各組用相同的起始日）", rows)
    for nm, s, e in (("2008 金融海嘯", "2007-10-09", "2009-03-09"), ("2020 疫情", "2020-02-19", "2020-03-23"), ("2022 升息", "2022-01-03", "2022-10-12")):
        print(f"{nm}：" + "、".join(f"{k} {v.loc[:e].iloc[-1] / v.loc[:s].iloc[-1] - 1:+.0%}" for k, v in us.items() if v.index[0] <= pd.Timestamp(s)))

    # ---------------- 以使用者的規模可不可行
    print(f"\n==== 以目前指數（期貨 {F_now:,.0f}）算一口的大小（保證金：期交所 2026/08/12）====")
    for c, nm in (("TX", "大台"), ("MTX", "小台"), ("TMF", "微台")):
        notional = F_now * MULT[c]
        print(f"{nm}：一口名目金額 {notional:,.0f} 元；原始保證金 {MARGIN[c][0]:,} 元（只放保證金＝{notional / MARGIN[c][0]:.0f} 倍槓桿）；"
              f"要做到 1 倍需 {notional:,.0f} 元、2 倍需 {notional / 2:,.0f} 元")
    K = round(F_now * 0.95 / 100) * 100
    print(f"賣一口價外 5% 的台指賣權（履約價約 {K:,.0f}）：現金擔保要 {K * 50:,.0f} 元；只放保證金約 {OPT_A[0]:,} 元上下（＝名目的 {K * 50 / OPT_A[0]:.0f} 倍）")
    spy = float(us["SPY"].iloc[-1])
    print(f"賣一口 SPY 賣權（股價 {spy:,.0f} 美元）：現金擔保約 {spy * 0.95 * 100:,.0f} 美元")


if __name__ == "__main__":
    main()
