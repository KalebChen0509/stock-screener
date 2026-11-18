"""模擬投資帳本（2026/11/17，使用者：還沒投入真錢前，也要清楚自己的投資績效）。

兩條線分開算，每月都投入同樣的錢（預設 1 萬元，使用者的真實計畫）：
  系統線  照系統策略（預設「成長動能（上限 10%）＋月底檢查停損」）每月自動買賣
  我的線  照使用者在網站「我的模擬」記錄的買賣
  基準    同樣每月投入，全買 0050；全買「只買 ETF」（VT 50＋0050 25＋SPY 25）
資料只存在私人倉庫（stock-screener-private），由使用者的 Mac 計算（scripts/sim_book_run.py）。

規則（不偷看未來）：
  - 每月第一個交易日入金；現金不計利息。
  - 我的線：每筆買賣用「記錄時間之後，第一個收盤」的收盤價成交（台股 13:30、美股 16:00 紐約時間）。
    補記過去的日期也一樣，只能用記錄之後的收盤價，不能挑過去的價格。
  - 系統線：每個月底收盤做決策（並行模擬鎖定的持股），下一個交易日收盤成交。
    先賣掉不在名單裡的，再從落後目標最多的部位開始用現金補（不為了微調而賣出，省手續費）；
    單筆不到 500 元的先保留現金（每月 1 萬時，40 檔的策略每檔只有約 125 元，要等資產累積才買得齊）。
  - 持股用「含息還原價」計值（股利視為再投入）；顯示的股數是約略值。
  - 費用（永豐，參數在 COSTS）：台股手續費 0.1425% × 2 折、最低 1 元，賣出證交稅 個股 0.3%／ETF 0.1%；
    美股（豐存美股）買進 0.08%、無最低，賣出 0.18%、最低 3 美元，換匯價差單邊 0.05%。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

COSTS = {"tw_fee": 0.001425, "tw_disc": 0.2, "tw_min": 1.0, "tw_tax_stock": 0.003, "tw_tax_etf": 0.001,
         "us_buy": 0.0008, "us_buy_min": 0.0, "us_sell": 0.0018, "us_sell_min": 3.0, "fx_spread": 0.0005}
MIN_BUY = 500.0                     # 系統線：低於 500 元的買單不下（手續費占比太高）
TW_ETF = {"0050.TW", "0056.TW", "006208.TW", "00878.TW", "00631L.TW", "00632R.TW"}
ETF_ONLY = {"VT": 0.5, "0050.TW": 0.25, "SPY": 0.25}


def is_tw(tk: str) -> bool:
    return tk.endswith((".TW", ".TWO"))


def close_time_utc(d: pd.Timestamp, tw: bool) -> pd.Timestamp:
    """那一天收盤的時間（UTC）。"""
    d = pd.Timestamp(d).normalize()
    if tw:
        return (d + pd.Timedelta(hours=13, minutes=30)).tz_localize("Asia/Taipei").tz_convert("UTC")
    return (d + pd.Timedelta(hours=16)).tz_localize("America/New_York").tz_convert("UTC")


def fee_buy(tk: str, amt_twd: float, fx: float, c=COSTS) -> float:
    if is_tw(tk):
        return max(round(amt_twd * c["tw_fee"] * c["tw_disc"]), c["tw_min"])
    return max(amt_twd * c["us_buy"], c["us_buy_min"] * fx) + amt_twd * c["fx_spread"]


def fee_sell(tk: str, amt_twd: float, fx: float, c=COSTS) -> float:
    if is_tw(tk):
        tax = c["tw_tax_etf"] if tk in TW_ETF else c["tw_tax_stock"]
        return max(round(amt_twd * c["tw_fee"] * c["tw_disc"]), c["tw_min"]) + round(amt_twd * tax)
    return max(amt_twd * c["us_sell"], c["us_sell_min"] * fx) + amt_twd * c["fx_spread"]


@dataclass
class Book:
    """一條線的帳本。positions：代號 → 含息還原單位數（價值＝單位 × 還原價 × 匯率）。"""
    name: str
    cash: float = 0.0
    invested: float = 0.0
    fees: float = 0.0
    units: dict = field(default_factory=dict)
    cost: dict = field(default_factory=dict)       # 代號 → 累計買進金額（台幣，含費用）
    log: list = field(default_factory=list)
    nav: dict = field(default_factory=dict)        # 日期 → (市值, 累計投入)

    def value(self, px_adj: dict, fx: float) -> float:
        return self.cash + sum(u * px_adj[t] * (1 if is_tw(t) else fx) for t, u in self.units.items() if t in px_adj)


class Prices:
    """收盤價（成交用）、含息還原價（計值用）、匯率（美元→台幣）。index＝日期（各市場自己的交易日）。"""

    def __init__(self, close: pd.DataFrame, adj: pd.DataFrame, fx: pd.Series):
        self.close, self.adj = close.sort_index(), adj.sort_index()
        self.fx = fx.sort_index().dropna()

    def fx_at(self, d) -> float:
        s = self.fx.loc[:pd.Timestamp(d)]
        return float(s.iloc[-1]) if len(s) else float(self.fx.iloc[0])

    def fill(self, tk: str, after_utc: pd.Timestamp):
        """記錄時間之後的第一個收盤 → (日期, 收盤價, 還原價)；還沒有這個收盤 → None。"""
        if tk not in self.close:
            return None
        s = self.close[tk].dropna()
        for d in s.index[s.index >= after_utc.tz_convert("Asia/Taipei").tz_localize(None).normalize() - pd.Timedelta(days=1)]:
            if close_time_utc(d, is_tw(tk)) > after_utc:
                a = self.adj[tk].get(d, np.nan)
                return d, float(s[d]), float(a) if np.isfinite(a) else float(s[d])
        return None

    def asof(self, tk: str, d) -> tuple[float, float]:
        """d（含）以前最後一個收盤 → (收盤價, 還原價)；美股某天沒開盤時用前一個收盤。"""
        c = self.close[tk].loc[:pd.Timestamp(d)].dropna() if tk in self.close else pd.Series(dtype=float)
        if c.empty:
            return np.nan, np.nan
        a = self.adj[tk].get(c.index[-1], np.nan) if tk in self.adj else np.nan
        return float(c.iloc[-1]), float(a) if np.isfinite(a) and a > 0 else float(c.iloc[-1])

    def adj_asof(self, d) -> dict:
        a = self.adj.loc[:pd.Timestamp(d)].ffill()
        return a.iloc[-1].dropna().to_dict() if len(a) else {}


def buy(b: Book, tk: str, d, amt_twd: float, px: Prices, note: str = "") -> bool:
    fx = 1.0 if is_tw(tk) else px.fx_at(d)
    f = fee_buy(tk, amt_twd, fx)
    c, a = px.asof(tk, d)
    if amt_twd <= 0 or amt_twd + f > b.cash + 1e-6 or not np.isfinite(c):
        return False
    b.units[tk] = b.units.get(tk, 0.0) + amt_twd / fx / a
    b.cost[tk] = b.cost.get(tk, 0.0) + amt_twd + f
    b.cash -= amt_twd + f
    b.fees += f
    b.log.append({"date": pd.Timestamp(d).strftime("%Y-%m-%d"), "side": "買", "ticker": tk, "amount": round(amt_twd),
                  "price": c, "shares": round(amt_twd / fx / c, 4) if c else None, "fee": round(f, 1), "note": note})
    return True


def sell(b: Book, tk: str, d, px: Prices, frac: float = 1.0, note: str = "") -> bool:
    u = b.units.get(tk, 0.0)
    if u <= 0:
        return False
    fx = 1.0 if is_tw(tk) else px.fx_at(d)
    c, a = px.asof(tk, d)
    if not np.isfinite(c):
        return False
    frac = min(max(frac, 0.0), 1.0)
    gross = u * frac * a * fx
    f = fee_sell(tk, gross, fx)
    b.units[tk] = u * (1 - frac)
    cost_out = b.cost.get(tk, 0.0) * frac
    b.cost[tk] = b.cost.get(tk, 0.0) - cost_out
    if b.units[tk] <= 1e-12:
        b.units.pop(tk, None)
        b.cost.pop(tk, None)
    b.cash += gross - f
    b.fees += f
    b.log.append({"date": pd.Timestamp(d).strftime("%Y-%m-%d"), "side": "賣", "ticker": tk, "amount": round(gross),
                  "price": c, "shares": round(u * frac * a / c, 4) if c else None, "fee": round(f, 1),
                  "pnl": round(gross - f - cost_out), "note": note})
    return True


def deposit_days(days: pd.DatetimeIndex, start: str, end) -> list:
    """每個月第一個（台股）交易日。"""
    d = days[(days >= pd.Timestamp(start)) & (days <= pd.Timestamp(end))]
    return list(pd.Series(d, index=d).groupby(d.to_period("M")).first())


def rebalance_to(b: Book, targets: dict, d, px: Prices, note: str):
    """賣掉不在名單裡的，再用現金補足落後目標最多的部位（只買不微調賣）。"""
    for tk in list(b.units):
        if targets.get(tk, 0) <= 0:
            sell(b, tk, d, px, 1.0, note)
    adj = {t: px.asof(t, d)[1] for t in set(targets) | set(b.units)}
    fx = px.fx_at(d)
    hold = {t: b.units.get(t, 0.0) * adj[t] * (1 if is_tw(t) else fx) for t in targets if np.isfinite(adj.get(t, np.nan))}
    total = b.cash + sum(hold.values())
    gap = {t: max(w * total - hold.get(t, 0.0), 0.0) for t, w in targets.items() if t in hold}
    # 從落後最多的補起；不到最小下單金額的先留現金（小額投入時，40 檔的策略要等資產累積才買得齊）
    for t, g in sorted(gap.items(), key=lambda x: -x[1]):
        if g < MIN_BUY / 2:
            continue
        amt = math.floor(min(max(g, MIN_BUY), b.cash * 0.995))
        if amt >= MIN_BUY:
            buy(b, t, d, amt, px, note)


def mark(b: Book, d, px: Prices):
    b.nav[pd.Timestamp(d)] = (b.value(px.adj_asof(d), px.fx_at(d)), b.invested)


def run_target_line(name: str, decisions: dict, px: Prices, days: pd.DatetimeIndex, start: str, monthly: float,
                    end=None) -> Book:
    """系統線／基準：decisions＝{決策月底: {代號: 比重}}。
    決策月底之後的第一個交易日收盤成交（通常就是下個月的入金日）；沒有新決策的入金日，把新的錢補進落後的部位。"""
    end = pd.Timestamp(end or days[-1])
    b = Book(name)
    deps = set(deposit_days(days, start, end))
    dec = sorted((pd.Timestamp(k) + pd.offsets.MonthEnd(0), v) for k, v in decisions.items())
    applied, cur = None, None
    for d in days[(days >= pd.Timestamp(start)) & (days <= end)]:
        if d in deps:
            b.cash += monthly
            b.invested += monthly
        new = [x for x in dec if x[0] < d and (applied is None or x[0] > applied)]
        if new:
            applied, cur = new[-1]
            rebalance_to(b, cur, d, px, f"{applied:%Y-%m} 月底決策")
        elif d in deps and cur:
            rebalance_to(b, cur, d, px, "每月投入")
        mark(b, d, px)
    return b


def check_trades(trades: list[dict], px: "Prices") -> tuple[dict, list]:
    """網站送來的紀錄先檢查：格式不對、代號抓不到股價 → 不算進帳本，回傳 (狀態, 可以算的紀錄)。"""
    status, good = {}, []
    for t in trades:
        tk = t.get("ticker", "")
        try:
            amt_ok = t.get("side") != "buy" or float(t.get("amount") or 0) > 0
        except (TypeError, ValueError):
            amt_ok = False
        if not t.get("id") or not t.get("created") or t.get("side") not in ("buy", "sell") or not amt_ok:
            status[str(t.get("id"))] = {"status": "格式錯誤", "msg": "缺少日期、買賣方向或金額"}
        elif tk not in px.close.columns or px.close[tk].dropna().empty:
            status[t["id"]] = {"status": "代號錯誤", "msg": f"抓不到 {tk} 的股價（請確認代號；美股 A／B 股寫法如 BRK-B）"}
        else:
            good.append(t)
    return status, good


def run_my_line(trades: list[dict], px: Prices, days: pd.DatetimeIndex, start: str, monthly: float, end=None) -> tuple[Book, list]:
    """我的線：trades＝[{id, created（UTC ISO）, ticker, side: buy/sell, amount（台幣）或 frac（賣出比例）}]。
    回傳 (帳本, 每筆的狀態)。"""
    end = pd.Timestamp(end or days[-1])
    b = Book("我的模擬")
    deps = set(deposit_days(days, start, end))
    fills = []
    for t in trades:
        c = pd.Timestamp(t["created"])
        c = c.tz_localize("UTC") if c.tzinfo is None else c.tz_convert("UTC")
        f = px.fill(t["ticker"], c)
        fills.append((f[0] if f else None, t, f))
    status = {}
    queue = sorted([x for x in fills if x[0] is not None], key=lambda x: (x[0], str(x[1].get("created"))))
    for x in fills:
        if x[0] is None:
            status[x[1]["id"]] = {"status": "待成交", "msg": "還沒有記錄之後的收盤價（收盤後才會成交）"}
    qi = 0
    for d in days[(days >= pd.Timestamp(start)) & (days <= end)]:
        if d in deps:
            b.cash += monthly
            b.invested += monthly
        while qi < len(queue) and queue[qi][0] <= d:
            fd, t, f = queue[qi]
            qi += 1
            tk = t["ticker"]
            if t["side"] == "buy":
                ok = buy(b, tk, fd, float(t["amount"]), px, t.get("note", ""))
                status[t["id"]] = {"status": "已成交" if ok else "現金不足", "date": fd.strftime("%Y-%m-%d"), "price": f[1],
                                   "msg": "" if ok else f"可用現金 {b.cash:,.0f} 元，不夠買 {float(t['amount']):,.0f} 元（含手續費）"}
            else:
                frac = float(t.get("frac") or 1.0)
                ok = sell(b, tk, fd, px, frac, t.get("note", ""))
                status[t["id"]] = {"status": "已成交" if ok else "沒有持股", "date": fd.strftime("%Y-%m-%d"), "price": f[1],
                                   "msg": "" if ok else "這條線沒有這檔股票"}
        mark(b, d, px)
    return b, status


def xirr(flows: list[tuple[pd.Timestamp, float]]) -> float | None:
    """資金加權年化報酬（投入為負、期末市值為正）。"""
    if len(flows) < 2 or not any(v > 0 for _, v in flows) or not any(v < 0 for _, v in flows):
        return None
    t0 = flows[0][0]
    yrs = np.array([(d - t0).days / 365.25 for d, _ in flows])
    v = np.array([x for _, x in flows])
    if yrs[-1] < 30 / 365.25:
        return None                                 # 不到一個月不年化
    lo, hi = -0.99, 10.0
    f = lambda r: float(np.sum(v / (1 + r) ** yrs))  # noqa: E731
    if f(lo) * f(hi) > 0:
        return None
    for _ in range(200):
        mid = (lo + hi) / 2
        if f(lo) * f(mid) <= 0:
            hi = mid
        else:
            lo = mid
    return (lo + hi) / 2


def summarize(b: Book, px: Prices, names: dict | None = None) -> dict:
    if not b.nav:
        return {"name": b.name, "empty": True}
    s = pd.Series({d: v for d, (v, _) in b.nav.items()}).sort_index()
    inv = pd.Series({d: i for d, (_, i) in b.nav.items()}).sort_index()
    d_last = s.index[-1]
    # 時間加權報酬：扣掉入金的每日報酬連乘
    flow = inv.diff().fillna(inv.iloc[0])
    r = (s - flow) / s.shift(1) - 1
    r = r.iloc[1:].replace([np.inf, -np.inf], np.nan).fillna(0)
    twr = float((1 + r).prod() - 1)
    navi = (1 + r).cumprod()
    mdd = float((navi / navi.cummax() - 1).min()) if len(navi) else 0.0
    flows = [(d, -f) for d, f in flow.items() if f > 0] + [(d_last, float(s.iloc[-1]))]
    adj = px.adj_asof(d_last)
    fx = px.fx_at(d_last)
    hold = []
    for t, u in sorted(b.units.items()):
        if t not in adj:
            continue
        v = u * adj[t] * (1 if is_tw(t) else fx)
        c = b.cost.get(t, 0.0)
        # 今天漲跌：這檔最後兩個收盤（含息還原價）；網站總覽用
        a2 = px.adj[t].loc[:d_last].dropna() if t in px.adj else pd.Series(dtype=float)
        day = float(a2.iloc[-1] / a2.iloc[-2] - 1) if len(a2) >= 2 and a2.iloc[-2] > 0 else None
        hold.append({"ticker": t, "name": (names or {}).get(t, ""), "value": round(v), "cost": round(c),
                     "pnl": round(v - c), "ret": (v / c - 1) if c else None, "weight": v / s.iloc[-1] if s.iloc[-1] else 0,
                     "day": day, "day_date": a2.index[-1].strftime("%Y-%m-%d") if len(a2) else None})
    hold.sort(key=lambda x: -x["value"])
    return {"name": b.name, "as_of": d_last.strftime("%Y-%m-%d"), "value": round(float(s.iloc[-1])), "invested": round(float(inv.iloc[-1])),
            "cash": round(b.cash), "fees": round(b.fees), "pnl": round(float(s.iloc[-1] - inv.iloc[-1])),
            "twr": twr, "xirr": xirr(flows), "mdd": mdd, "holdings": hold, "trades": b.log[-200:],
            "nav": [[d.strftime("%Y-%m-%d"), round(float(v)), round(float(i))] for d, v, i in zip(s.index, s.values, inv.values)]}
