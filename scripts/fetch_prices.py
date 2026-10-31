"""更新月價格快取。--full 重新下載全部歷史；預設只補最近幾個月（並把舊的還原價對齊新基準）。"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd  # noqa: E402

from screener import prices  # noqa: E402
from screener.common import CACHE_DIR, load_etf_universe, load_strategy, log  # noqa: E402

EXTRA = ["TWD=X", "^TWII", "^GSPC", "0050.TW", "SPY", "VT"]


def all_tickers() -> list[str]:
    tk = set(EXTRA)
    p = CACHE_DIR / "tw_universe.csv"
    if p.exists():
        tk |= set(pd.read_csv(p, dtype=str)["yahoo"])
    p = CACHE_DIR / "us_universe.csv"
    f = CACHE_DIR / "us_fundamentals.csv.gz"
    if p.exists():
        u = pd.read_csv(p, dtype=str)
        if f.exists():
            have = set(pd.read_csv(f, usecols=["id"], dtype=str)["id"])
            u = u[u["ticker"].isin(have)]
        tk |= set(u["yahoo"])
    for e in load_etf_universe():
        tk.add(e["ticker"])
        if e.get("tracking_index"):
            tk.add(e["tracking_index"])
    cfg = load_strategy()
    tk |= set(cfg["top_down"]["regime_indices"].values())
    tk |= set(cfg["backtest"].get("benchmarks", []))
    tk |= set((cfg.get("macro") or {}).get("tickers", []))
    # 固定部位（全球大盤、債券）：本設定與各回測方案用到的代號
    sleeves = list(cfg["portfolio"].get("fixed_sleeves", []) or [])
    for v in cfg["backtest"].get("variants", []) or []:
        sleeves += ((v.get("patch") or {}).get("portfolio") or {}).get("fixed_sleeves", []) or []
    for fs in sleeves:
        tk |= set((fs.get("holdings") or {}).keys())
    return sorted(t for t in tk if isinstance(t, str) and t)


ALT_MISS = CACHE_DIR / "prices_alt.miss"
NEW_MISS = CACHE_DIR / "prices_new.miss"     # 新加入股票清單、但 Yahoo 抓不到的代號（下市等），不每天重試
GAP_DONE = CACHE_DIR / "prices_gap.done"     # 股價歷史太短、已補抓過完整歷史的代號（真的是新上市就不再重抓）


def short_history(old: pd.DataFrame, tickers, months: int = 12, limit: int = 600) -> list[str]:
    """股價歷史不到 months 個月的代號（例如某次下載失敗，只留下之後每天補的最近幾個月）。
    每個代號只補抓一次完整歷史；抓完仍然很短（新上市）就記在 prices_gap.done 不再重抓。"""
    if old.empty:
        return []
    first = old.groupby("ticker")["month"].min()
    cutoff = old["month"].max() - pd.DateOffset(months=months)
    done = set(GAP_DONE.read_text().split()) if GAP_DONE.exists() else set()
    return sorted(t for t in tickers if t in first.index and first[t] > cutoff and t not in done)[:limit]


def tw_alt_tickers(have: set) -> tuple[list[str], list[str]]:
    """台股改上市／上櫃後，FinMind 清單的 .TW／.TWO 可能是舊的：主代號沒有股價的公司，試另一個字尾。
    回傳 (已找到要持續更新的, 還沒試過的)。試過找不到的記在 prices_alt.miss，不重試。"""
    p = CACHE_DIR / "tw_universe.csv"
    if not p.exists():
        return [], []
    u = pd.read_csv(p, dtype=str)
    missed = set(ALT_MISS.read_text().split()) if ALT_MISS.exists() else set()
    found, todo = [], []
    for sid, y in zip(u["stock_id"], u["yahoo"]):
        if not isinstance(y, str) or y in have:
            continue
        a = sid + (".TW" if y.endswith(".TWO") else ".TWO")
        if a in have:
            found.append(a)
        elif a not in missed:
            todo.append(a)
    return found, todo


def _record_alt_miss(tried: list[str], got: set, path=ALT_MISS) -> None:
    missed = set(path.read_text().split()) if path.exists() else set()
    missed |= {t for t in tried if t not in got}
    path.write_text("\n".join(sorted(missed)))


def key_tickers() -> list[str]:
    cfg = load_strategy()
    tk = set(cfg["backtest"].get("benchmarks", [])) | {e["ticker"] for e in load_etf_universe()}
    tk |= set((cfg.get("macro") or {}).get("tickers", []))
    sleeves = list(cfg["portfolio"].get("fixed_sleeves", []) or [])
    for v in cfg["backtest"].get("variants", []) or []:
        sleeves += ((v.get("patch") or {}).get("portfolio") or {}).get("fixed_sleeves", []) or []
    for fs in sleeves:
        tk |= set((fs.get("holdings") or {}).keys())
    return sorted(tk)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--full", action="store_true")
    args = ap.parse_args()
    tickers = all_tickers()
    alt_found, alt_todo = tw_alt_tickers(prices.monthly_tickers())
    log.info("price tickers: %d (+ TW alternate suffix: %d known, %d to try)", len(tickers), len(alt_found), len(alt_todo))
    if args.full or not prices.MONTHLY_PATH.exists():
        res = prices.fetch_monthly(sorted(set(tickers) | set(alt_found) | set(alt_todo)), start="2011-01-01", save=False)
        _record_alt_miss(alt_todo, set(res["ticker"]))
        if prices.MONTHLY_PATH.exists():   # 這次下載失敗的代號保留舊資料（不要整批消失）
            old = prices.load_monthly()
            res = pd.concat([res, old[~old["ticker"].isin(set(res["ticker"]))]], ignore_index=True)
        res.sort_values(["ticker", "month"]).to_csv(prices.MONTHLY_PATH, index=False, compression="gzip")
        log.info("monthly prices (full): %d rows, %d tickers", len(res), res["ticker"].nunique())
        return
    tickers = sorted(set(tickers) | set(alt_found))
    old = prices.load_monthly()
    start = (pd.Timestamp.today() - pd.offsets.MonthBegin(4)).normalize()
    new = prices.fetch_monthly(tickers, start=str(start.date()), save=False)
    overlap = (start + pd.offsets.MonthEnd(0)).normalize()
    merged = []
    old_groups = dict(tuple(old.groupby("ticker")))
    for t, g_new in new.groupby("ticker"):
        g_old = old_groups.get(t, old.iloc[0:0])
        if g_old.empty:
            merged.append(g_new)
            continue
        o = g_old[g_old["month"] == overlap]
        n = g_new[g_new["month"] == overlap]
        g_old = g_old[g_old["month"] < overlap + pd.offsets.MonthEnd(1)]
        if len(o) and len(n) and o["adj_close"].iloc[0] > 0:
            scale = n["adj_close"].iloc[0] / o["adj_close"].iloc[0]
            g_old = g_old.assign(adj_close=g_old["adj_close"] * scale)
        g_old = g_old[g_old["month"] < overlap]
        merged.append(pd.concat([g_old, g_new]))
    # 新加入的 ETF／基準／固定部位代號：補抓完整歷史（個股不在此列，避免每天重抓下市股票）
    # 新加入股票清單的公司（例如補進來的「其他業」）：抓完整歷史一次；抓不到的記下來不再重試
    new_miss = set(NEW_MISS.read_text().split()) if NEW_MISS.exists() else set()
    have_recent = set(new["ticker"]) if len(new) else set()
    # 有最近股價、卻被記成「抓不到」的（當初是暫時下載失敗）：移出清單，重新抓
    if new_miss & have_recent:
        new_miss -= have_recent
        NEW_MISS.write_text("\n".join(sorted(new_miss)))
    new_stocks = sorted(set(tickers) - set(old["ticker"]) - set(key_tickers()) - new_miss)
    gaps = [t for t in short_history(old, tickers) if t in have_recent]   # 還在交易的才補（下市的不必每天重試）
    if gaps:
        log.info("short price history (%d tickers), refetching full history once: %s", len(gaps), gaps[:20])
    missing = sorted((set(key_tickers()) - set(old["ticker"])) | set(alt_todo) | set(new_stocks) | set(gaps))
    if missing:
        log.info("new tickers, fetching full history: %s", missing[:20])
        full_new = prices.fetch_monthly(missing, start="2011-01-01", save=False)
        got_new = set(full_new["ticker"]) if len(full_new) else set()
        keep_old, got = set(), set()
        _record_alt_miss(alt_todo, got_new)
        # 只有連最近的股價也抓不到才記成「抓不到」；抓完整歷史時暫時失敗的，下次再試
        _record_alt_miss([t for t in new_stocks if t not in have_recent], got_new, NEW_MISS)
        if len(full_new):
            first_old = old.groupby("ticker")["month"].min()
            first_new = full_new.groupby("ticker")["month"].min()
            # 補抓的歷史沒有比原本長（新上市）就保留原本的資料
            keep_old = {t for t in gaps if t in first_new.index and first_new[t] >= first_old.get(t, first_new[t])}
            full_new = full_new[~full_new["ticker"].isin(keep_old)]
            got = set(full_new["ticker"])
            merged = [g for g in merged if not (len(g) and g["ticker"].iloc[0] in got)]
            merged.append(full_new)
        if gaps:
            done = set(GAP_DONE.read_text().split()) if GAP_DONE.exists() else set()
            GAP_DONE.write_text("\n".join(sorted(done | (set(gaps) & got_new))))
            log.info("price history: %d of %d refetched, %d now longer", len(set(gaps) & got_new), len(gaps),
                     len((set(gaps) & got_new) - keep_old))
    else:
        got = set()
    # 舊資料：這次有更新（最近幾個月或完整歷史）的代號已經在 merged 裡，其餘照舊保留
    seen = set(new["ticker"]) | got
    merged.append(old[~old["ticker"].isin(seen)])
    res = pd.concat(merged, ignore_index=True).sort_values(["ticker", "month"])
    res.to_csv(prices.MONTHLY_PATH, index=False, compression="gzip")
    log.info("monthly prices updated: %d rows, %d tickers", len(res), res["ticker"].nunique())


if __name__ == "__main__":
    main()
