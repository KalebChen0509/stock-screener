"""每日資料品質檢查：找出可能算錯的數字，網站與日報提醒（不改變選股結果）。

個股：
  mcap_suspect   市值可能算錯（美股：虧損、營收很小，市值卻很大；常見於 ADR 股數換算比例）
  rev_missing    近四季營收缺漏
  shares_jump    股數一年內變動超過 2 倍或減少 60% 以上（可能是分割、合併或資料錯誤）
  stale_fin      最新財報超過 200 天（可能停止申報或抓取失敗）
  stale_px       股價超過 10 天沒更新
  px_spike       近 12 個月有單月漲跌超過 +300%／−80%
整體：股數缺漏的家數、股價歷史太短的家數、美股財報最後更新日、各市場有資料的比例
"""
from __future__ import annotations

import numpy as np
import pandas as pd

LABEL = {"mcap_suspect": "市值可能算錯", "rev_missing": "營收缺漏", "shares_jump": "股數變動異常", "stale_fin": "財報過舊",
         "stale_px": "股價沒更新", "px_spike": "股價單月暴漲暴跌"}
SEVERE = {"mcap_suspect"}


def stock_issues(df: pd.DataFrame, mkt: str, close: pd.DataFrame, ymap: dict, today) -> list[dict]:
    today = pd.Timestamp(today).normalize()
    out = []
    if df is None or df.empty:
        return out
    d = df.set_index("id")
    rev = pd.to_numeric(d.get("revenue"), errors="coerce")
    mc = pd.to_numeric(d.get("market_cap"), errors="coerce")
    ni = pd.to_numeric(d.get("net_income"), errors="coerce")
    if mkt == "us":
        # 虧損又沒什麼營收、市值卻超過 500 億美元：多半是 ADR 股數換算比例錯（例如 1 單位 ADR = 多股普通股）
        sus = (mc > 5e10) & (ni <= 0) & (rev.isna() | (rev <= 0) | (mc / rev.where(rev > 0) > 30))
        for sid in d.index[sus.fillna(False)]:
            rv = "沒有營收資料" if pd.isna(rev[sid]) else f"近四季營收 {rev[sid] / 1e8:,.1f} 億美元"
            out.append({"id": sid, "kind": "mcap_suspect", "text": f"算出的市值 {mc[sid] / 1e8:,.0f} 億美元，但{rv}、沒有獲利"})
    for sid in d.index[(rev.isna() & mc.notna()).fillna(False)]:
        out.append({"id": sid, "kind": "rev_missing", "text": "近四季營收缺漏（營收相關條件會判為不通過）"})
    sg = pd.to_numeric(d.get("share_growth"), errors="coerce")
    for sid in d.index[((sg > 2) | (sg < -0.6)).fillna(False)]:
        out.append({"id": sid, "kind": "shares_jump", "text": f"股數一年內變動 {sg[sid]:+.0%}"})
    pe = pd.to_datetime(d.get("period_end"), errors="coerce")
    for sid in d.index[(pe < today - pd.Timedelta(days=200)).fillna(False)]:
        out.append({"id": sid, "kind": "stale_fin", "text": f"最新財報期別 {pe[sid].date()}"})
    if close is not None and len(close):
        lastm = close.index[-1]
        ys = [ymap.get(s) for s in d.index]
        ys = [y for y in ys if y in close.columns]
        lv = close[ys].apply(lambda s: s.last_valid_index())
        rev_map = {v: k for k, v in ymap.items()}
        for y, t in lv.items():
            if t is not None and t < lastm - pd.offsets.MonthEnd(1):
                out.append({"id": rev_map[y], "kind": "stale_px", "text": f"最後股價 {pd.Timestamp(t).date()}"})
        r = close[ys].iloc[-13:].pct_change(fill_method=None).iloc[1:]
        spike = ((r > 3) | (r < -0.8)).any()
        for y in spike.index[spike]:
            out.append({"id": rev_map[y], "kind": "px_spike", "text": f"近 12 個月單月最大漲跌 {r[y].abs().max():+.0%}"})
    for o in out:
        o["market"] = mkt
        o["label"] = LABEL[o["kind"]]
        o["severe"] = o["kind"] in SEVERE
    return out


