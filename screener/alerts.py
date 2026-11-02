"""盤中提醒：停損線、自己設的價位、均線／KD 訊號、合理價。

由 .github/workflows/alerts.yml 在台股、美股盤中每 30 分鐘執行一次（Yahoo 報價約延遲 15～20 分鐘）。
同一則提醒一天只寄一次。只是提醒，不會下單；停損的正式判斷仍以收盤價為準（隔天早上的日報）。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

TW_OPEN, TW_CLOSE = (9, 0), (13, 30)
US_OPEN, US_CLOSE = (9, 30), (16, 0)


def market_open(mkt: str, now_utc: pd.Timestamp) -> bool:
    tz, (oh, om), (ch, cm) = (("Asia/Taipei", TW_OPEN, TW_CLOSE) if mkt == "tw" else ("America/New_York", US_OPEN, US_CLOSE))
    t = pd.Timestamp(now_utc).tz_convert(tz)
    if t.weekday() >= 5:
        return False
    m = t.hour * 60 + t.minute
    return oh * 60 + om <= m <= ch * 60 + cm + 10


def _sma(c: np.ndarray, n: int) -> float | None:
    return float(c[-n:].mean()) if len(c) >= n else None


def _kd(h, l, c, n=9, a=3, d=3):
    k = dd = 50.0
    K, D = [], []
    for i in range(len(c)):
        if i >= n - 1:
            hi, lo = h[i - n + 1:i + 1].max(), l[i - n + 1:i + 1].min()
            rsv = 50.0 if hi == lo else 100 * (c[i] - lo) / (hi - lo)
            k = ((a - 1) * k + rsv) / a
            dd = ((d - 1) * dd + k) / d
        K.append(k)
        D.append(dd)
    return K, D


def check(watch: list[dict], bars: dict, cfg: dict) -> list[dict]:
    """watch：[{mkt, id, yahoo, tag, stop, fair_low, fair_base, fair_high, above, below}]
    bars：yahoo → 日線 DataFrame（open, high, low, close；最後一列是今天盤中）。回傳 [{key, text}]。"""
    rules = cfg.get("rules") or {}
    tech = cfg.get("technical") or {}
    out = []
    for w in watch:
        b = bars.get(w["yahoo"])
        if b is None or len(b) < 2:
            continue
        b = b.dropna(subset=["close"])
        c, px, prev = b["close"].to_numpy(float), float(b["close"].iloc[-1]), float(b["close"].iloc[-2])
        tag, k0 = w["tag"], f"{w['mkt']}:{w['id']}"
        chg = px / prev - 1
        head = f"{tag} 現價 {px:,.2f}（{chg:+.1%}）"
        if rules.get("stop", True) and w.get("stop") and px <= w["stop"]:
            out.append({"key": f"{k0}:stop", "text": f"🛑 {head}：跌破停損線 {w['stop']:,.2f}。若收盤仍在下方，明早日報會正式通知賣出"})
        if rules.get("levels", True):
            if w.get("above") and px >= w["above"]:
                out.append({"key": f"{k0}:above:{w['above']}", "text": f"🔔 {head}：漲到你設的 {w['above']:,.2f} 以上"})
            if w.get("below") and px <= w["below"]:
                out.append({"key": f"{k0}:below:{w['below']}", "text": f"🔔 {head}：跌到你設的 {w['below']:,.2f} 以下"})
        if rules.get("fair_value", True):
            if w.get("fair_low") and px <= w["fair_low"]:
                out.append({"key": f"{k0}:fair_low", "text": f"💰 {head}：低於合理價下緣 {w['fair_low']:,.2f}（偏便宜）"})
            if w.get("fair_high") and px >= w["fair_high"]:
                out.append({"key": f"{k0}:fair_high", "text": f"🔺 {head}：高於合理價上緣 {w['fair_high']:,.2f}（偏貴）"})
        if rules.get("technical", True) and len(c) >= 3:
            for n in tech.get("ma", [20, 60]):
                m_now, m_prev = _sma(c, n), _sma(c[:-1], n)
                if m_now is None or m_prev is None:
                    continue
                if prev < m_prev and px > m_now:
                    out.append({"key": f"{k0}:ma{n}:up", "text": f"📈 {head}：站上 {n} 日均線（{m_now:,.2f}）"})
                elif prev > m_prev and px < m_now:
                    out.append({"key": f"{k0}:ma{n}:dn", "text": f"📉 {head}：跌破 {n} 日均線（{m_now:,.2f}）"})
            n, a, d = (tech.get("kd") or [9, 3, 3])[:3]
            if len(c) >= n + 2:
                K, D = _kd(b["high"].to_numpy(float), b["low"].to_numpy(float), c, n, a, d)
                if K[-2] <= D[-2] and K[-1] > D[-1] and K[-1] < 30:
                    out.append({"key": f"{k0}:kd:golden", "text": f"📈 {head}：KD 低檔黃金交叉（K {K[-1]:.0f}／D {D[-1]:.0f}）"})
                elif K[-2] >= D[-2] and K[-1] < D[-1] and K[-1] > 70:
                    out.append({"key": f"{k0}:kd:dead", "text": f"📉 {head}：KD 高檔死亡交叉（K {K[-1]:.0f}／D {D[-1]:.0f}）"})
    return out


def new_only(alerts: list[dict], state: dict, today: str) -> tuple[list[dict], dict]:
    """同一則提醒一天只寄一次。"""
    sent = set(state.get(today, []))
    fresh = [a for a in alerts if a["key"] not in sent]
    state = {today: sorted(sent | {a["key"] for a in fresh})}      # 只留今天
    return fresh, state
