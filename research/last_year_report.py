"""last_year.py 的報告：一年報酬、跟大盤比、買賣明細（存 research/out/last_year/trades_*.csv），以及 11 月至今的估計。

    SCREENER_CACHE=<data 分支>/cache .venv/bin/python research/last_year_report.py
"""
import os
import pickle
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _paths import OUT, ROOT  # noqa: E402

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import yaml  # noqa: E402

from screener import paper, prices  # noqa: E402
from screener.common import CACHE_DIR  # noqa: E402

D = OUT / "last_year"
CAP = 1_000_000
P = pickle.load(open(D / "full.pkl", "rb"))
full, names = P["full"], P["names"]
try:
    ZH = yaml.safe_load(open(ROOT / "config" / "names_zh.yaml")) or {}
except Exception:  # noqa
    ZH = {}


def nm(w) -> str:
    tk = w["ticker"]
    if w.get("kind") != "stock":
        z = (ZH.get("etf") or {}).get(tk.replace(".TW", ""), "")
        return f"{tk}{'（' + str(z).split('｜')[0] + '）' if z else ''}"
    mkt, sid = w.get("mkt"), w.get("id")
    # names_zh：美股是「中文名｜說明」，台股只有說明（公司名用資料來源的中文簡稱）
    z = str((ZH.get(mkt) or {}).get(sid, "")).split("｜")[0] if mkt == "us" else ""
    n = z or str(names.get(mkt, {}).get(sid, ""))[:18]
    return f"{sid} {n}".strip()


def st(r: pd.Series) -> dict:
    nav = (1 + r).cumprod()
    return {"一年報酬": nav.iloc[-1] - 1, "期末金額": CAP * nav.iloc[-1], "最大跌幅": float(min((nav / nav.cummax() - 1).min(), nav.min() - 1, 0)),
            "最差單月": float(r.min()), "最好單月": float(r.max())}


pd.set_option("display.width", 250)
first = next(iter(full.values()))["monthly"]
print(f"期間：{first.index[0]:%Y-%m}～{first.index[-1]:%Y-%m}（{len(first)} 個月；第一次決策 2025-10-31，一次投入 {CAP:,} 元）\n")
rows = []
for name, o in full.items():
    m = o["monthly"]
    s = st(m["ret_net"])
    rows.append({"版本": name, **s, "未扣費": (1 + m["ret_gross"]).prod() - 1, "台股個股部位": (1 + m["sleeve_tw"]).prod() - 1,
                 "美股個股部位": (1 + m["sleeve_us"]).prod() - 1, "停損次數": len(o["stops"]), "交易成本合計": float(m["cost"].sum())})
for b, lab in (("bench_0050.TW", "〔大盤〕0050 含息"), ("bench_SPY", "〔大盤〕SPY 含息（換成台幣）"), ("bench_VT", "〔參考〕VT 全球（台幣）")):
    if b in first:
        rows.append({"版本": lab, **st(first[b])})
R = pd.DataFrame(rows)
f = R.copy()
for c in ("一年報酬", "最大跌幅", "最差單月", "最好單月", "未扣費", "台股個股部位", "美股個股部位", "交易成本合計"):
    f[c] = f[c].map(lambda v: "" if pd.isna(v) else f"{v:+.1%}")
f["期末金額"] = f["期末金額"].map(lambda v: f"{v:,.0f}")
f["停損次數"] = f["停損次數"].map(lambda v: "" if pd.isna(v) else f"{int(v)}")
print("==== 一年報酬（扣交易成本後；最大跌幅用月底淨值算）====")
print(f.to_string(index=False))
R.to_csv(D / "summary.csv", index=False)

print("\n==== 每月報酬（扣費後）====")
M = pd.DataFrame({k.split("（")[0][:12] + ("月底停損" if "月底檢查" in k else ""): o["monthly"]["ret_net"] for k, o in full.items()})
M["0050"], M["SPY台幣"] = first["bench_0050.TW"], first["bench_SPY"]
M.index = M.index.strftime("%Y-%m")
print(M.map(lambda v: f"{v:+.1%}").to_string())

