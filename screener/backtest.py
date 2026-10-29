"""月調倉回測：每月底依當時可得資料選股，次月持有；等權重；台幣計價。

兩種情境同時模擬：
  gross：不扣任何費用與稅
  net  ：扣手續費、證交稅、換匯價差、美股股利預扣 30%
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .common import log
from .engine import px_frame_monthly, ret_n_months, screen_growth, screen_market, select_with_buffer
from .etf import point_in_time_metrics, screen_and_rank
from .screen import market_regime


def wide(prices: pd.DataFrame, col: str) -> pd.DataFrame:
    return prices.pivot_table(index="month", columns="ticker", values=col, aggfunc="last").sort_index()


def _asset_class(ticker: str, kind: str) -> str:
    if kind in ("etf", "fill", "core"):
        return "tw_etf" if ticker.endswith((".TW", ".TWO")) else "us"
    return "tw_stock" if ticker.endswith((".TW", ".TWO")) else "us"


def screen_key(cfg: dict) -> str:
    """影響選股結果的設定（不含組合、停損、成本、回測區間）。設定相同的方案可共用選股結果。"""
    import hashlib
    import json
    sub = {k: v for k, v in cfg.items() if k not in ("portfolio", "risk", "backtest", "costs")}
    return hashlib.md5(json.dumps(sub, sort_keys=True, default=str).encode()).hexdigest()


def stock_styles(cfg: dict) -> list[tuple[str, float]]:
    """個股部位用哪些選股風格、各占多少：[("value", 1.0)]、[("growth", 1.0)] 或兩者各半。"""
    p = cfg.get("portfolio", {}) or {}
    st = p.get("stock_style", "value") or "value"
    if st == "blend":
        b = {k: float(v) for k, v in (p.get("blend") or {"value": 0.5, "growth": 0.5}).items() if float(v) > 0}
        tot = sum(b.values()) or 1.0
        return [(k, v / tot) for k, v in b.items()]
    return [(st, 1.0)]


def weight_picks(ids: list[str], mcap: dict, total: float, scheme: str = "equal",
                 cap_of_total: float | None = None) -> dict:
    """把 total 分給 ids。equal = 等權重；cap = 照市值；cap_capped = 照市值、單檔不超過 total × cap_of_total
    （超出的部分依市值比例分給其他檔；上限太低、分不完時退回等權重）。"""
    if not ids or total <= 0:
        return {}
    n = len(ids)
    if scheme == "equal" or not mcap:
        return {i: total / n for i in ids}
    w = np.array([float(mcap.get(i, np.nan)) for i in ids])
    w = np.where(np.isfinite(w) & (w > 0), w, np.nan)
    w = np.where(np.isnan(w), np.nanmedian(w) if np.isfinite(w).any() else 1.0, w)
    w = w / w.sum()
    if scheme == "cap_capped" and cap_of_total:
        cap = float(cap_of_total)
        if cap * n < 1:
            return {i: total / n for i in ids}
        for _ in range(100):
            over = w > cap + 1e-12
            if not over.any():
                break
            excess = float((w[over] - cap).sum())
            w[over] = cap
            free = w < cap - 1e-12
            if not free.any():
                break
            w[free] += excess * w[free] / w[free].sum()
    return {i: total * float(x) for i, x in zip(ids, w)}


class StopRule:
    """個股停損規則。

    basis: entry（買進價）／trailing（持有期間最高價）／vol（移動停損，幅度 = 個股月波動 × vol_mult）
    reentry: cooldown（固定冷卻月數）／ma（冷卻至少 1 個月且月收盤站上 10 個月均線才可買回）
    """

    def __init__(self, cfg: dict):
        c = (cfg.get("risk", {}) or {}).get("stop_loss", {}) or {}
        self.on = bool(c.get("enabled"))
        self.pct = float(c.get("pct", 0.15))
        self.basis = c.get("basis", "entry")
        self.vol_mult = float(c.get("vol_mult", 2.5))
        self.vol_min, self.vol_max = float(c.get("vol_min", 0.08)), float(c.get("vol_max", 0.35))
        self.apply = set(c.get("apply_to", ["tw", "us"]))
        self.cool = int(c.get("cooldown_months", 3))
        self.reentry = c.get("reentry", "cooldown")
        self.ma_months = int(c.get("reentry_ma_months", 10))
        self.delay = int(c.get("exit_delay_days", 1))
        self.cash = c.get("proceeds", "cash") == "cash"

    @property
    def trailing(self):
        return self.basis in ("trailing", "vol")

    def pct_for(self, adj: pd.DataFrame, tk: str, t) -> float:
        if self.basis != "vol" or tk not in adj:
            return self.pct
        s = adj[tk].loc[:t].dropna().iloc[-13:]
        sd = s.pct_change().dropna().std()
        if not np.isfinite(sd) or len(s) < 7:
            return self.pct
        return float(np.clip(self.vol_mult * sd, self.vol_min, self.vol_max))


def run_backtest(cfg: dict, panels: dict, infos: dict, prices: pd.DataFrame,
                 etf_static: pd.DataFrame, start: str | None = None, end: str | None = None,
                 progress=None, events: dict | None = None, daily: pd.DataFrame | None = None,
                 screen_cache: dict | None = None, collect: set | None = None,
                 macro: dict | None = None, month_rev: pd.DataFrame | None = None,
                 decide_last: bool = False) -> dict:
    """daily：日線含息還原價寬表（index=日期, columns=yahoo 代號），有的話停損逐日模擬。
    screen_cache：跨方案共用的選股結果快取。collect：收集曾進入排名名單的代號（用來決定要抓哪些日線）。
    decide_last：最後一個完整月份的月底也做一次選股決策（還沒有下個月的報酬），並放進 holdings（並行模擬用）。"""
    close, adj, vol = wide(prices, "close"), wide(prices, "adj_close"), wide(prices, "volume")
    fx = close["TWD=X"].dropna() if "TWD=X" in close else pd.Series(dtype=float)
    months = close.index
    bt = cfg["backtest"]
    start = pd.Timestamp(start or bt["start"]) + pd.offsets.MonthEnd(0)
    # 最後一個「完整」月份：yfinance 當月資料不完整，排除
    last_full = (pd.Timestamp.today().normalize() - pd.offsets.MonthEnd(1))
    end_ts = last_full if (end in (None, "latest") and bt.get("end", "latest") == "latest") \
        else pd.Timestamp(end or bt["end"]) + pd.offsets.MonthEnd(0)
    rebal = [m for m in months if start - pd.offsets.MonthEnd(1) <= m and (m <= end_ts if decide_last else m < end_ts)]
    pw = cfg["portfolio"]["weights"]
    max_n = cfg["portfolio"]["max_holdings_per_stock_sleeve"]
    k_etf = cfg["portfolio"]["etf_holdings"]
    costs = cfg["costs"]
    td = cfg["top_down"]
    skey = screen_key(cfg)
    if screen_cache is None:
        screen_cache = {}

    y_maps = {m: dict(zip(infos[m].index, infos[m]["yahoo"])) for m in infos}
    etf_tk = set(etf_static["ticker"]) | set(etf_static.get("tracking_index", pd.Series(dtype=str)).dropna())
    etf_prices = prices[prices["ticker"].isin(etf_tk)]
    nav = {"gross": 1.0, "net": 1.0}
    w_prev = {"gross": {}, "net": {}}
    records, holdings_log = [], []
    total_cost = 0.0
    total_wht = 0.0
    buf = cfg.get("buffer", {})
    mult = buf.get("rank_multiple", 2)
    held = {"tw": [], "us": [], "etf": []}
    rule = StopRule(cfg)
    dcols = set(daily.columns) if daily is not None and not daily.empty else set()
    # 停損狀態：每檔持股的參考價（買進價或最高價）、停損幅度；有日線用日線數值，沒有用月線
    ref, spct = {"tw": {}, "us": {}}, {"tw": {}, "us": {}}
    cooldown = {"tw": {}, "us": {}}  # id -> 最早可再買進的月份序號
    stops_log = []
    # 每一筆持股（買進到賣出）的紀錄，用來統計大賺／小賺／持平／小跌／大跌
    bench_of = {"tw": "0050.TW", "us": "SPY"}
    spells_open = {"tw": {}, "us": {}}   # sid -> {start, tk}
    spells = []

    def _span_ret(tk, d0, d1, use_daily, mult=1.0):
        """d0（月底）到 d1 的含息報酬；有日線時兩端都用日線，避免日線與月線的還原基準不同。
        d1 沒有股價（下市、停止交易）時用 d1 之前最後一筆；mult：下市折價（1 − 折價比例）。"""
        if use_daily and tk in dcols:
            a, b = _dlast(tk, d0), _dlast(tk, d1)
        else:
            a = adj.at[d0, tk] if tk in adj and d0 in adj.index else np.nan
            b = adj.at[d1, tk] if tk in adj and d1 in adj.index else np.nan
            if not np.isfinite(b) and tk in adj:
                s_ = adj[tk].loc[:d1].dropna()
                b = float(s_.iloc[-1]) if len(s_) else np.nan
        return float(b * mult / a - 1) if np.isfinite(a) and np.isfinite(b) and a > 0 else np.nan

    def _close_spell(mkt, sid, end, reason, use_daily=False, mult=1.0):
        sp = spells_open[mkt].pop(sid, None)
        if not sp:
            return
        r = _span_ret(sp["tk"], sp["start"], end, use_daily, mult)
        bk = bench_of[mkt]
        b = _span_ret(bk, sp["start"], end, use_daily and bk in dcols)
        if not use_daily and not np.isfinite(b):
            b = np.nan
        spells.append({"market": mkt, "id": sid, "start": sp["start"].strftime("%Y-%m"),
                       "end": pd.Timestamp(end).strftime("%Y-%m-%d"),
                       "months": round((pd.Timestamp(end) - sp["start"]).days / 30.44, 1),
                       "ret": r, "bench": b, "reason": reason})

    close_stack = [None]          # 月收盤價 stack 後的快取（產業規則的歷史股價淨值比用）
    # 景氣階段表（cycle.enabled 時依階段切換規則；未啟用也記錄階段供分析）
    ph_tables = {}
    if macro and macro.get("cycle_inputs"):
        from .cycle import tables as _cyc_tables
        try:
            ph_tables = _cyc_tables(macro["cycle_inputs"], cfg)
        except Exception as e:  # noqa
            log.warning("cycle tables failed: %s", e)

    def _phase(mkt, t):
        from .cycle import phase_at
        r = phase_at(ph_tables.get(mkt), t)
        return r["phase"] if r else None

    def _rf_at(mkt, t):
        """估值用無風險利率：美股用當時的 10 年期美債殖利率（沒有資料時用設定檔）。"""
        if mkt != "us" or not macro:
            return None
        s_ = (macro.get("dgs10") if isinstance(macro, dict) else None)
        if s_ is None or not len(s_):
            return None
        s_ = s_.loc[:t]
        return float(s_.iloc[-1]) / 100 if len(s_) else None

    def _madj(tk, t):
        return adj.at[t, tk] if tk in adj and t in adj.index else np.nan

    def _dlast(tk, t):
        """日線在 t（含）之前最後一筆。"""
        if tk not in dcols:
            return np.nan
        s = daily[tk].loc[:t].dropna()
        return float(s.iloc[-1]) if len(s) else np.nan

    def _ref_now(tk, t):
        return _dlast(tk, t) if tk in dcols else _madj(tk, t)

    def _reentry_ok(mkt, sid, i, t):
        until = cooldown[mkt].get(sid)
        if until is None:
            return True
        if rule.reentry == "ma":
            if i < until:
                return False
            tk = y_maps[mkt].get(sid)
            if tk not in adj:
                return False
            s = adj[tk].loc[:t].dropna().iloc[-rule.ma_months:]
            ok = len(s) >= rule.ma_months and s.iloc[-1] > s.mean()
            if ok:
                cooldown[mkt].pop(sid, None)
            return bool(ok)
        if i >= until:
            cooldown[mkt].pop(sid, None)
            return True
        return False

    # ---------- 選股風格、持股比重、下市公司 ----------
    styles = stock_styles(cfg)
    pcfg = cfg["portfolio"]
    scheme = pcfg.get("weighting", "equal") or "equal"
    max_w = float(pcfg.get("max_weight", 0.10) or 0.10)
    core_sat = pcfg.get("core_satellite") or {}
    gcfg = cfg.get("growth") or {}
    held_s = {}                     # (市場, 風格) -> 持股
    _inputs = {}                    # 本月的股價輸入（各風格共用）
    # 下市資訊（回測載入下市公司時才有）：(市場, 代號) -> (下市日, 最後交易日)
    delist = {}
    for mk_, inf_ in infos.items():
        if "delist_date" not in inf_:
            continue
        d_ = inf_[inf_["delist_date"].notna()]
        lt_col = d_["last_trade"] if "last_trade" in d_ else pd.Series(pd.NaT, index=d_.index)
        for sid, dd_, lt_ in zip(d_.index, d_["delist_date"], lt_col):
            delist[(mk_, str(sid))] = (pd.Timestamp(dd_), pd.Timestamp(lt_) if pd.notna(lt_) else None)
    tk_delist = {y_maps[mk_][sid]: (mk_, sid, dd_, lt_) for (mk_, sid), (dd_, lt_) in delist.items()
                 if sid in y_maps.get(mk_, {})}
    # 停止交易一段時間後才下市＝多半是經營出問題（不是被併購）：最後成交價再打折（預設 5 折）
    haircut = float(bt.get("delist_haircut", 0.5))
    halt_days = int(bt.get("delist_halt_days", 20))
    delist_exits = []

    def _get_ranked(style, mkt, t):
        if style == "index":            # 不選股：個股部位全部放大盤 ETF（0050／SPY）
            return pd.DataFrame(columns=["id", "rank", "pass_all", "market_cap"])
        ck = (skey, style, mkt, t)
        if ck in screen_cache:
            return screen_cache[ck]
        ym = y_maps[mkt]
        if (mkt, t) not in _inputs:
            _inputs[(mkt, t)] = (px_frame_monthly(close, vol, t, ym),
                                 ret_n_months(adj, t, td.get("sector_momentum_months", 6), ym),
                                 ret_n_months(adj, t, 11, ym, skip=1))
        px, r6, r12 = _inputs[(mkt, t)]
        if style == "growth":
            res = screen_growth(panels[mkt], infos[mkt], px, r6, t, cfg, mkt, ret12=r12,
                                events=(events or {}).get(mkt), month_rev=month_rev)
        else:
            if close_stack[0] is None and ((cfg.get("industry_rules") or {}).get("enabled")
                                           or (cfg.get("cycle") or {}).get("enabled")):
                cs_ = close.stack()
                cs_.index.names = ["m", "tk"]
                close_stack[0] = cs_
            ctx = {"adj": adj, "close": close_stack[0] if close_stack[0] is not None else close,
                   "ymap": ym, "index": td["regime_indices"][mkt],
                   "rf": _rf_at(mkt, t), "implied": False, "phase": _phase(mkt, t)}
            res = screen_market(panels[mkt], infos[mkt], px, r6, t, cfg, mkt, ret12=r12,
                                events=(events or {}).get(mkt), month_rev=month_rev, ctx=ctx)
        rk = res["ranked"]
        cols = [c_ for c_ in ("id", "rank", "pass_all", "market_cap") if c_ in rk]
        ranked = rk[cols].copy() if len(rk) else rk
        screen_cache[ck] = ranked
        return ranked

    for i, t in enumerate(rebal if decide_last else rebal[:-1]):
        t1 = rebal[i + 1] if i + 1 < len(rebal) else None
        # ---------- 選股 ----------
        targets = {}
        picks = {}
        n_stops_month = 0
        stock_meta = {}  # targets key -> (mkt, sid)
        # 信用利差風險訊號：成立時把指定部位按比例減碼（減下來的放現金）
        co = (cfg.get("risk", {}) or {}).get("credit_overlay", {}) or {}
        credit_off = False
        if co.get("enabled") and macro:
            from .macro import credit_signal
            credit_off = bool(credit_signal(macro, t, co).get("risk_off"))
        co_scale = float(co.get("scale", 0.5)) if credit_off else 1.0
        co_apply = set(co.get("apply_to", ["tw", "us"]))
        _inputs.clear()
        stock_share = {}
        for mkt, sleeve in (("tw", "tw_stock"), ("us", "us_stock")):
            if mkt not in panels or panels[mkt].empty:
                continue
            ym = y_maps[mkt]
            prev_held = list(held[mkt])
            ranked_by = {}
            for style, _sh in styles:
                rk = _get_ranked(style, mkt, t)
                if collect is not None and len(rk):
                    collect.update(ym[x] for x in rk["id"] if x in ym)
                ranked_by[style] = rk
            # 沒有日線的股票：退回月底檢查
            stopped = []
            if rule.on and mkt in rule.apply:
                for sid in held[mkt]:
                    tk = ym.get(sid)
                    if tk in dcols:
                        continue
                    p_now, r0 = _madj(tk, t), ref[mkt].get(sid)
                    if r0 and np.isfinite(p_now) and p_now / r0 - 1 <= -spct[mkt].get(sid, rule.pct):
                        stopped.append(sid)
                        cooldown[mkt][sid] = i + max(rule.cool if rule.reentry == "cooldown" else 1, 1)
                        stops_log.append({"date": t.strftime("%Y-%m-%d"), "market": mkt, "id": sid,
                                          "ret_from_ref": float(p_now / r0 - 1), "daily": False})
                held[mkt] = [h for h in held[mkt] if h not in stopped]
            blocked = {s_ for s_ in list(cooldown[mkt]) if not _reentry_ok(mkt, s_, i, t)}
            gone = {sid for (mk_, sid), (dd_, _lt) in delist.items() if mk_ == mkt and dd_ <= t}
            weight = pw[sleeve] * (co_scale if mkt in co_apply else 1.0)
            if td.get("regime_defensive"):
                idx_t = td["regime_indices"][mkt]
                if idx_t in close:
                    rg = market_regime(close[idx_t], t, td.get("regime_ma_months", 10))
                    if rg["bull"] is False:
                        weight *= 0.5
            fill = cfg["portfolio"].get("fill_with_index", {}) or {}
            min_n = int(fill.get("min_holdings", 0) or 0) if fill.get("enabled") else 0
            cs = float(core_sat.get(mkt, 0) or 0)
            ids = []
            for style, share in styles:
                rk = ranked_by[style]
                if len(rk) and (blocked or gone):
                    rk = rk[~rk["id"].isin(blocked | gone)]
                n_max = int(gcfg.get("max_holdings", max_n)) if style == "growth" else max_n
                prev_s = held_s.get((mkt, style), [])
                ids_s = select_with_buffer(rk, [h for h in prev_s if h in held[mkt] and h not in blocked], n_max, mult)
                held_s[(mkt, style)] = ids_s
                w_style = weight * share * (1 - cs)
                # 停損賣出的名額：proceeds = cash 時本月先放現金，下次調倉再遞補；redistribute 時直接分給其他持股
                n_stop_s = len([x for x in stopped if x in prev_s]) if rule.cash else 0
                min_s = int(round(min_n * share)) if min_n else 0
                slots = max(len(ids_s) + n_stop_s, min_s)
                if slots > 0 and w_style > 0:
                    mc = dict(zip(rk["id"], rk["market_cap"])) if len(rk) and "market_cap" in rk else {}
                    stock_total = w_style * len(ids_s) / slots
                    cap_rel = max_w * weight * (1 - cs) / stock_total if stock_total > 0 else None
                    for sid, w_ in weight_picks(ids_s, mc, stock_total, scheme, cap_rel).items():
                        k = ("stock", ym[sid])
                        targets[k] = targets.get(k, 0.0) + w_
                        stock_meta[k] = (mkt, sid)
                    n_fill = slots - len(ids_s) - n_stop_s
                    if n_fill > 0 and fill.get(mkt):
                        k = ("fill", fill[mkt])
                        targets[k] = targets.get(k, 0.0) + w_style * n_fill / slots
                for sid in ids_s:
                    if sid not in ids:
                        ids.append(sid)
            if cs > 0 and fill.get(mkt) and weight > 0:
                k = ("fill", fill[mkt])
                targets[k] = targets.get(k, 0.0) + weight * cs
            if rule.on and mkt in rule.apply:
                for sid in ids:
                    tk = ym.get(sid)
                    p_now = _ref_now(tk, t)
                    if sid not in ref[mkt] or not ref[mkt][sid]:
                        ref[mkt][sid] = p_now if np.isfinite(p_now) else None
                        spct[mkt][sid] = rule.pct_for(adj, tk, t)
                    elif rule.trailing and np.isfinite(p_now) and tk not in dcols:
                        ref[mkt][sid] = max(ref[mkt][sid], p_now)
                ref[mkt] = {k: v for k, v in ref[mkt].items() if k in ids}
                spct[mkt] = {k: v for k, v in spct[mkt].items() if k in ids}
            held[mkt] = ids
            for sid in prev_held:
                if sid not in ids:
                    _close_spell(mkt, sid, t, "停損（月底）" if sid in stopped else "剔除")
            for sid in ids:
                if sid not in spells_open[mkt] and ym.get(sid):
                    spells_open[mkt][sid] = {"start": t, "tk": ym[sid]}
            picks[mkt] = ids
            n_stops_month += len(stopped)
            # 個股部位裡真正買股票的比例（其餘是補位或固定的大盤 ETF）
            sw = sum(v for k_, v in targets.items() if k_[0] == "stock" and stock_meta.get(k_, ("",))[0] == mkt)
            fw = targets.get(("fill", fill.get(mkt)), 0.0) if fill.get(mkt) else 0.0
            stock_share[mkt] = sw / (sw + fw) if (sw + fw) > 0 else 0.0
        ek = ("etf", skey, t)
        if ek in screen_cache:
            er = screen_cache[ek]
        else:
            em = point_in_time_metrics(etf_static, etf_prices, fx, t)
            er = screen_and_rank(em, cfg["etf"])
            er = er[er["pass_all"]].copy()
            er["rank"] = np.arange(1, len(er) + 1)
            er["id"] = er["ticker"]
            er = er[["id", "ticker", "rank", "pass_all"]]
            screen_cache[ek] = er
        etf_ids = select_with_buffer(er, held["etf"], k_etf, mult)
        held["etf"] = etf_ids
        picks["etf"] = etf_ids
        for tk in etf_ids:
            if pw.get("etf", 0) > 0:
                targets[("etf", tk)] = pw["etf"] / len(etf_ids)
        # 固定部位（例如全球大盤 VT、債券）：照設定比例持有，每月再平衡
        for fs in cfg["portfolio"].get("fixed_sleeves", []) or []:
            w_s = float(fs.get("weight", 0) or 0) * (co_scale if "core" in co_apply else 1.0)
            hs = fs.get("holdings") or {}
            tot_h = sum(hs.values()) or 1.0
            for tk, hw in hs.items():
                if w_s > 0 and tk in adj and np.isfinite(adj.at[t, tk]):
                    targets[("core", tk)] = targets.get(("core", tk), 0.0) + w_s * hw / tot_h

        wlog = [{"kind": k[0], "ticker": k[1], "w": float(w_),
                 "mkt": stock_meta.get(k, (None, None))[0], "id": stock_meta.get(k, (None, None))[1]}
                for k, w_ in targets.items() if w_ > 1e-9]
        if t1 is None:
            # 最後一次決策（並行模擬用）：只記錄要持有什麼，還沒有報酬
            holdings_log.append({"month": t, "tw": picks.get("tw", []), "us": picks.get("us", []),
                                 "etf": picks.get("etf", []), "weights": wlog, "final": True})
            break

        # ---------- 月中逐日停損（有日線者） ----------
        mid_stop = {}  # key -> 出場時的累積報酬（當地幣、含息）
        if rule.on and dcols:
            for k, (mkt, sid) in stock_meta.items():
                tk = k[1]
                if mkt not in rule.apply or tk not in dcols or not ref[mkt].get(sid):
                    continue
                s = daily[tk].loc[:t1].dropna()
                s0 = s.loc[:t]
                if s0.empty:
                    continue
                p0 = float(s0.iloc[-1])
                path = s.loc[s.index > s0.index[-1]]
                if path.empty:
                    continue
                r0 = ref[mkt][sid]
                pc = spct[mkt].get(sid, rule.pct)
                hit = None
                for j, (d, p) in enumerate(path.items()):
                    if rule.trailing:
                        r0 = max(r0, p)
                    if p / r0 - 1 <= -pc:
                        hit = j
                        break
                if hit is None:
                    if rule.trailing:
                        ref[mkt][sid] = r0
                    continue
                # 觸發後隔 exit_delay_days 個交易日以收盤價賣出（模擬跳空、無法即時成交）
                full = daily[tk].loc[path.index[0]:].dropna()
                xj = min(hit + rule.delay, len(full) - 1)
                p_exit = float(full.iloc[xj])
                mid_stop[k] = p_exit / p0 - 1
                cooldown[mkt][sid] = i + 1 + max(rule.cool if rule.reentry == "cooldown" else 1, 1)
                stops_log.append({"date": full.index[xj].strftime("%Y-%m-%d"), "market": mkt, "id": sid,
                                  "ret_from_ref": float(p_exit / ref[mkt][sid] - 1) if not rule.trailing
                                  else float(p_exit / r0 - 1), "daily": True})
                held[mkt] = [h for h in held[mkt] if h != sid]
                _close_spell(mkt, sid, full.index[xj], "停損", use_daily=True)
                ref[mkt].pop(sid, None)
                spct[mkt].pop(sid, None)
                n_stops_month += 1

        # ---------- 持有一個月的報酬 ----------
        fx0, fx1 = fx.get(t, np.nan), fx.get(t1, np.nan)
        fx_r = fx1 / fx0 if fx0 and fx1 and np.isfinite(fx0) and np.isfinite(fx1) else 1.0
        rec = {"month": t1}
        for scen in ("gross", "net"):
            prev = w_prev[scen]
            # 交易成本：依權重變化
            cost = 0.0
            if scen == "net":
                keys = set(prev) | set(targets)
                for k in keys:
                    d = targets.get(k, 0.0) - prev.get(k, 0.0)
                    cls = _asset_class(k[1], k[0])
                    c = costs[cls]
                    rate = c["buy"] if d > 0 else c["sell"]
                    if cls == "us":
                        rate += costs.get("fx_spread", 0.0)
                    cost += abs(d) * rate
            port_r = 0.0
            sleeve_r = {"tw": [0.0, 0.0], "us": [0.0, 0.0], "etf": [0.0, 0.0], "core": [0.0, 0.0]}
            new_w = {}
            stop_cash = 0.0
            wht = 0.0
            for k, w in targets.items():
                tk = k[1]
                us = not tk.endswith((".TW", ".TWO"))
                if k in mid_stop:
                    r_loc = mid_stop[k]
                    r = (1 + r_loc) * (fx_r if us else 1.0) - 1
                    if scen == "net":
                        cls = _asset_class(tk, k[0])
                        rate = costs[cls]["sell"] + (costs.get("fx_spread", 0.0) if cls == "us" else 0.0)
                        r -= (1 + r) * rate
                    port_r += w * r
                    sleeve_r["us" if us else "tw"][0] += w * r
                    sleeve_r["us" if us else "tw"][1] += w
                    stop_cash += w * (1 + r)
                    continue
                a0, a1 = adj.at[t, tk] if tk in adj else np.nan, adj.at[t1, tk] if tk in adj else np.nan
                c0, c1 = close.at[t, tk] if tk in close else np.nan, close.at[t1, tk] if tk in close else np.nan
                if k[0] == "stock" and tk in tk_delist and np.isfinite(a0) and a0 > 0:
                    mk_, sid_, dd_, lt_ = tk_delist[tk]
                    if dd_ <= t1 or (not np.isfinite(a1) and lt_ is not None and lt_ <= t1):
                        # 本月下市（或停止交易後不再有成交）：以最後成交價出場；停止交易很久才下市者再打折
                        distressed = lt_ is not None and (dd_ - lt_).days > halt_days
                        mult_ = (1 - haircut) if distressed else 1.0
                        last = a1 if np.isfinite(a1) else a0
                        r = last * mult_ / a0 - 1
                        port_r += w * r
                        sleeve_r["tw" if not us else "us"][0] += w * r
                        sleeve_r["tw" if not us else "us"][1] += w
                        stop_cash += w * (1 + r)
                        if scen == "gross":
                            delist_exits.append({"date": (lt_ or dd_).strftime("%Y-%m-%d"), "market": mk_, "id": sid_,
                                                 "ret_month": float(r), "distressed": bool(distressed)})
                            _close_spell(mk_, sid_, t1, "下市（停止交易後）" if distressed else "下市", mult=mult_)
                            held[mk_] = [h for h in held[mk_] if h != sid_]
                            ref[mk_].pop(sid_, None)
                            spct[mk_].pop(sid_, None)
                        continue
                if not (np.isfinite(a0) and np.isfinite(a1)) or a0 <= 0:
                    r_loc, div = 0.0, 0.0
                else:
                    r_loc = a1 / a0 - 1
                    pr = c1 / c0 - 1 if (np.isfinite(c0) and np.isfinite(c1) and c0 > 0) else r_loc
                    div = max(r_loc - pr, 0.0)
                    if abs(r_loc) > 3:  # 明顯錯誤的報價
                        r_loc, div = 0.0, 0.0
                if scen == "net" and us:
                    r_loc -= costs.get("us_dividend_withholding", 0.0) * div
                    wht += w * costs.get("us_dividend_withholding", 0.0) * div
                r = (1 + r_loc) * (fx_r if us else 1.0) - 1
                port_r += w * r
                sl = k[0] if k[0] in ("etf", "core") else ("us" if us else "tw")
                sleeve_r[sl][0] += w * r
                sleeve_r[sl][1] += w
                new_w[k] = w * (1 + r)
            cash_w = max(0.0, 1.0 - sum(targets.values()))
            gross_val = port_r + 1.0  # 現金報酬 0
            nav_before = nav[scen] * (1 - cost)
            nav[scen] = nav_before * gross_val
            tot = sum(new_w.values()) + cash_w + stop_cash
            w_prev[scen] = {k: v / tot for k, v in new_w.items()} if tot > 0 else {}
            rec[f"ret_{scen}"] = (1 - cost) * gross_val - 1
            rec[f"nav_{scen}"] = nav[scen]
            if scen == "gross":
                for sl, (a, b) in sleeve_r.items():
                    rec[f"sleeve_{sl}"] = a / b if b > 0 else 0.0
            if scen == "net":
                rec["cost"] = cost
                rec["withholding"] = wht
                total_cost += cost
                total_wht += wht
        rec["n_tw"] = len(picks.get("tw", []))
        rec["n_us"] = len(picks.get("us", []))
        rec["n_etf"] = len(picks.get("etf", []))
        rec["n_stops"] = n_stops_month
        rec["stock_share_tw"] = stock_share.get("tw", np.nan)
        rec["stock_share_us"] = stock_share.get("us", np.nan)
        rec["credit_off"] = int(credit_off)
        rec["phase_tw"], rec["phase_us"] = _phase("tw", t), _phase("us", t)
        rec["cash_weight"] = max(0.0, 1.0 - sum(targets.values()))
        records.append(rec)
        holdings_log.append({"month": t, "tw": picks.get("tw", []), "us": picks.get("us", []),
                             "etf": picks.get("etf", []), "weights": wlog})
        if progress:
            progress(i + 1, len(rebal) - 1)
        if (i + 1) % 12 == 0:
            log.info("backtest %s nav gross %.3f net %.3f (tw %d us %d etf %d)", t1.date(),
                     nav["gross"], nav["net"], rec["n_tw"], rec["n_us"], rec["n_etf"])

    for mkt in spells_open:
        for sid in list(spells_open[mkt]):
            _close_spell(mkt, sid, rebal[-1], "持有中")
    res = pd.DataFrame(records).set_index("month") if records else \
        pd.DataFrame(columns=["ret_gross", "ret_net", "n_tw", "n_us"], index=pd.DatetimeIndex([], name="month"))
    # ---------- 基準 ----------
    bench = {}
    for b in bt.get("benchmarks", []):
        if b not in adj:
            continue
        s = adj[b].reindex([rebal[0]] + list(res.index))
        r = s.pct_change().iloc[1:]
        if not b.endswith((".TW", ".TWO")):
            f = fx.reindex([rebal[0]] + list(res.index))
            r = (1 + r) * (f / f.shift(1)).iloc[1:] - 1
        bench[b] = r.fillna(0.0)
    if {"0050.TW", "SPY", "VT"} <= set(bench):
        bench["混合基準(20/20/60)"] = 0.2 * bench["0050.TW"] + 0.2 * bench["SPY"] + 0.6 * bench["VT"]
    for b, r in bench.items():
        res[f"bench_{b}"] = r
    res.attrs["total_cost"] = total_cost
    res.attrs["total_withholding"] = total_wht
    res.attrs["stops"] = stops_log
    return {"monthly": res, "holdings": holdings_log, "stops": stops_log, "spells": spells,
            "delist_exits": delist_exits, "n_delisted_universe": len(delist)}


def perf_stats(r: pd.Series, rf: float = 0.0) -> dict:
    r = r.dropna()
    if r.empty:
        return {}
    nav = (1 + r).cumprod()
    years = len(r) / 12
    cagr = nav.iloc[-1] ** (1 / years) - 1 if years > 0 else np.nan
    vol = r.std() * np.sqrt(12)
    sharpe = (cagr - rf) / vol if vol > 0 else np.nan
    dd = nav / nav.cummax() - 1
    yearly = (1 + r).groupby(r.index.year).prod() - 1
    return {"total_return": float(nav.iloc[-1] - 1), "cagr": float(cagr), "vol": float(vol),
            "sharpe": float(sharpe), "max_drawdown": float(dd.min()),
            "best_month": float(r.max()), "worst_month": float(r.min()),
            "yearly": {int(k): float(v) for k, v in yearly.items()}, "months": int(len(r))}
