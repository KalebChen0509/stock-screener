"""每日更新：用最新價格與已公布財報做篩選，產生網站資料並發送通知。"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from screener import prices  # noqa: E402
from screener.backtest import wide  # noqa: E402
from screener.common import (CACHE_DIR, ROOT, SITE_DATA, load_etf_universe, load_strategy,  # noqa: E402
                             load_watchlist, log, write_json)
from screener.engine import ret_n_months, screen_market, select_with_buffer  # noqa: E402
from screener import events as evmod  # noqa: E402
from screener.etf import enrich_live, point_in_time_metrics, screen_and_rank, static_frame  # noqa: E402
from screener.loaders import load_infos, load_panels  # noqa: E402
from screener.notify import notify  # noqa: E402
from screener.screen import market_regime  # noqa: E402

STOCK_COLS = ["id", "name", "industry", "close", "market_cap", "pe", "pb", "earnings_yield",
              "fcf_yield", "book_to_price", "cash_ratio", "intangible_ratio", "lt_invest_ratio",
              "inventory_ratio", "peer_limit_inventory_ratio", "equity_ratio", "current_ratio",
              "quick_ratio", "gross_margin", "op_margin", "op_to_gross", "nonop_share", "revenue",
              "net_income", "cfo", "fcf", "cff", "cfo_to_ni", "fcf_to_ni", "roe", "profitable_3y",
              "share_growth", "cf_type", "cf_type_ok", "cfo_3y", "cfi_3y", "cff_3y", "cfo_positive_3y", "gross_margin_min3y", "gross_margin_avg3y", "op_margin_min3y",
              "op_margin_avg3y", "rd_to_revenue", "revenue_cagr_3y", "ret_12m", "mom_12m_pct", "pledge_ratio",
              "event_veto", "event_flags", "groups", "avg_traded_value", "n_quarters", "is_financial", "period_end",
              "top_down_score", "score", "pass_all", "failed"]
ETF_COLS = ["ticker", "name", "market", "style", "expense_ratio", "aum_usd", "avg_traded_value_usd",
            "age_years", "top10_weight", "div_yield_ttm", "pe", "pb", "tracking_vol", "value_tilt",
            "is_value", "score", "pass_all", "failed"]


def table(df: pd.DataFrame, cols: list[str]) -> dict:
    cols = [c for c in cols if c in df.columns]
    d = df[cols].copy()
    for c in d.columns:
        if pd.api.types.is_datetime64_any_dtype(d[c]):
            d[c] = d[c].dt.strftime("%Y-%m-%d")
        elif pd.api.types.is_float_dtype(d[c]):
            d[c] = d[c].replace([np.inf], 1e12).replace([-np.inf], -1e12).round(6)
    return {"cols": cols, "rows": d.astype(object).where(d.notna(), None).values.tolist()}


def px_now(close: pd.DataFrame, vol: pd.DataFrame, ymap: dict) -> pd.DataFrame:
    """最新收盤價（當月未完成的月 K 收盤即最新價）＋前 3 個完整月份的日均成交值。"""
    last = close.ffill().iloc[-1]
    full = close.index[close.index <= (pd.Timestamp.today() - pd.offsets.MonthEnd(1))]
    tail = full[-3:]
    tv = (vol.loc[tail] * close.loc[tail] / 21).mean()
    ids = pd.Series(ymap)
    out = pd.DataFrame({"close": ids.map(last), "avg_traded_value": ids.map(tv)})
    out.index.name = "id"
    return out


def quarterly_history(raw: pd.DataFrame, panel: pd.DataFrame, sid: str) -> list:
    r = raw[raw["id"] == sid].sort_values("period_end").tail(16)
    p = panel[panel["id"] == sid].set_index("period_end")
    out = []
    for row in r.itertuples():
        pe = row.period_end
        pr = p.loc[pe] if pe in p.index else None
        def g(k):
            return None if pr is None or pd.isna(pr.get(k)) else float(pr.get(k))
        out.append({"q": pe.strftime("%Y-%m"), "revenue": row.revenue, "net_income": row.net_income,
                    "cfo": row.cfo, "fcf": (row.cfo - (row.capex or 0)) if pd.notna(row.cfo) else None,
                    "cfi": getattr(row, "cfi", None), "cff": row.cff,
                    "gm": g("gross_margin"), "om": g("op_margin"), "cash_ratio": g("cash_ratio"),
                    "equity_ratio": g("equity_ratio"), "fcf_to_ni": g("fcf_to_ni"), "roe": g("roe")})
    return out


def check_stop_loss(cfg, state, held, infos, today, month_key):
    """檢查持股是否跌破停損。會就地修改 held（移除停損股）與 state（冷卻期、本月停損名單）。

    參考價優先用 config/positions.yaml 的實際買進成本；沒有的話用系統建議買進當天的收盤價。
    報酬含息：除息造成的帳面下跌不算虧損。
    """
    sl = (cfg.get("risk", {}) or {}).get("stop_loss", {}) or {}
    status, lines = {}, []
    if not sl.get("enabled"):
        return status, lines
    pct = float(sl.get("pct", 0.15))
    trail = sl.get("basis", "entry") in ("trailing", "vol")
    use_vol = sl.get("basis") == "vol"
    cool_m = int(sl.get("cooldown_months", 3)) if sl.get("reentry", "cooldown") == "cooldown" else 1
    pos = load_positions()
    ent = state.get("entry", {})
    tick = {}
    for mkt in sl.get("apply_to", ["tw", "us"]):
        for sid in held.get(mkt, []):
            if mkt in infos and sid in infos[mkt].index:
                tick[(mkt, sid)] = infos[mkt].at[sid, "yahoo"]
        # 自己原本的持股（positions.yaml 標 keep: true）也套用停損
        for sid, p_ in (pos.get(mkt) or {}).items():
            if (p_ or {}).get("keep") and not state.get("manual_stopped", {}).get(sid):
                tick[(mkt, sid)] = yahoo_of(infos, mkt, sid)
    if not tick:
        return status, lines
    try:
        d = prices.fetch_daily(sorted(set(tick.values())), period="5y")
    except Exception as e:  # noqa
        log.warning("stop-loss prices failed: %s", e)
        return status, lines
    if d.empty:
        return status, lines
    g = {t: x.sort_values("date").set_index("date") for t, x in d.groupby("ticker")}
    if state.get("stopped_month", {}).get("month") != month_key:
        state["stopped_month"] = {"month": month_key}
    for (mkt, sid), yt in tick.items():
        x = g.get(yt)
        if x is None or x.empty:
            continue
        p = pos.get(mkt, {}).get(sid) or {}
        e = ent.get(mkt, {}).get(sid) or {}
        d0 = pd.Timestamp(p.get("date") or e.get("date") or today)
        if p.get("keep") and not p.get("date"):
            d0 = x.index[-1]   # 原有持股沒填日期：報酬用總成本計算，只需最新價
        after = x[x.index >= d0]
        if after.empty:
            continue
        c_now, a_now = float(after["close"].iloc[-1]), float(after["adj_close"].iloc[-1])
        c0, a0 = float(after["close"].iloc[0]), float(after["adj_close"].iloc[0])
        manual = bool(p.get("keep"))
        sh_known = p.get("shares") or state.get("est_shares", {}).get(sid)
        if manual and p.get("cost_total") and not sh_known and p.get("value_now"):
            # 還沒填股數：用「目前市值 ÷ 今天股價」估算一次並記住
            sh_known = float(p["value_now"]) / c_now
            state.setdefault("est_shares", {})[sid] = sh_known
        if p.get("cost_total") and sh_known:
            ret = c_now * float(sh_known) / float(p["cost_total"]) - 1
        elif p.get("cost"):
            # 你的實際成本 × 期間股息還原倍數
            divf = (a_now / a0) / (c_now / c0) if c0 and c_now else 1.0
            ret = c_now / float(p["cost"]) * divf - 1
        else:
            ret = a_now / a0 - 1
        if trail and not manual:  # 移動停損：從持有期間最高點（含息）算回檔
            ret = a_now / float(after["adj_close"].max()) - 1
        pct_i = pct
        if use_vol:  # 停損幅度 = 進場前 12 個月的月報酬標準差 × vol_mult
            mh = x["adj_close"].loc[:d0].resample("ME").last().dropna().iloc[-13:]
            sd = mh.pct_change().dropna().std()
            if np.isfinite(sd) and len(mh) >= 7:
                pct_i = float(np.clip(float(sl.get("vol_mult", 2.5)) * sd, float(sl.get("vol_min", 0.08)),
                                      float(sl.get("vol_max", 0.35))))
        dist = ret + pct_i
        name = p.get("name") or (infos[mkt].at[sid, "name"] if mkt in infos and sid in infos[mkt].index else sid)
        stt = {"name": name, "manual": manual, "yahoo": yt, "entry_date": d0.strftime("%Y-%m-%d"), "ret": ret, "dist": dist,
               "triggered": bool(ret <= -pct_i), "cost": p.get("cost"), "pct": pct_i}
        status[(mkt, sid)] = stt
        if stt["triggered"] and manual:
            state.setdefault("manual_stopped", {})[sid] = today.strftime("%Y-%m-%d")
            lines.append(f"🛑 停損觸發：{sid} {name}（你原本的持股）自成本已 {ret:+.1%}，請賣出")
        elif stt["triggered"]:
            held[mkt] = [h for h in held[mkt] if h != sid]
            until = (today + pd.DateOffset(months=cool_m)).strftime("%Y-%m-%d")
            state.setdefault("cooldown", {}).setdefault(mkt, {})[sid] = until
            state["stopped_month"].setdefault(mkt, [])
            if sid not in state["stopped_month"][mkt]:
                state["stopped_month"][mkt].append(sid)
            ref = "最高點" if trail else ("你的成本" if p.get("cost") else f"{d0.date()} 買進價")
            again = f"{cool_m} 個月內不再買回" if sl.get("reentry", "cooldown") == "cooldown" else "至少 1 個月後、且站上 10 個月均線才會再買回"
            lines.append(f"🛑 停損觸發：{sid} {name} 自{ref}已 {ret:+.1%}，請賣出（{again}）")
        elif dist < 0.03:
            lines.append(f"⚠️ 接近停損：{sid} {name} 目前 {ret:+.1%}（再跌約 {dist:.1%} 觸發）")
    return status, lines


def yahoo_of(infos, mkt, sid) -> str:
    if mkt in infos and sid in infos[mkt].index:
        return infos[mkt].at[sid, "yahoo"]
    if mkt == "tw" and not str(sid).endswith((".TW", ".TWO")):
        return f"{sid}.TW"
    return str(sid)


def load_positions() -> dict:
    """config/positions.yaml：填你實際的買進成本與日期（選填）。"""
    import yaml
    p = ROOT / "config" / "positions.yaml"
    if not p.exists():
        return {}
    d = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    return {m: {str(k): v for k, v in (d.get(m) or {}).items()} for m in ("tw", "us")}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-notify", action="store_true")
    ap.add_argument("--no-live-etf", action="store_true")
    args = ap.parse_args()
    cfg = load_strategy()
    monthly = prices.load_monthly()
    close, adj, vol = wide(monthly, "close"), wide(monthly, "adj_close"), wide(monthly, "volume")
    infos = load_infos()
    panels, raws = load_panels(infos, monthly)
    today = pd.Timestamp.today().normalize()
    last_month = close.index[-1]
    fx_now = float(close["TWD=X"].dropna().iloc[-1])

    meta = {"repo": os.environ.get("GITHUB_REPOSITORY", ""), "updated": pd.Timestamp.now(tz="Asia/Taipei").strftime("%Y-%m-%d %H:%M"),
            "fx_usdtwd": fx_now, "regime": {}, "sectors": {}, "counts": {}}
    state_p = CACHE_DIR / "notify_state.json"
    state = json.loads(state_p.read_text()) if state_p.exists() else {}
    # 重大事件與質押
    ev_all = {}
    try:
        from screener.data_us import load_universe
        ev_all["us"] = evmod.load_us_events(load_universe())
    except Exception as e:  # noqa
        log.warning("US events: %s", e)
    try:
        ev_all["tw"] = evmod.update_tw_events()
    except Exception as e:  # noqa
        log.warning("TW events: %s", e)
    try:
        pl = evmod.update_tw_pledge()
        pledge = pl.set_index("id")["pledge_ratio"] if len(pl) else None
    except Exception as e:  # noqa
        log.warning("TW pledge: %s", e)
        pledge = None
    results = {}
    for mkt in ("tw", "us"):
        if mkt not in panels:
            continue
        ymap = dict(zip(infos[mkt].index, infos[mkt]["yahoo"]))
        px = px_now(close, vol, ymap)
        r6 = ret_n_months(adj, last_month, cfg["top_down"]["sector_momentum_months"], ymap)
        r12 = ret_n_months(adj, last_month, 11, ymap, skip=1)
        res = screen_market(panels[mkt], infos[mkt], px, r6, today, cfg, mkt, ret12=r12,
                            events=ev_all.get(mkt), pledge=pledge if mkt == "tw" else None)
        df = res["all"]
        rk = res["ranked"]
        df["top_down_score"] = df["industry"].map(res["sectors"]["score"]).fillna(50.0) \
            if len(res["sectors"]) else 50.0
        df["score"] = df["id"].map(rk.set_index("id")["score"]) if len(rk) else np.nan
        results[mkt] = (df, rk)
        write_json(SITE_DATA / f"stocks_{mkt}.json", table(df, STOCK_COLS))
        sec = res["sectors"].reset_index().rename(columns={"index": "industry"})
        meta["sectors"][mkt] = sec.round(4).to_dict("records")
        idx_t = cfg["top_down"]["regime_indices"][mkt]
        if idx_t in close:
            meta["regime"][mkt] = market_regime(close[idx_t], last_month, cfg["top_down"]["regime_ma_months"])
        meta["counts"][mkt] = {"universe": int(len(infos[mkt])), "with_data": int(len(df)),
                               "passed": int(df["pass_all"].sum())}
        log.info("%s: %d with data, %d passed", mkt, len(df), int(df["pass_all"].sum()))

    # ---------- ETF ----------
    etfs = load_etf_universe()
    base = static_frame(etfs, fx_now) if args.no_live_etf else enrich_live(etfs, fx_now)
    em = point_in_time_metrics(base, monthly, close["TWD=X"].dropna(), last_month)
    er = screen_and_rank(em, cfg["etf"])
    write_json(SITE_DATA / "etf.json", table(er, ETF_COLS))
    meta["counts"]["etf"] = {"universe": len(etfs), "passed": int(er["pass_all"].sum())}

    # ---------- 停損檢查（每天收盤後，含息報酬） ----------
    month_key = today.strftime("%Y-%m")
    held_prev = {k: list(v) for k, v in state.get("held", {}).items()}
    positions = load_positions()
    sl_status, stop_lines = check_stop_loss(cfg, state, held_prev, infos, today, month_key)

    # ---------- 目前建議投資組合 ----------
    pw = cfg["portfolio"]["weights"]
    max_n = cfg["portfolio"]["max_holdings_per_stock_sleeve"]
    port = []
    mult = cfg.get("buffer", {}).get("rank_multiple", 2)
    new_held = {}
    cool = state.get("cooldown", {})
    stopped_now = state.get("stopped_month", {}) if state.get("stopped_month", {}).get("month") == month_key else {}
    for mkt, sleeve in (("tw", "tw_stock"), ("us", "us_stock")):
        if mkt in results:
            rk = results[mkt][1]
            blocked = {k for k, until in cool.get(mkt, {}).items() if until > today.strftime("%Y-%m-%d")}
            if ((cfg.get("risk") or {}).get("stop_loss") or {}).get("reentry") == "ma":
                ma_n = int(cfg["risk"]["stop_loss"].get("reentry_ma_months", 10))
                for k in list(cool.get(mkt, {})):
                    yt = infos[mkt].at[k, "yahoo"] if k in infos[mkt].index else None
                    s_ = adj[yt].dropna().iloc[-ma_n:] if yt in adj else pd.Series(dtype=float)
                    if len(s_) < ma_n or s_.iloc[-1] <= s_.mean():
                        blocked.add(k)   # 還沒站上均線，不買回
            if blocked and len(rk):
                rk = rk[~rk["id"].isin(blocked)]
            ids = select_with_buffer(rk, [h for h in held_prev.get(mkt, []) if h not in blocked], max_n, mult)
            new_held[mkt] = ids
            top = rk.set_index("id").loc[ids].reset_index() if ids else rk.iloc[0:0]
            w_sl = float(pw.get(sleeve, 0) or 0)
            if w_sl <= 0:
                continue
            # 自己原本的持股（positions.yaml keep: true）：算一個名額、只持有不加碼
            manual = [sid for sid, p_ in (positions.get(mkt) or {}).items()
                      if (p_ or {}).get("keep") and not state.get("manual_stopped", {}).get(sid) and sid not in ids]
            # 本月停損賣出的名額先放現金，不加碼其他股票；名額太少時用大盤補位
            n_stop = len(stopped_now.get(mkt, []))
            fill = cfg["portfolio"].get("fill_with_index", {}) or {}
            min_n = int(fill.get("min_holdings", 0) or 0) if fill.get("enabled") else 0
            slots = max(len(top) + len(manual) + n_stop, min_n)
            if slots == 0:
                continue
            for r in top.itertuples():
                st_ = sl_status.get((mkt, r.id), {})
                port.append({"sleeve": mkt, "kind": "stock", "id": r.id, "key": yahoo_of(infos, mkt, r.id),
                             "market": mkt, "name": r.name, "weight": w_sl / slots,
                             "score": r.score, "cross_ok": bool(r.cross_ok), "industry": r.industry,
                             "entry_date": st_.get("entry_date"), "since_entry": st_.get("ret"),
                             "stop_dist": st_.get("dist")})
            for sid in manual:
                p_ = positions[mkt][sid]
                st_ = sl_status.get((mkt, sid), {})
                port.append({"sleeve": mkt, "kind": "stock", "id": sid, "key": yahoo_of(infos, mkt, sid),
                             "market": mkt, "name": p_.get("name") or sid, "weight": w_sl / slots, "no_buy": True,
                             "score": None, "cross_ok": None, "industry": "原有持股（只持有不加碼）",
                             "entry_date": st_.get("entry_date"), "since_entry": st_.get("ret"),
                             "stop_dist": st_.get("dist")})
            n_fill = slots - len(top) - len(manual) - n_stop
            if n_fill > 0 and fill.get(mkt):
                port.append({"sleeve": mkt, "kind": "fill", "id": fill[mkt], "key": fill[mkt], "market": mkt,
                             "name": f"大盤補位（{n_fill} 個空名額）", "weight": w_sl * n_fill / slots,
                             "fractional": mkt == "us", "score": None, "cross_ok": None, "industry": "指數 ETF"})
    erp = er[er["pass_all"]].copy()
    erp["rank"] = np.arange(1, len(erp) + 1)
    erp["id"] = erp["ticker"]
    etf_ids = select_with_buffer(erp, held_prev.get("etf", []), cfg["portfolio"]["etf_holdings"], mult)
    new_held["etf"] = etf_ids
    top_etf = erp.set_index("id").loc[etf_ids].reset_index() if etf_ids else erp.iloc[0:0]
    if float(pw.get("etf", 0) or 0) > 0:
        for r in top_etf.itertuples():
            port.append({"sleeve": "etf", "kind": "etf", "id": r.ticker, "key": r.ticker,
                         "market": "tw" if str(r.ticker).endswith((".TW", ".TWO")) else "us",
                         "name": r.name, "weight": pw["etf"] / len(top_etf),
                         "fractional": not str(r.ticker).endswith((".TW", ".TWO")),
                         "score": r.score, "cross_ok": None, "industry": r.style})
    etf_names = {e["ticker"]: e["name"] for e in etfs}
    for fs in cfg["portfolio"].get("fixed_sleeves", []) or []:
        hs = fs.get("holdings") or {}
        tot_h = sum(hs.values()) or 1.0
        for tk, hw in hs.items():
            us_ = not str(tk).endswith((".TW", ".TWO"))
            port.append({"sleeve": "core", "kind": "core", "id": tk, "key": tk, "market": "us" if us_ else "tw",
                         "name": etf_names.get(tk, fs.get("name", tk)), "weight": float(fs["weight"]) * hw / tot_h,
                         "fractional": us_, "score": None, "cross_ok": None, "industry": fs.get("name", "核心")})
    write_json(SITE_DATA / "portfolio.json", {
        "as_of": meta["updated"], "holdings": port,
        "stop_loss": (cfg.get("risk", {}) or {}).get("stop_loss", {}),
        "stopped": [{"market": m_, "id": i_, **v_} for (m_, i_), v_ in sl_status.items() if v_.get("triggered")],
        "cooldown": [{"market": m_, "id": k_, "until": u_} for m_, d_ in cool.items() for k_, u_ in d_.items()
                     if u_ > today.strftime("%Y-%m-%d")]})

    # ---------- 個股詳情（通過者、差 1~2 條者、追蹤清單） ----------
    wl = load_watchlist()
    detail_ids = {}
    for mkt in results:
        df = results[mkt][0]
        ids = set(df[df["fail_count"] <= 2]["id"]) | set(wl.get(mkt, []))
        detail_ids[mkt] = sorted(i for i in ids if i in infos[mkt].index)
    yahoo_list = [infos[m].at[i, "yahoo"] for m in detail_ids for i in detail_ids[m]]
    yahoo_list += [e["ticker"] for e in etfs]
    try:
        daily = prices.fetch_daily(yahoo_list, period="2y")
    except Exception as e:  # noqa
        log.warning("daily prices failed: %s", e)
        daily = pd.DataFrame()
    dmap = {t: g for t, g in daily.groupby("ticker")} if not daily.empty else {}
    det_dir = SITE_DATA / "detail"
    for mkt, ids in detail_ids.items():
        df = results[mkt][0].set_index("id")
        for sid in ids:
            yt = infos[mkt].at[sid, "yahoo"]
            g = dmap.get(yt)
            write_json(det_dir / mkt / f"{sid}.json", {
                "id": sid, "name": infos[mkt].at[sid, "name"],
                "prices": [] if g is None else [[d.strftime("%Y-%m-%d"), round(float(c), 4)]
                                                for d, c in zip(g["date"], g["close"])],
                "quarters": quarterly_history(raws[mkt], panels[mkt], sid),
                "failed": df.at[sid, "failed"] if sid in df.index else None,
            })
    for e in etfs:
        g = dmap.get(e["ticker"])
        write_json(det_dir / "etf" / f"{e['ticker']}.json", {
            "id": e["ticker"], "name": e["name"], "quarters": [],
            "prices": [] if g is None else [[d.strftime("%Y-%m-%d"), round(float(c), 4)]
                                            for d, c in zip(g["date"], g["close"])]})
    meta["detail_ids"] = {**detail_ids, "etf": [e["ticker"] for e in etfs]}

    prog = CACHE_DIR / "tw_progress.json"
    if prog.exists():
        meta["tw_backfill"] = json.loads(prog.read_text())
    write_json(SITE_DATA / "meta.json", meta)
    cfg_out = dict(cfg)
    write_json(SITE_DATA / "config.json", cfg_out)

    # ---------- 新聞與重大事件（持股＋追蹤清單） ----------
    news_items, ev_lines = [], []
    for h in port:
        grp = h["sleeve"] if h.get("kind") == "stock" else "etf"
        if (grp, h["id"]) not in {(a, b) for a, b, _ in news_items}:
            news_items.append((grp, h["id"], h["key"]))
    for m in ("tw", "us"):
        for sid in wl.get(m, []):
            if m in infos and sid in infos[m].index and (m, sid) not in {(a, b) for a, b, _ in news_items}:
                news_items.append((m, sid, infos[m].at[sid, "yahoo"]))
    fm = None
    if os.environ.get("FINMIND_TOKEN"):
        from screener.data_tw import FinMind
        fm = FinMind(max_calls=80)
    news = evmod.fetch_news(news_items, days=3, finmind=fm)
    wsj = evmod.fetch_wsj()
    comp = [(m, i, infos[m].at[i, "name"]) for m, i, _ in news_items if m in infos and i in infos[m].index]
    comp += [("etf", e["ticker"], e["name"]) for e in etfs if e["ticker"] in {i for m, i, _ in news_items if m == "etf"}]
    news += evmod.match_wsj(wsj, comp)
    flags = []
    for m in results:
        df = results[m][0]
        ids = {b for a, b, _ in news_items if a == m}
        sub_df = df[df["id"].isin(ids) & df["event_flags"].notna()]
        for r in sub_df.itertuples():
            flags.append({"market": m, "id": r.id, "name": r.name, "veto": bool(r.event_veto), "text": r.event_flags})
    write_json(SITE_DATA / "news.json", {"updated": meta["updated"], "news": news, "events": flags,
                                        "wsj_headlines": wsj[:30]})
    neg = [n for n in news if n["negative"]]
    if flags or neg:
        ev_lines.append("⚠️ 持股／追蹤清單的事件與負面新聞：")
        for f_ in flags[:10]:
            ev_lines.append(f"  {'⛔' if f_['veto'] else '•'} {f_['id']} {f_['name']}：{f_['text']}")
        for n in neg[:10]:
            ev_lines.append(f"  • {n['id']}：{n['title'][:60]}")

    # ---------- 通知 ----------
    cur = {m: sorted(results[m][0][results[m][0]["pass_all"]]["id"]) for m in results}
    cur["etf"] = list(top_etf["ticker"])
    lines = []
    changed = False
    for m, label in (("tw", "台股"), ("us", "美股"), ("etf", "ETF 前段班")):
        if m not in cur:
            continue
        prev = set(state.get("passed", {}).get(m, []))
        now = set(cur[m])
        new, gone = sorted(now - prev), sorted(prev - now)
        nm = (lambda i: f"{i} {infos[m].at[i, 'name']}" if m in infos and i in infos[m].index else i)
        lines.append(f"{label}：通過 {len(now)} 檔")
        if state.get("passed"):
            if new:
                lines.append("  新進：" + "、".join(nm(i) for i in new[:15]) + ("…" if len(new) > 15 else ""))
            if gone:
                lines.append("  移出：" + "、".join(nm(i) for i in gone[:15]) + ("…" if len(gone) > 15 else ""))
            changed |= bool(new or gone)
    wl_lines = []
    for m in ("tw", "us"):
        if m not in results:
            continue
        df = results[m][0].set_index("id")
        for sid in wl.get(m, []):
            if sid in df.index:
                ok = bool(df.at[sid, "pass_all"])
                why = "" if ok else "（未過：" + "、".join(df.at[sid, "failed"][:4]) + "）"
                wl_lines.append(f"  {sid} {df.at[sid, 'name']}：{'✅ 通過' if ok else '❌'}{why}")
    if wl_lines:
        lines.append("追蹤清單：")
        lines += wl_lines
    if ev_lines:
        lines += ev_lines
        changed = True
    if stop_lines:
        lines = stop_lines + [""] + lines
        changed = True
    rebalance = state.get("last_rebalance_month") != month_key
    # ---------- 下單清單：每月新投入的錢補足低於目標的部位，原則上不賣 ----------
    from screener.orders import plan_orders
    contrib = float(cfg["portfolio"].get("monthly_contribution_twd", 0) or 0)
    last_close = close.ffill().iloc[-1]
    # 月初：假設上個月清單已照做，把上月計畫併入持股
    plan_prev = state.get("plan") or {}
    shares = dict(state.get("shares", {}))
    cash = float(state.get("cash_twd", 0.0) or 0.0)
    if plan_prev.get("month") and plan_prev.get("month") != month_key:
        for o in plan_prev.get("orders", []):
            if o["action"] == "sell":
                shares.pop(o["key"], None)
                state.setdefault("sold_keys", {})[o["key"]] = plan_prev["month"]
            elif o["action"] == "buy":
                shares[o["key"]] = round(shares.get(o["key"], 0) + o["shares"], 4)
        cash = float(plan_prev.get("cash_after", 0.0))
        state["shares"], state["cash_twd"] = shares, cash
    # positions.yaml 有填股數的，以實際股數為準
    for m_, items in positions.items():
        for sid, p_ in items.items():
            p_ = p_ or {}
            k_ = yahoo_of(infos, m_, sid)
            if state.get("manual_stopped", {}).get(sid) and p_.get("keep"):
                if k_ not in state.get("sold_keys", {}):   # 停損後還沒賣：持續列在賣出清單
                    shares.setdefault(k_, p_.get("shares") or state.get("est_shares", {}).get(sid) or 0)
            elif p_.get("shares"):
                shares[k_] = float(p_["shares"])
            elif p_.get("keep") and state.get("est_shares", {}).get(sid):
                shares[k_] = float(state["est_shares"][sid])
    # 參考價（當地幣）
    keys = {h["key"] for h in port} | set(shares)
    px = {k: float(last_close.get(k)) for k in keys if np.isfinite(last_close.get(k, np.nan))}
    missing = sorted(k for k in keys if k not in px)
    if missing:
        try:
            dm = prices.fetch_daily(missing, period="5d")
            for k, g_ in dm.groupby("ticker"):
                px[k] = float(g_.sort_values("date")["close"].iloc[-1])
        except Exception as e:  # noqa
            log.warning("latest prices failed: %s", e)
    sell_keys = {yahoo_of(infos, m_, sid) for m_ in ("tw", "us") for sid in stopped_now.get(m_, [])}
    sell_keys |= {v_["yahoo"] for v_ in sl_status.values() if v_.get("triggered")}
    entries = [{**h, "fractional": bool(h.get("fractional"))} for h in port]
    names = {h["key"]: h["name"] for h in port}
    plan = plan_orders(entries, shares, px, fx_now, contrib, cash, sell_keys, names)
    for o in plan["orders"]:
        o["currency"] = "TWD" if o["market"] == "tw" else "USD"
        if o["price"] is not None:
            o["price"] = round(o["price"], 2)
    write_json(SITE_DATA / "orders.json", {
        "as_of": meta["updated"], "month": month_key, "fx_usdtwd": fx_now,
        "monthly_contribution_twd": contrib, **{k: v for k, v in plan.items() if k != "orders"},
        "orders": plan["orders"],
        "note": "以最新收盤價估算。持股假設你已照上個月清單下單；實際股數可填在 config/positions.yaml，系統會以那裡為準。"})
    if rebalance:
        lines.append("")
        lines.append(f"📅 {month_key} 本月投入 NT${contrib:,.0f}（核心／台股／美股 = "
                     f"{sum(h['weight'] for h in port if h['sleeve'] == 'core'):.0%}／"
                     f"{sum(h['weight'] for h in port if h['sleeve'] == 'tw'):.0%}／"
                     f"{sum(h['weight'] for h in port if h['sleeve'] == 'us'):.0%}）")
        lines.append("🧾 下單清單（以最新收盤估算）：")
        for o in plan["orders"]:
            if o["action"] == "sell":
                lines.append(f"  賣出 {o['id']} {o['name']} 全部（{o['reason']}）")
            elif o["action"] == "buy":
                amt = f"US${o['amount_local']:,.2f}" if o["market"] == "us" else f"NT${o['amount_twd']:,}"
                unit = f"{o['shares']} 股" if not float(o["shares"]).is_integer() else f"{int(o['shares']):,} 股"
                lines.append(f"  買進 {o['id']} {o['name']} {unit}（約 {amt}，{o['reason']}）")
        lines.append(f"  剩餘現金 NT${plan['cash_after_twd']:,} 留到下個月")
    site = os.environ.get("PAGES_URL", "")
    if site:
        lines.append(f"\n網站：{site}")
    if not args.no_notify and (changed or rebalance or os.environ.get("NOTIFY_ALWAYS") == "1"):
        r = notify(f"價值選股日報 {today.date()}", "\n".join(lines))
        log.info("notify result %s", r)
    else:
        log.info("no notification (changed=%s rebalance=%s)", changed, rebalance)
    state["passed"] = cur
    state["plan"] = {"month": month_key, "cash_after": plan["cash_after_twd"],
                     "orders": [{"key": o["key"], "action": o["action"], "shares": o["shares"]} for o in plan["orders"]]}
    if rebalance:
        state["last_rebalance_month"] = month_key
    if rebalance or not held_prev:
        state["held"] = new_held
    else:
        state["held"] = held_prev            # 月中只移除停損的股票，不換股
    ent = state.setdefault("entry", {})
    for mkt, ids in state["held"].items():
        if mkt == "etf":
            continue
        e = ent.setdefault(mkt, {})
        for sid in ids:
            if sid not in e:
                yt = infos[mkt].at[sid, "yahoo"] if mkt in infos and sid in infos[mkt].index else None
                pxe = float(last_close.get(yt, np.nan)) if yt else np.nan
                e[sid] = {"date": today.strftime("%Y-%m-%d"), "px": pxe if np.isfinite(pxe) else None}
        for sid in list(e):
            if sid not in ids:
                del e[sid]
    state_p.write_text(json.dumps(state, ensure_ascii=False))
    print("\n".join(lines))


if __name__ == "__main__":
    main()