# ---------------------------------------------------------------- 買賣明細
for name, o in full.items():
    H = o["holdings"]
    stops = pd.DataFrame(o["stops"]) if o["stops"] else pd.DataFrame(columns=["date", "market", "id"])
    sp = pd.DataFrame(o["spells"])
    trades, prev = [], {}
    nav = CAP
    mret = o["monthly"]["ret_net"]
    print(f"\n{'=' * 20} {name} {'=' * 20}")
    for i, h in enumerate(H):
        t = pd.Timestamp(h["month"])
        cur = {(w["kind"], w["ticker"]): w for w in h["weights"]}
        buys = [cur[k] for k in cur if k not in prev]
        sells = [prev[k] for k in prev if k not in cur]
        for w in buys:
            trades.append({"日期": t.strftime("%Y-%m-%d"), "動作": "買進", "標的": nm(w), "代號": w["ticker"], "類別": w["kind"], "目標比重": w["w"], "約略金額": nav * w["w"]})
        for w in sells:
            sid = w.get("id")
            stp = stops[(stops["id"] == sid) & (pd.to_datetime(stops["date"]) > (pd.Timestamp(H[i - 1]["month"]) if i else t)) & (pd.to_datetime(stops["date"]) <= t)] if sid and len(stops) else []
            why = f"停損（{stp.iloc[0]['date']}）" if len(stp) else "調整賣出"
            trades.append({"日期": (stp.iloc[0]["date"] if len(stp) else t.strftime("%Y-%m-%d")), "動作": why, "標的": nm(w), "代號": w["ticker"], "類別": w["kind"], "目標比重": 0.0, "約略金額": np.nan})
        if i == 0:
            stock = [w for w in h["weights"] if w["kind"] == "stock"]
            other = [w for w in h["weights"] if w["kind"] != "stock"]
            print(f"【{t:%Y-%m-%d} 第一次買進】現金 {1 - sum(w['w'] for w in h['weights']):.1%}")
            if other:
                print("  ETF：" + "、".join(f"{nm(w)} {w['w']:.1%}" for w in sorted(other, key=lambda x: -x["w"])))
            for mk, lab in (("tw", "台股"), ("us", "美股")):
                g = sorted([w for w in stock if w.get("mkt") == mk], key=lambda x: -x["w"])
                if g:
                    print(f"  {lab} {len(g)} 檔（合計 {sum(w['w'] for w in g):.1%}）：" + "、".join(f"{nm(w)} {w['w']:.1%}" for w in g))
        elif buys or sells:
            b = "、".join(nm(w) for w in buys if w["kind"] == "stock") or "—"
            s = "、".join(nm(w) + ("〔停損〕" if any(x["標的"] == nm(w) and x["動作"].startswith("停損") and x["日期"] > H[i - 1]["month"].strftime("%Y-%m-%d") for x in trades) else "")
                          for w in sells if w["kind"] == "stock") or "—"
            print(f"【{t:%Y-%m}月底】買：{b}｜賣：{s}")
        prev = cur
        if t in mret.index or (i + 1 < len(H) and pd.Timestamp(H[i + 1]["month"]) in mret.index):
            nxt = pd.Timestamp(H[i + 1]["month"]) if i + 1 < len(H) else None
            if nxt is not None and nxt in mret.index:
                nav *= 1 + mret.loc[nxt]
    T = pd.DataFrame(trades)
    T.to_csv(D / f"trades_{list(full).index(name) + 1}.csv", index=False)
    nb, ns, nst = int((T["動作"] == "買進").sum()), int((T["動作"] == "調整賣出").sum()), int(T["動作"].str.startswith("停損").sum())
    print(f"合計：買進 {nb} 筆、調整賣出 {ns} 筆、停損 {nst} 筆（含 ETF 進出）")
    if len(sp):
        sp = sp.dropna(subset=["ret"]).copy()
        sp["名稱"] = [nm({"kind": "stock", "mkt": m_, "id": i_, "ticker": ""}) for m_, i_ in zip(sp["market"], sp["id"])]
        top, bot = sp.sort_values("ret", ascending=False).head(5), sp.sort_values("ret").head(5)
        print("  賺最多：" + "、".join(f"{r_['名稱']} {r_['ret']:+.0%}（{r_['start']}～{r_['end'][:7]}，{r_['reason']}）" for _, r_ in top.iterrows()))
        print("  賠最多：" + "、".join(f"{r_['名稱']} {r_['ret']:+.0%}（{r_['start']}～{r_['end'][:7]}，{r_['reason']}）" for _, r_ in bot.iterrows()))
        print(f"  每一筆持股：{len(sp)} 筆，{(sp['ret'] > 0).mean():.0%} 賺錢、{(sp['ret'] > sp['bench']).mean():.0%} 贏同期大盤；平均 {sp['ret'].mean():+.1%}、中位數 {sp['ret'].median():+.1%}")

# ---------------------------------------------------------------- 11 月至今（估計）
try:
    c = pd.read_csv(CACHE_DIR / "paper_daily.csv.gz", parse_dates=["date"])
    daily = c.pivot_table(index="date", columns="ticker", values="adj_close", aggfunc="last").sort_index()
    print(f"\n==== 2026-11 月初至 {daily.index[-1]:%m/%d}（用 10/31 的持股估計，不含月中停損與成本）====")
    for name, o in full.items():
        h = o["holdings"][-1]
        mtd, day, cov = paper.mark_to_market(h["weights"], daily, h["month"])
        print(f"{name}：{'' if mtd is None else f'{mtd:+.1%}'}（到 {day}，有報價的比重 {cov:.0%}）")
    for tk in ("0050.TW", "SPY"):
        if tk in daily:
            s = daily[tk].dropna()
            print(f"{tk}：{s.iloc[-1] / s.loc[:'2026-10-31'].iloc[-1] - 1:+.1%}（原幣）")
except Exception as e:  # noqa
    print("11 月估計跳過：", str(e)[:150])
