"""【2026/11/08】回測「上櫃融資維持率擇時買 0050／正二」。網路說法：上櫃維持率 <150（嚴格 145）買、>210（保守 205）賣，「每趟穩賺至少 20%」。

先跑資料（官方數據，data/ 不上傳）：
    research/margin_ratio.py --market tpex / --market twse  → data/margin_ratio/{tpex,twse}.csv（推估維持率；跟 MacroMicro 11/05、11/06 完全一致）
    research/etf_prices.py                                → data/etf/{0050,00631L}_daily.csv、dividends.csv（證交所官方、未還原）
    央行 A13Rate.csv                                      → data/rates/deposit_1y_5banks.csv（五大銀行一年期定存固定利率月平均）
執行：.venv/bin/python research/margin_timing.py → research/out/margin_timing/（report.md、trades.csv、圖）
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _paths import OUT as OUT_ROOT, ROOT  # noqa: E402

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

# ================================================================ 參數（都在這裡改）
CAPITAL = 1_000_000                 # 初始資金（元）
FEE = 0.001425                      # 手續費（買、賣各一次，無折扣）
FEE_MIN = 20                        # 單筆最低手續費
TAX_ETF = 0.001                     # ETF 證交稅（賣出）
NHI_RATE, NHI_MIN = 0.0211, 20_000  # 二代健保補充保費：單筆股利 ≥2 萬元扣 2.11%
IDLE_MMF = 0.010                    # 閒置資金 b：貨幣型／短債 ETF 年化報酬（使用者選 1.0%）
LEV_EXPENSE = 0.010                 # 正二模擬：年化費用率
LEV_SPREAD = 0.012                  # 正二模擬：借錢成本＝定存利率 + 這個加碼（年化）；用 2014-10～2026 重疊期間校準（模擬≈真實）
PAIRS = [(150, 210), (150, 205), (145, 210), (145, 205)]   # （進場, 出場）
TRANCHE_STEP = 5                    # 分批：進場門檻、−5、−10 各買三分之一（150 → 150／145／140）
GRID_ENTRY = range(130, 171, 5)
GRID_EXIT = range(190, 231, 5)
SPLIT_IS = "2017-12-31"             # 樣本內 2009～2017，樣本外 2018～
# 已知分割（日期＝分割後恢復交易第一天）；程式會用官方價格驗證跳動比例
SPLITS = {"0050": [("2025-06-18", 4)], "00631L": [("2026-03-31", 22)]}   # 官方公告比例；detect_splits 只用來核對日期

DATA = ROOT / "data"
OUT = OUT_ROOT / "margin_timing"


# ================================================================ 資料
def load_ratio(market: str) -> pd.Series:
    d = pd.read_csv(DATA / "margin_ratio" / f"{market}.csv", parse_dates=["date"]).set_index("date")["ratio"]
    return d.sort_index()


def load_rate() -> pd.Series:
    """年利率（小數），日頻（沿用當月）。"""
    m = pd.read_csv(DATA / "rates" / "deposit_1y_5banks.csv", parse_dates=["date"]).set_index("date")["rate"] / 100
    idx = pd.date_range(m.index.min(), pd.Timestamp.today().normalize(), freq="D")
    return m.reindex(idx).ffill()


def detect_splits(code: str, px: pd.DataFrame) -> list[tuple[str, int]]:
    """官方未還原價格：收盤 ÷ 前一日收盤 < 0.4 的日子當成分割，比例取最接近的整數。"""
    r = px["close"] / px["close"].shift(1)
    out = []
    for d, v in r[r < 0.4].items():
        out.append((d.strftime("%Y-%m-%d"), int(round(1 / v))))
    return out


def load_etf(code: str) -> pd.DataFrame:
    """官方日行情＋除息 → date, open, close（未還原）, div（每股現金股利，除息日）, split（當天分割比例）。"""
    px = pd.read_csv(DATA / "etf" / f"{code}_daily.csv", parse_dates=["date"]).set_index("date").sort_index()
    px = px[px["close"].notna()]
    px["open"] = px["open"].fillna(px["close"])
    dv = pd.read_csv(DATA / "etf" / "dividends.csv", parse_dates=["date"], dtype={"code": str})
    dv = dv[(dv["code"].astype(str) == code) & (dv["type"].astype(str).str.contains("息"))].groupby("date")["cash"].sum()
    px["div"] = dv.reindex(px.index).fillna(0.0)
    found = detect_splits(code, px)
    known = SPLITS.get(code, [])
    if code in SPLITS and not known:
        SPLITS[code] = found
    px["split"] = 1.0
    for d, k in SPLITS[code]:
        px.loc[pd.Timestamp(d), "split"] = k
    px.attrs["splits_found"] = found
    return px


def total_return_close(px: pd.DataFrame) -> pd.Series:
    """含息還原收盤價（股利當天再投入、不扣稅費；只用來驗證與畫圖）。"""
    prev = px["close"].shift(1) / px["split"]
    r = (px["close"] + px["div"]) / prev - 1
    r.iloc[0] = 0
    return (1 + r).cumprod()


def simulate_lev(base: pd.DataFrame, real: pd.DataFrame, rate: pd.Series) -> tuple[pd.DataFrame, pd.Timestamp]:
    """正二：上市前用 0050 含息日報酬 ×2 − 年化費用 − 借錢成本（定存利率＋加碼）模擬；上市後用真實官方價格。
    真實價格整段乘同一個比例 k，讓「上市第一天開盤」＝「模擬最後一天收盤」，持有跨過上市日時淨值不跳動。
    分割欄位保留（交易引擎在分割日把股數放大）。回傳 (資料, 真實資料開始日)。"""
    start = real.index.min()
    b = base.loc[:start].iloc[:-1]
    tr = total_return_close(base).loc[b.index]
    r0 = tr.pct_change().fillna(0)
    prev = (b["close"].shift(1) / b["split"]).fillna(b["open"])
    ro = b["open"] / prev - 1                                         # 前收 → 今開
    cost = (LEV_EXPENSE + rate.reindex(b.index).ffill().bfill() + LEV_SPREAD) / 252
    close = 100 * (1 + 2 * r0 - cost).cumprod()
    opn = close.shift(1).fillna(100) * (1 + 2 * ro)
    sim = pd.DataFrame({"open": opn, "close": close, "div": 0.0, "split": 1.0, "sim": True}, index=b.index)
    k = close.iloc[-1] / real["open"].iloc[0]
    rr = real[["open", "close", "split"]].copy()
    rr["open"] *= k
    rr["close"] *= k
    rr["div"], rr["sim"] = 0.0, False
    return pd.concat([sim, rr]).sort_index(), start


def idle_factor(index: pd.DatetimeIndex, annual) -> pd.Series:
    """閒置資金：前一個交易日 → 今天的成長倍數（日曆天複利）。annual＝常數或日頻年利率序列。"""
    gap = pd.Series(index, index=index).diff().dt.days.fillna(0)
    a = annual.reindex(index).ffill().bfill() if isinstance(annual, pd.Series) else pd.Series(annual, index=index)
    return (1 + a) ** (gap / 365)


# ================================================================ 交易引擎
def fee(amount: float) -> float:
    return max(FEE_MIN, amount * FEE) if amount > 0 else 0.0


@dataclass
class Book:
    cash: float
    shares: float = 0.0
    trades: list = field(default_factory=list)
    cost_basis: float = 0.0
    nhi: float = 0.0
    fees: float = 0.0


def buy(b: Book, amount: float, price: float, day, info: dict):
    if amount <= 0 or price <= 0:
        return
    n = np.floor((amount - FEE_MIN) / (price * (1 + FEE)))
    if n <= 0:
        return
    gross = n * price
    f = fee(gross)
    b.cash -= gross + f
    b.shares += n
    b.cost_basis += gross + f
    b.fees += f
    b.trades.append({"date": day, "side": "買", "price": price, "shares": n, "amount": gross, "fee": f, **info})


def sell_all(b: Book, price: float, day, info: dict):
    if b.shares <= 0:
        return 0.0
    gross = b.shares * price
    f = fee(gross)
    tax = gross * TAX_ETF
    b.cash += gross - f - tax
    b.fees += f + tax
    b.trades.append({"date": day, "side": "賣", "price": price, "shares": b.shares, "amount": gross, "fee": f + tax, **info})
    b.shares, b.cost_basis = 0.0, 0.0
    return gross - f - tax


def run(px: pd.DataFrame, ratio: pd.Series, entry: float, exit_: float, mode: str, idle: pd.Series,
        start=None, end=None) -> dict:
    """mode：A＝一次全押；B＝三等份（entry、entry−5、entry−10）；hold＝買進持有（含息再投入）。
    訊號：t 日收盤後已知的維持率 → t+1 日開盤成交。股利：除息日入帳（閒置資金，持有期間不加碼）；
    hold 模式股利隔天開盤再投入。idle：日報酬（閒置資金）。"""
    px = px.loc[start:end]
    sig = ratio.reindex(px.index, method="ffill")            # 沒有維持率的日子沿用前一個
    b = Book(cash=float(CAPITAL))
    levels = [entry - TRANCHE_STEP * i for i in range(3)] if mode == "B" else [entry]
    filled = [False] * len(levels)
    tranche = CAPITAL / len(levels)
    pending_buy, pending_sell, reinvest = [], False, 0.0
    nav, inmkt, trips = [], [], []
    trip = None
    days = px.index
    for i, d in enumerate(days):
        row = px.loc[d]
        # 0) 閒置資金計息（前一天到今天的日曆天數）
        if i > 0:
            b.cash *= float(idle.loc[d])
        # 1) 分割：股數放大
        if row["split"] != 1 and b.shares > 0:
            b.shares *= row["split"]
        # 2) 除息日：前一天收盤還持有的股數領股利（當天開盤賣掉也領得到；當天才買的領不到），扣二代健保
        if row["div"] > 0 and b.shares > 0:
            g = b.shares * row["div"]
            nhi = g * NHI_RATE if g >= NHI_MIN else 0.0
            b.cash += g - nhi
            b.nhi += nhi
            if mode == "hold":
                reinvest = g - nhi
        # 3) 開盤：執行昨天收盤決定的交易
        if pending_sell:
            proceeds = sell_all(b, row["open"], d, {"ratio_signal": trip["exit_ratio"], "sim": bool(row.get("sim", False))})
            trip.update({"exit_date": d, "exit_price": row["open"], "proceeds": proceeds})
            trips.append(trip)
            trip, filled, pending_sell = None, [False] * len(levels), False
        for j, rv in pending_buy:
            amt = b.cash if mode == "A" else min(tranche, b.cash)
            if mode == "hold":
                amt = b.cash
            before = b.cash
            buy(b, amt, row["open"], d, {"ratio_signal": rv, "tranche": j + 1, "sim": bool(row.get("sim", False))})
            if trip is None:
                trip = {"entry_date": d, "entry_ratio": rv, "invested": 0.0, "tranches": 0}
            trip["invested"] += before - b.cash
            trip["tranches"] += 1
        pending_buy = []
        if mode == "hold" and reinvest > 0:
            buy(b, b.cash, row["open"], d, {"ratio_signal": np.nan, "tranche": 0, "sim": bool(row.get("sim", False))})
            reinvest = 0.0
        # 4) 收盤：決定明天的交易（hold 模式的股利在隔天開盤再投入）
        rv = float(sig.loc[d]) if not pd.isna(sig.loc[d]) else np.nan
        if mode == "hold":
            if i == 0:
                pending_buy = [(0, np.nan)]
        elif not np.isnan(rv):
            if b.shares > 0 and rv > exit_ and not pending_sell:
                pending_sell = True
                trip["exit_ratio"] = rv
                trip["exit_signal_date"] = d
            elif not pending_sell:
                for j, lv in enumerate(levels):
                    if not filled[j] and rv < lv:
                        filled[j] = True
                        pending_buy.append((j, rv))
                        if mode == "A":
                            break
        nav.append(b.cash + b.shares * row["close"])
        inmkt.append(b.shares > 0)
    s = pd.Series(nav, index=days)
    if trip is not None:                                           # 期末仍持有：未實現
        trip.update({"exit_date": days[-1], "exit_price": px["close"].iloc[-1], "exit_ratio": np.nan,
                     "proceeds": b.shares * px["close"].iloc[-1], "unrealized": True})
        trips.append(trip)
    return {"nav": s, "in_market": pd.Series(inmkt, index=days), "trades": b.trades, "trips": trips, "book": b}


def dca(px: pd.DataFrame, idle: pd.Series, start=None, end=None) -> dict:
    """每月第一個交易日開盤投入 CAPITAL ÷ 月數（未投入的錢不算在帳上，用 XIRR 算報酬）；股利再投入。"""
    px = px.loc[start:end]
    firsts = px.groupby(px.index.to_period("M")).head(1).index
    each = CAPITAL / len(firsts)
    b = Book(cash=0.0)
    flows, nav, inflow = [], [], []
    reinvest = 0.0
    for d in px.index:
        row = px.loc[d]
        if row["split"] != 1 and b.shares > 0:
            b.shares *= row["split"]
        if row["div"] > 0 and b.shares > 0:              # 先領股利（前一天收盤持有的股數）
            g = b.shares * row["div"]
            nhi = g * NHI_RATE if g >= NHI_MIN else 0.0
            b.cash += g - nhi
            pend_div = True
        else:
            pend_div = False
        inflow.append(each if d in firsts else 0.0)
        if d in firsts:
            b.cash += each
            flows.append((d, -each))
            buy(b, b.cash, row["open"], d, {"ratio_signal": np.nan, "tranche": 0, "sim": bool(row.get("sim", False))})
        elif reinvest > 0:
            buy(b, b.cash, row["open"], d, {"ratio_signal": np.nan, "tranche": 0, "sim": bool(row.get("sim", False))})
            reinvest = 0.0
        if pend_div:
            reinvest = b.cash                             # 隔天開盤再投入
        nav.append(b.cash + b.shares * row["close"])
    s = pd.Series(nav, index=px.index)
    fl = pd.Series(inflow, index=px.index)
    twr = ((s - fl) / s.shift(1)).where(s.shift(1) > 0).fillna(1.0).cumprod()      # 時間加權（扣掉新投入的錢）
    flows.append((px.index[-1], s.iloc[-1]))
    return {"nav": s, "twr": twr, "flows": flows, "xirr": xirr(flows), "invested": each * len(firsts)}


def xirr(flows) -> float:
    t0 = flows[0][0]
    ts = np.array([(d - t0).days / 365.25 for d, _ in flows])
    cf = np.array([c for _, c in flows])
    lo, hi = -0.99, 5.0
    f = lambda r: np.sum(cf / (1 + r) ** ts)  # noqa: E731
    for _ in range(200):
        mid = (lo + hi) / 2
        if f(lo) * f(mid) <= 0:
            hi = mid
        else:
            lo = mid
    return (lo + hi) / 2


# ================================================================ 指標
def metrics(nav: pd.Series, rf: pd.Series) -> dict:
    r = nav.pct_change().dropna()
    yrs = (nav.index[-1] - nav.index[0]).days / 365.25
    cagr = (nav.iloc[-1] / nav.iloc[0]) ** (1 / yrs) - 1
    vol = r.std() * np.sqrt(252)
    rf_d = (rf.reindex(r.index).ffill().bfill()) / 252
    sharpe = (r - rf_d).mean() / r.std() * np.sqrt(252) if r.std() > 0 else np.nan
    return {"年化報酬": cagr, "總報酬": nav.iloc[-1] / nav.iloc[0] - 1, "最大回撤": float((nav / nav.cummax() - 1).min()),
            "年化波動": vol, "Sharpe": sharpe}


def trip_table(res: dict, px: pd.DataFrame, ratio: pd.Series) -> pd.DataFrame:
    rows = []
    for t in res["trips"]:
        ret = t["proceeds"] / t["invested"] - 1 if t["invested"] else np.nan
        rows.append({"進場日": t["entry_date"].date(), "進場維持率": round(t["entry_ratio"], 1), "批數": t["tranches"],
                     "出場日": t["exit_date"].date(), "出場維持率": round(t.get("exit_ratio", np.nan), 1) if not pd.isna(t.get("exit_ratio", np.nan)) else "（未出場）",
                     "持有天數": (t["exit_date"] - t["entry_date"]).days, "投入": round(t["invested"]), "收回": round(t["proceeds"]),
                     "該筆報酬": ret, "未實現": bool(t.get("unrealized", False))})
    return pd.DataFrame(rows)


def longest_flat(inmkt: pd.Series, px_tr: pd.Series) -> tuple[int, str, float]:
    """最長空手期間（日曆天）、期間、這段時間標的含息漲幅。"""
    best, cur_start = (0, "", 0.0), None
    days = inmkt.index
    for i, d in enumerate(days):
        if not inmkt.iloc[i] and cur_start is None:
            cur_start = d
        if (inmkt.iloc[i] or i == len(days) - 1) and cur_start is not None:
            e = d
            n = (e - cur_start).days
            if n > best[0]:
                g = float(px_tr.loc[e] / px_tr.loc[cur_start] - 1)
                best = (n, f"{cur_start:%Y-%m-%d}～{e:%Y-%m-%d}", g)
            cur_start = None
    return best


# ================================================================ 主程式
def pct(v, d=1):
    return "" if v is None or (isinstance(v, float) and np.isnan(v)) else f"{v * 100:+.{d}f}%"


def md_table(df: pd.DataFrame) -> str:
    cols = list(df.columns)
    out = ["| " + " | ".join(map(str, cols)) + " |", "|" + "---|" * len(cols)]
    for _, r in df.iterrows():
        out.append("| " + " | ".join("" if (isinstance(v, float) and np.isnan(v)) else str(v) for v in r.values) + " |")
    return "\n".join(out)


def setup_fonts():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.rcParams["font.sans-serif"] = ["Heiti TC", "PingFang TC", "Arial Unicode MS", "STHeiti", "sans-serif"]
    plt.rcParams["axes.unicode_minus"] = False
    return plt


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    log = []

    def say(*a):
        s = " ".join(str(x) for x in a)
        print(s, flush=True)
        log.append(s)

    tpex, twse = load_ratio("tpex"), load_ratio("twse")
    rate = load_rate()
    e50, lev_real = load_etf("0050"), load_etf("00631L")
    say(f"上櫃維持率 {tpex.index.min():%Y-%m-%d}～{tpex.index.max():%Y-%m-%d}（{len(tpex)} 天）；上市 {twse.index.min():%Y-%m-%d}～{twse.index.max():%Y-%m-%d}（{len(twse)} 天）")
    say(f"0050 官方行情 {e50.index.min():%Y-%m-%d}～{e50.index.max():%Y-%m-%d}（{len(e50)} 天）；00631L {lev_real.index.min():%Y-%m-%d}～（{len(lev_real)} 天）")
    # ---- 驗證：分割、配息
    say("分割偵測（官方未還原價格，收盤÷前收<0.4）：0050", e50.attrs["splits_found"], "；設定", SPLITS["0050"],
        "｜00631L", lev_real.attrs["splits_found"], "；設定", SPLITS["00631L"])
    divs = e50.loc[e50["div"] > 0, "div"]
    say(f"0050 除息 {len(divs)} 次：" + "、".join(f"{d:%Y-%m-%d} {v:g}" for d, v in divs.items()))
    say(f"00631L 除息 {int((lev_real['div'] > 0).sum())} 次")
    tr50 = total_return_close(e50)
    yrs = (tr50.index[-1] - tr50.index[0]).days / 365.25
    say(f"0050 自己還原的含息報酬：{tr50.iloc[-1]:.2f} 倍，年化 {pct(tr50.iloc[-1] ** (1 / yrs) - 1)}"
        f"（未還原收盤 {e50['close'].iloc[0]:g} → {e50['close'].iloc[-1]:g}，中間 1 拆 4）")
    try:
        import yfinance as yf
        y = yf.Ticker("0050.TW").history(start="2015-01-01", auto_adjust=True)["Close"]       # Yahoo 早期有錯價，只比 2015 起
        y.index = y.index.tz_localize(None).normalize()
        y = y.reindex(tr50.index).dropna()
        a, b_ = tr50.reindex(y.index), y
        say(f"對照 Yahoo 還原價（同期間）：自己還原 {a.iloc[-1] / a.iloc[0]:.3f} 倍 vs Yahoo {b_.iloc[-1] / b_.iloc[0]:.3f} 倍")
    except Exception as ex:  # noqa
        say("Yahoo 對照失敗：", ex)

    lev, lev_start = simulate_lev(e50, lev_real, rate)
    lev_only = lev[~lev["sim"]].copy()
    # 模擬參數驗證：重疊期間（真實上市後）用同一公式模擬，跟真實比
    ov = e50.loc[lev_start:]
    r_sim = 2 * total_return_close(ov).pct_change().fillna(0) - (LEV_EXPENSE + rate.reindex(ov.index).ffill() + LEV_SPREAD) / 252
    adj_real = lev_only["close"] / lev_only["split"][::-1].cumprod()[::-1].shift(-1).fillna(1)
    real_r = (lev_only["close"] / (lev_only["close"].shift(1) / lev_only["split"])).fillna(1) - 1
    yrs2 = (ov.index[-1] - ov.index[0]).days / 365.25
    say(f"正二模擬公式驗證（{lev_start:%Y-%m-%d} 起重疊期間）：模擬年化 {pct((1 + r_sim).prod() ** (1 / yrs2) - 1)}"
        f" vs 真實 00631L {pct((1 + real_r).prod() ** (1 / yrs2) - 1)}；日報酬相關 {np.corrcoef(r_sim.reindex(real_r.index).fillna(0), real_r)[0, 1]:.3f}")
    del adj_real

    assets = {"0050": e50, "正二（2014-10 前為模擬）": lev, "正二（只用上市後真實資料）": lev_only}
    # 閒置資金 c：台灣金融業隔夜拆款利率（央行；一般人拿不到這個利率，接近銀行間資金成本，當「現金最低報酬」參考）
    from screener.macro_ind import fetch_cbc_overnight
    on = fetch_cbc_overnight() / 100
    on = on.reindex(pd.date_range(on.index.min(), pd.Timestamp.today().normalize(), freq="D")).ffill()
    idles = {"定存": rate, "貨幣ETF": IDLE_MMF, "隔夜拆款": on}
    rows, all_trips, results = [], [], {}
    for an, px in assets.items():
        start = max(px.index.min(), tpex.index.min())
        for iname, ia in idles.items():
            fac = idle_factor(px.loc[start:].index, ia)
            bh = run(px, tpex, 0, 0, "hold", fac, start=start)
            results[(an, "買進持有", iname)] = bh
            rows.append({"標的": an, "閒置資金": iname, "方案": "買進持有（含息再投入）", **metrics(bh["nav"], rate),
                         "交易趟數": 0, "≥20% 比例": "", "在市場時間": 1.0})
            for ent, ex in PAIRS:
                for mode in ("A", "B"):
                    res = run(px, tpex, ent, ex, mode, fac, start=start)
                    results[(an, f"{mode} {ent}/{ex}", iname)] = res
                    tt = trip_table(res, px, tpex)
                    done = tt[~tt["未實現"]] if len(tt) else tt
                    rows.append({"標的": an, "閒置資金": iname, "方案": f"{'一次全押' if mode == 'A' else '三批'} {ent}/{ex}",
                                 **metrics(res["nav"], rate), "交易趟數": f"{len(done)}" + ("＋1 未實現" if len(tt) > len(done) else ""),
                                 "≥20% 比例": f"{(done['該筆報酬'] >= 0.2).sum()}/{len(done)}" if len(done) else "0/0",
                                 "在市場時間": float(res["in_market"].mean())})
                    if len(tt):
                        tt.insert(0, "方案", f"{an}｜{'一次全押' if mode == 'A' else '三批'} {ent}/{ex}｜閒置={iname}")
                        all_trips.append(tt)
        # 定期定額
        d = dca(px, None, start=start)
        results[(an, "定期定額", "")] = d
        m = metrics(d["twr"], rate)                       # 波動、回撤、Sharpe 用時間加權報酬
        rows.append({"標的": an, "閒置資金": "", "方案": "每月定期定額（年化＝XIRR）", **m, "年化報酬": d["xirr"],
                     "總報酬": d["nav"].iloc[-1] / d["invested"] - 1, "交易趟數": "", "≥20% 比例": "", "在市場時間": np.nan})
    res_df = pd.DataFrame(rows)
    trips = pd.concat(all_trips, ignore_index=True) if all_trips else pd.DataFrame()
    trips.to_csv(OUT / "trades.csv", index=False, encoding="utf-8-sig")
    fills = []
    for (an, plan, iname), r in results.items():
        for t in r.get("trades", []):
            if plan not in ("買進持有", "定期定額"):
                fills.append({"標的": an, "方案": plan, "閒置": iname, **t})
    pd.DataFrame(fills).to_csv(OUT / "fills.csv", index=False, encoding="utf-8-sig")

    # ---- 空手最久
    flat_rows = []
    for an, px in assets.items():
        trc = total_return_close(px) if an == "0050" else (px["close"] / px["split"][::-1].cumprod()[::-1].shift(-1).fillna(1))
        for ent, ex in PAIRS:
            r = results[(an, f"A {ent}/{ex}", "定存")]
            n, span, g = longest_flat(r["in_market"], trc)
            flat_rows.append({"標的": an, "門檻": f"{ent}/{ex}", "最長空手（天）": n, "期間": span, "這段期間標的漲幅（含息）": pct(g)})
    flat = pd.DataFrame(flat_rows)

    # ---- 參數熱力圖（0050、一次全押、閒置＝定存）
    grid = {}
    for an in ("0050", "正二（2014-10 前為模擬）"):
        px = assets[an]
        start = max(px.index.min(), tpex.index.min())
        fac = idle_factor(px.loc[start:].index, rate)
        g = pd.DataFrame(index=list(GRID_ENTRY), columns=list(GRID_EXIT), dtype=float)
        gn = g.copy()
        for ent in GRID_ENTRY:
            for ex in GRID_EXIT:
                r = run(px, tpex, ent, ex, "A", fac, start=start)
                g.loc[ent, ex] = metrics(r["nav"], rate)["年化報酬"]
                gn.loc[ent, ex] = len(r["trips"])
        grid[an] = (g, gn)

    # ---- 樣本內／外（0050、一次全押、閒置＝定存）
    px = e50
    start = max(px.index.min(), tpex.index.min())
    fac_all = idle_factor(px.loc[start:].index, rate)
    is_rows = []
    best, best_v = None, -9
    for ent in GRID_ENTRY:
        for ex in GRID_EXIT:
            r = run(px, tpex, ent, ex, "A", fac_all, start=start, end=SPLIT_IS)
            v = metrics(r["nav"], rate)["年化報酬"]
            if v > best_v:
                best, best_v = (ent, ex), v
    oos_start = pd.Timestamp(SPLIT_IS) + pd.Timedelta(days=1)
    fac_oos = idle_factor(px.loc[oos_start:].index, rate)
    for name, (ent, ex) in (("樣本內最佳", best), ("網路說法 150/210", (150, 210))):
        r_in = run(px, tpex, ent, ex, "A", fac_all, start=start, end=SPLIT_IS)
        r_out = run(px, tpex, ent, ex, "A", fac_oos, start=oos_start)
        is_rows.append({"門檻": f"{name}（{ent}/{ex}）", "樣本內年化": pct(metrics(r_in["nav"], rate)["年化報酬"]),
                        "樣本內趟數": len(r_in["trips"]), "樣本外年化": pct(metrics(r_out["nav"], rate)["年化報酬"]),
                        "樣本外趟數": len(r_out["trips"]), "樣本外最大回撤": pct(metrics(r_out["nav"], rate)["最大回撤"])})
    bh_in = run(px, tpex, 0, 0, "hold", fac_all, start=start, end=SPLIT_IS)
    bh_out = run(px, tpex, 0, 0, "hold", fac_oos, start=oos_start)
    is_rows.append({"門檻": "0050 買進持有", "樣本內年化": pct(metrics(bh_in["nav"], rate)["年化報酬"]), "樣本內趟數": "",
                    "樣本外年化": pct(metrics(bh_out["nav"], rate)["年化報酬"]), "樣本外趟數": "",
                    "樣本外最大回撤": pct(metrics(bh_out["nav"], rate)["最大回撤"])})
    isos = pd.DataFrame(is_rows)

    # ---- 起始日敏感度（0050、一次全押 150/210、閒置＝定存；每年 1 月第一個交易日開始到最後）
    st_rows = []
    for y in range(2009, 2022):
        s0 = e50.index[e50.index >= f"{y}-01-01"][0]
        fac_y = idle_factor(e50.loc[s0:].index, rate)
        r = run(e50, tpex, 150, 210, "A", fac_y, start=s0)
        h = run(e50, tpex, 0, 0, "hold", fac_y, start=s0)
        mr, mh = metrics(r["nav"], rate), metrics(h["nav"], rate)
        st_rows.append({"起點": f"{s0:%Y-%m}", "策略年化": pct(mr["年化報酬"]), "買進持有年化": pct(mh["年化報酬"]),
                        "差距": pct(mr["年化報酬"] - mh["年化報酬"]), "策略最大回撤": pct(mr["最大回撤"]),
                        "持有最大回撤": pct(mh["最大回撤"]), "完成趟數": sum(1 for x in r["trips"] if not x.get("unrealized"))})
    start_sens = pd.DataFrame(st_rows)

    # ---- 事件研究：跌破門檻後 N 個月 0050 含息報酬 vs 任何一天（重疊樣本，只當參考）
    trc = total_return_close(e50)
    sig = tpex.reindex(trc.index, method="ffill")
    ev_rows = []
    for thr in (150, 145):
        below = sig < thr
        first = below & ~below.shift(1, fill_value=False)
        for n in (6, 12, 24):
            fwd = trc.shift(-int(n * 21)) / trc - 1
            ok = fwd.notna()
            ev_rows.append({"條件": f"維持率 <{thr}", "持有": f"{n} 個月",
                            "全部符合的日子（平均）": pct(fwd[below & ok].mean()), "天數": int((below & ok).sum()),
                            "每次剛跌破那天（平均）": pct(fwd[first & ok].mean()), "次數": int((first & ok).sum()),
                            "剛跌破後上漲的比例": f"{(fwd[first & ok] > 0).mean():.0%}" if (first & ok).any() else "",
                            "任何一天（平均）": pct(fwd[ok].mean())})
    events = pd.DataFrame(ev_rows)

    # ---- 上市維持率對照（同門檻＋換算門檻：上市融資成數 6 成 vs 上櫃 5 成 → 起始維持率 166% vs 200%，比例 0.833）
    tw_rows = []
    start = max(e50.index.min(), twse.index.min())
    fac = idle_factor(e50.loc[start:].index, rate)
    for label, ent, ex in (("同門檻", 150, 210), ("換算門檻（×0.833）", 125, 175)):
        r = run(e50, twse, ent, ex, "A", fac, start=start)
        tt = trip_table(r, e50, twse)
        done = tt[~tt["未實現"]] if len(tt) else tt
        tw_rows.append({"維持率": "上市", "門檻": f"{label} {ent}/{ex}", **{k: pct(v) if k != "Sharpe" else f"{v:.2f}" for k, v in metrics(r["nav"], rate).items()},
                        "趟數": f"{len(done)}" + ("＋1 未實現" if len(tt) > len(done) else ""),
                        "≥20%": f"{(done['該筆報酬'] >= 0.2).sum()}/{len(done)}" if len(done) else "0/0"})
    tw_cmp = pd.DataFrame(tw_rows)

    # ---- 圖
    plt = setup_fonts()
    # 1) 淨值
    fig, ax = plt.subplots(figsize=(11, 5.5))
    for key, lab in (((("0050", "A 150/210", "定存")), "0050 擇時 150/210（一次全押）"), (("0050", "買進持有", "定存"), "0050 買進持有"),
                     (("正二（2014-10 前為模擬）", "A 150/210", "定存"), "正二 擇時 150/210"), (("正二（2014-10 前為模擬）", "買進持有", "定存"), "正二 買進持有")):
        s = results[key]["nav"]
        ax.plot(s.index, s / CAPITAL, label=lab, lw=1.3)
    ax.axvspan(lev.index.min(), lev_start, color="grey", alpha=0.12, label="正二模擬期間")
    ax.set_yscale("log")
    ax.set_title("淨值（初始 1 = 100 萬元，對數刻度；閒置資金＝定存）")
    ax.legend(fontsize=9)
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(OUT / "nav.png", dpi=130)
    plt.close(fig)
    # 2) 維持率與買賣點
    fig, ax = plt.subplots(figsize=(11, 5))
    ax.plot(tpex.index, tpex, lw=0.8, label="上櫃融資維持率（官方數據推估）")
    ax.plot(twse.index, twse, lw=0.8, alpha=0.6, label="上市融資維持率")
    for y_, c in ((150, "green"), (145, "green"), (210, "red"), (205, "red")):
        ax.axhline(y_, color=c, ls="--", lw=0.7, alpha=0.7)
    for t in results[("0050", "A 150/210", "定存")]["trips"]:
        ax.scatter([t["entry_date"]], [tpex.asof(t["entry_date"])], color="green", marker="^", s=50, zorder=5)
        if not t.get("unrealized"):
            ax.scatter([t["exit_date"]], [tpex.asof(t["exit_date"])], color="red", marker="v", s=50, zorder=5)
    ax.set_title("融資維持率與買賣點（150 買／210 賣；▲買 ▼賣）")
    ax.legend(fontsize=9)
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(OUT / "ratio_signals.png", dpi=130)
    plt.close(fig)
    # 3) 熱力圖
    for i, (an, (g, gn)) in enumerate(grid.items()):
        fig, ax = plt.subplots(figsize=(8, 6))
        im = ax.imshow(g.values.astype(float) * 100, cmap="RdYlGn", aspect="auto")
        ax.set_xticks(range(len(g.columns)), g.columns)
        ax.set_yticks(range(len(g.index)), g.index)
        for a_ in range(len(g.index)):
            for b2 in range(len(g.columns)):
                ax.text(b2, a_, f"{g.iat[a_, b2] * 100:.1f}\n({int(gn.iat[a_, b2])})", ha="center", va="center", fontsize=7)
        ax.set_xlabel("出場門檻")
        ax.set_ylabel("進場門檻")
        ax.set_title(f"{an}：年化報酬 %（括號＝交易趟數；一次全押、閒置＝定存）")
        fig.colorbar(im, ax=ax)
        fig.tight_layout()
        fig.savefig(OUT / f"heatmap_{i}.png", dpi=130)
        plt.close(fig)

    # ---- 結果表（report.md 由人寫結論，這裡輸出所有表格）
    show = res_df.copy()
    for c in ("年化報酬", "總報酬", "最大回撤", "年化波動"):
        show[c] = show[c].map(pct)
    show["Sharpe"] = show["Sharpe"].map(lambda v: "" if pd.isna(v) else f"{v:.2f}")
    show["在市場時間"] = show["在市場時間"].map(lambda v: "" if pd.isna(v) else f"{v:.0%}")
    tt_show = trips.copy()
    if len(tt_show):
        tt_show["該筆報酬"] = tt_show["該筆報酬"].map(pct)
    with open(OUT / "results.md", "w") as f:
        f.write("# 計算結果（自動產生）\n\n## 資料檢查\n\n" + "\n".join(f"- {x}" for x in log) + "\n\n")
        f.write("## 策略與基準\n\n" + md_table(show) + "\n\n")
        f.write("## 每趟交易（只列閒置＝定存）\n\n" + md_table(tt_show[tt_show["方案"].str.contains("閒置=定存")]) + "\n\n")
        f.write("## 最長空手期間\n\n" + md_table(flat) + "\n\n")
        f.write("## 樣本內（2009～2017）／樣本外（2018～）\n\n" + md_table(isos) + "\n\n")
        f.write("## 上市維持率對照（0050、一次全押）\n\n" + md_table(tw_cmp) + "\n\n")
        f.write("## 起始日敏感度（0050、一次全押 150/210）\n\n" + md_table(start_sens) + "\n\n")
        f.write("## 事件研究（跌破後 N 個月 0050 含息報酬；重疊樣本）\n\n" + md_table(events) + "\n\n")
        for an, (g, gn) in grid.items():
            gg = (g * 100).round(1).astype(str) + "（" + gn.astype(int).astype(str) + "）"
            gg.index.name = "進場＼出場"
            f.write(f"## 參數格點：{an}（年化 %，括號＝趟數）\n\n" + md_table(gg.reset_index()) + "\n\n")
    res_df.to_csv(OUT / "summary.csv", index=False, encoding="utf-8-sig")
    print("輸出：", OUT)


if __name__ == "__main__":
    main()