def overall(panels: dict, infos: dict, monthly: pd.DataFrame | None, counts: dict, us_fund_last=None, today=None) -> list[dict]:
    """整體檢查：回傳 [{key, ok, text}]。"""
    today = pd.Timestamp(today or pd.Timestamp.today()).normalize()
    out = []
    for mkt in ("tw", "us"):
        c = counts.get(mkt) or {}
        if c.get("universe"):
            share = c.get("with_data", 0) / c["universe"]
            out.append({"key": f"coverage_{mkt}", "ok": share >= (0.85 if mkt == "tw" else 0.5),
                        "text": f"{'台股' if mkt == 'tw' else '美股'}有完整資料 {c.get('with_data', 0):,} / {c['universe']:,} 家（{share:.0%}）"})
    p = panels.get("us") if panels else None
    if p is not None and len(p):
        lastq = p.sort_values("period_end").groupby("id").tail(1)
        miss = int(lastq["adj_shares"].isna().sum()) if "adj_shares" in lastq else 0
        out.append({"key": "us_shares", "ok": miss / max(len(lastq), 1) < 0.1,
                    "text": f"美股最新一季沒有股數（算不出市值、無法篩選）：{miss:,} 家（{miss / max(len(lastq), 1):.0%}）"})
    if us_fund_last is not None:
        age = (today - pd.Timestamp(us_fund_last)).days
        out.append({"key": "us_fund_age", "ok": age <= 10, "text": f"美股財報最後一筆申報日 {pd.Timestamp(us_fund_last).date()}（{age} 天前）"})
    if monthly is not None and len(monthly):
        m = monthly
        first = m.groupby("ticker")["month"].min()
        lastm = m["month"].max()
        short = int((first > lastm - pd.DateOffset(months=12)).sum())
        out.append({"key": "short_history", "ok": short < 150, "text": f"股價歷史不到 12 個月的代號：{short:,} 個（新上市或待補抓）"})
        stale = m.groupby("ticker")["month"].max()
        n_stale = int((stale < lastm - pd.DateOffset(months=1)).sum())
        out.append({"key": "stale_prices", "ok": n_stale < 300, "text": f"超過一個月沒有新股價的代號：{n_stale:,} 個（多半已下市）"})
    return out


def report(issues: list[dict], checks: list[dict], today=None) -> dict:
    by = {}
    for o in issues:
        by.setdefault(o["kind"], 0)
        by[o["kind"]] += 1
    return {"as_of": str(pd.Timestamp(today or pd.Timestamp.today()).date()), "checks": checks,
            "counts": {LABEL[k]: v for k, v in sorted(by.items(), key=lambda x: -x[1])},
            "issues": sorted(issues, key=lambda o: (not o["severe"], o["market"], o["id"]))[:400]}


def email_lines(rep: dict, prev: dict | None) -> list[str]:
    """有整體檢查沒過，或嚴重個股問題比昨天多時才寫進日報。"""
    bad = [c for c in rep["checks"] if not c["ok"]]
    sev = {(o["market"], o["id"], o["kind"]) for o in rep["issues"] if o["severe"]}
    old = {(o["market"], o["id"], o["kind"]) for o in (prev or {}).get("issues", []) if o.get("severe")}
    new = sev - old if prev else set()
    if not bad and not new:
        return []
    out = ["🔍 資料品質提醒（詳見網站「條件說明」頁最上面）："]
    out += [f"  • {c['text']}" for c in bad]
    if new:
        out.append(f"  • 新出現 {len(new)} 檔數字可疑的股票：" + "、".join(f"{i}（{next(o['label'] for o in rep['issues'] if o['id'] == i and o['kind'] == k)}）" for _, i, k in sorted(new)[:8]))
    return out


def flags_by_stock(issues: list[dict]) -> dict:
    out = {}
    for o in issues:
        out.setdefault(f"{o['market']}:{o['id']}", []).append(o["label"])
    return out


__all__ = ["stock_issues", "overall", "report", "email_lines", "flags_by_stock", "LABEL", "np"]
