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
from screener.common import (CACHE_DIR, SITE_DATA, load_etf_universe, load_strategy,  # noqa: E402
                             load_watchlist, log, write_json)
from screener.engine import ret_n_months, screen_market  # noqa: E402
from screener.etf import enrich_live, point_in_time_metrics, screen_and_rank, static_frame  # noqa: E402
from screener.loaders import load_infos, load_panels  # noqa: E402
from screener.notify import notify  # noqa: E402
from screener.screen import market_regime  # noqa: E402

STOCK_COLS = ["id", "name", "industry", "close", "market_cap", "pe", "pb", "earnings_yield",
              "fcf_yield", "book_to_price", "cash_ratio", "intangible_ratio", "lt_invest_ratio",
              "inventory_ratio", "peer_limit_inventory_ratio", "equity_ratio", "current_ratio",
              "quick_ratio", "gross_margin", "op_margin", "op_to_gross", "nonop_share", "revenue",
              "net_income", "cfo", "fcf", "cff", "cfo_to_ni", "fcf_to_ni", "roe", "profitable_3y",
              "share_growth", "cf_type", "cf_type_ok", "cfo_3y", "cfi_3y", "cff_3y", "cfo_positive_3y", "avg_traded_value", "n_quarters", "is_financial", "period_end",
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
    results = {}
    for mkt in ("tw", "us"):
        if mkt not in panels:
            continue
        ymap = dict(zip(infos[mkt].index, infos[mkt]["yahoo"]))
        px = px_now(close, vol, ymap)
        r6 = ret_n_months(adj, last_month, cfg["top_down"]["sector_momentum_months"], ymap)
        res = screen_market(panels[mkt], infos[mkt], px, r6, today, cfg, mkt)
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

    # ---------- 目前建議投資組合 ----------
    pw = cfg["portfolio"]["weights"]
    max_n = cfg["portfolio"]["max_holdings_per_stock_sleeve"]
    port = []
    for mkt, sleeve in (("tw", "tw_stock"), ("us", "us_stock")):
        if mkt in results:
            top = results[mkt][1].head(max_n)
            for r in top.itertuples():
                port.append({"sleeve": mkt, "id": r.id, "name": r.name, "weight": pw[sleeve] / len(top),
                             "score": r.score, "cross_ok": bool(r.cross_ok), "industry": r.industry})
    top_etf = er[er["pass_all"]].head(cfg["portfolio"]["etf_holdings"])
    for r in top_etf.itertuples():
        port.append({"sleeve": "etf", "id": r.ticker, "name": r.name, "weight": pw["etf"] / len(top_etf),
                     "score": r.score, "cross_ok": None, "industry": r.style})
    write_json(SITE_DATA / "portfolio.json", {"as_of": meta["updated"], "holdings": port})

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

    # ---------- 通知 ----------
    state_p = CACHE_DIR / "notify_state.json"
    state = json.loads(state_p.read_text()) if state_p.exists() else {}
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
    month_key = today.strftime("%Y-%m")
    rebalance = state.get("last_rebalance_month") != month_key
    if rebalance:
        lines.append("")
        lines.append(f"📅 {month_key} 月初調倉建議（台股 {pw['tw_stock']:.0%}／美股 {pw['us_stock']:.0%}／ETF {pw['etf']:.0%}）：")
        for sl, label in (("tw", "台股"), ("us", "美股"), ("etf", "ETF")):
            hs = [h for h in port if h["sleeve"] == sl]
            if hs:
                lines.append(f"{label}（每檔 {hs[0]['weight']:.2%}）：" + "、".join(f"{h['id']} {h['name']}" for h in hs))
            else:
                lines.append(f"{label}：無符合條件標的，持有現金")
    site = os.environ.get("PAGES_URL", "")
    if site:
        lines.append(f"\n網站：{site}")
    if not args.no_notify and (changed or rebalance or os.environ.get("NOTIFY_ALWAYS") == "1"):
        r = notify(f"價值選股日報 {today.date()}", "\n".join(lines))
        log.info("notify result %s", r)
        if rebalance and any(r.values()):
            state["last_rebalance_month"] = month_key
    else:
        log.info("no notification (changed=%s rebalance=%s)", changed, rebalance)
    state["passed"] = cur
    state_p.write_text(json.dumps(state, ensure_ascii=False))
    print("\n".join(lines))


if __name__ == "__main__":
    main()
