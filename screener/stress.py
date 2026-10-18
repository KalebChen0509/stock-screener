"""第二層壓力測試：用指數／ETF 的日線，測「停損規則本身」在歷史大崩盤中的效果。

不涉及選股（2016 年以前沒有完整財報可以重跑選股），只回答：
  同一套停損＋買回規則，在慢跌型（2000、2008、2022）與 V 型（2020）崩盤下，
  少跌多少？錯過多少反彈？被洗幾次？
注意：對「整個指數」停損等於擇時進出大盤，結果只能當作停損規則特性的參考。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .backtest import StopRule


def simulate(px: pd.Series, rule: StopRule, start, end) -> dict:
    """在 [start, end] 期間從第一天買進，套用停損與買回規則；出場後持有現金（報酬 0）。"""
    s = px.dropna()
    hist = s.loc[:end]
    s = hist.loc[start:]
    if len(s) < 20:
        return {}
    vals = np.empty(len(s))
    in_pos, value = True, 1.0
    p_prev = s.iloc[0]

    def pct_now(j):
        if rule.basis != "vol":
            return rule.pct
        h = hist.loc[:s.index[j]].iloc[-253:]
        sd = h.pct_change().dropna().std() * np.sqrt(21)
        return float(np.clip(rule.vol_mult * sd, rule.vol_min, rule.vol_max)) if np.isfinite(sd) else rule.pct

    ref, pc = float(s.iloc[0]), pct_now(0)
    exit_at, out_since, n_stops = None, None, 0
    ma_win = 21 * rule.ma_months
    for j, (d, p) in enumerate(s.items()):
        if in_pos:
            value *= p / p_prev
            if exit_at is None:
                if rule.trailing:
                    ref = max(ref, p)
                if rule.on and p / ref - 1 <= -pc:
                    exit_at = j + rule.delay
            if exit_at is not None and j >= exit_at:
                in_pos, out_since, exit_at = False, j, None
                n_stops += 1
        else:
            waited = j - out_since
            if rule.reentry == "ma":
                ok = waited >= 21 and p > hist.loc[:d].iloc[-ma_win:].mean()
            else:
                ok = waited >= 21 * rule.cool
            if ok:
                in_pos, ref, pc = True, float(p), pct_now(j)
        p_prev = p
        vals[j] = value
    nav = pd.Series(vals, index=s.index)
    return {"nav": nav, "stops": n_stops}


def episode_stats(px: pd.Series, rule: StopRule, ep: dict, rebound_months: int = 12) -> dict | None:
    s = px.dropna()
    a, tb = pd.Timestamp(ep["start"]), pd.Timestamp(ep["trough_by"])
    win = s.loc[a:tb]
    if len(win) < 20 or s.index[0] > a + pd.Timedelta(days=10):
        return None  # 這個標的當時還沒上市
    trough = win.idxmin()
    end = min(trough + pd.DateOffset(months=rebound_months), s.index[-1])
    sim = simulate(s, rule, a, end)
    if not sim:
        return None
    nav = sim["nav"]
    t_ = nav.index[nav.index.get_indexer([trough], method="nearest")[0]]
    return {
        "total": float(nav.iloc[-1] - 1),
        "fall": float(nav.loc[t_] - 1),
        "rebound": float(nav.iloc[-1] / nav.loc[t_] - 1),
        "mdd": float((nav / nav.cummax() - 1).min()),
        "stops": int(sim["stops"]),
        "trough": trough.strftime("%Y-%m-%d"),
        "end": nav.index[-1].strftime("%Y-%m-%d"),
    }


def run_stress(daily: pd.DataFrame, rules: dict[str, StopRule], cfg_stress: dict) -> dict:
    """daily：日線含息還原價寬表。rules：方案名稱 -> 停損規則（包含「無停損」）。"""
    out = {}
    for asset in cfg_stress.get("assets", []):
        if asset not in daily:
            continue
        px = daily[asset]
        out[asset] = {}
        for ep in cfg_stress.get("episodes", []):
            row = {}
            for name, rule in rules.items():
                st = episode_stats(px, rule, ep, cfg_stress.get("rebound_months", 12))
                if st:
                    row[name] = st
            if row:
                out[asset][ep["name"]] = row
    return out
