"""市場重大事件偵測：新聞分級、關鍵字過濾、壓力數字（一個波段只通知一次）、合併去重、重大立刻寄／注意每日彙整。

執行：python -m tests.test_market_events（不需網路、不需 pytest）
"""
import json
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("SCREENER_CACHE", tempfile.mkdtemp())

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from screener import market_events as M  # noqa: E402


def test_classify_and_relevance():
    assert M.classify("台股下周「安」了！國安基金緊急召開臨時會 拍板進場護盤", "zh", True) == "重大"
    assert M.classify("台股活水來了？國安基金宣布進場", "zh", True) == "注意"            # 有問號＝推測
    assert M.classify("國安基金可能進場護盤", "zh", True) == "注意"
    assert M.classify("台股重挫 宣布", "zh", False) == "注意"                            # 這類主題最多只到注意
    assert M.classify("South Korea stocks plunge 8%, circuit breaker triggered", "en", True) == "重大"
    assert M.classify("How Stocks Nearly Triggered a Market-Wide Trading Freeze", "en", True) == "注意"
    assert M.relevant("tw_halt", "證交所宣布暫停交易") and not M.relevant("tw_halt", "櫃買業績發表 21公司上秀")
    assert M.relevant("us_fed", "Fed cuts rates by half point")


def _px(tw_last_drop=-0.06, dd_level=0.78):
    idx = pd.bdate_range("2025-10-01", "2026-11-06")
    tw = pd.Series(np.linspace(30000, 50000, len(idx)), index=idx)
    tw.iloc[-25:] = np.linspace(50000, 50000 * dd_level / (1 + tw_last_drop), 25)
    tw.iloc[-1] = tw.iloc[-2] * (1 + tw_last_drop)
    return pd.DataFrame({"^TWII": tw, "^GSPC": pd.Series(5000.0, index=idx), "^VIX": pd.Series(20.0, index=idx),
                         "TWD=X": pd.Series(32.0, index=idx)})


def test_stress_events_once_per_episode():
    now = pd.Timestamp("2026-11-06 15:00", tz="Asia/Taipei")
    st = {}
    flows = {"date": "2026-11-06", "foreign_net": -2100.0, "margin": 5300.0, "margin_chg": -450.0}
    ev, read = M.stress_events(now, _px(), flows, st)
    labels = {e["label"]: e["level"] for e in ev}
    assert labels["台股加權指數單日重挫"] == "重大"                                 # −6%
    assert labels["台股加權指數距一年高點 -20%"] == "重大"
    assert "台股加權指數距一年高點 -10%" not in labels                               # 同時跨過只發一則
    assert labels["外資單日大賣"] == "重大" and labels["融資大減（斷頭潮）"] == "重大"
    assert read["^TWII"]["from_1y_high"] < -0.2
    ev2, _ = M.stress_events(now + pd.Timedelta(hours=1), _px(tw_last_drop=0.0), {}, st)
    assert not any("距一年高點" in e["label"] for e in ev2)                          # 同一個波段不再通知


def test_merge_dedupe_and_major_suppresses_notice():
    st = {}
    base = {"kind": "news", "topic": "tw_nsf", "label": "國安基金", "category": "台灣政策", "source": "A", "url": ""}
    evs = [dict(base, level="重大", time="2026-11-06 18:04", title="國安基金拍板進場護盤"),
           dict(base, level="重大", time="2026-11-06 18:05", title="國安基金宣布啟動護盤", source="B"),
           dict(base, level="注意", time="2026-11-06 18:30", title="國安基金進場了嗎？")]
    fresh, hist = M.merge(evs, st)
    assert len(fresh) == 1 and fresh[0]["level"] == "重大" and len(fresh[0]["more"]) == 1 and len(hist) == 3
    fresh2, hist2 = M.merge(evs, st)                                                # 同樣的標題不再出現
    assert fresh2 == [] and hist2 == []


def test_run_first_baseline_then_major_now_digest_morning():
    store = Path(tempfile.mkdtemp())
    sent = []
    news = {"國安基金": [{"date": "2026-11-06 10:04", "title": "國安基金緊急召開臨時會 拍板進場護盤", "source": "鉅亨網", "url": "u1"}]}

    def fetch(q, lang="zh-TW", since=None, limit=8):
        return [x for x in news.get(q, []) if since is None or pd.Timestamp(x["date"]) >= since]
    calm = _px(tw_last_drop=0.0, dd_level=1.0)
    kw = dict(fetch_news=fetch, px=calm, flows={}, send=lambda s, t: sent.append((s, t)) or True)
    r = M.run(store, now="2026-11-06 18:30", **kw)
    assert r["first_run"] and sent == []                                            # 第一次只建立基準
    news["國安基金"].append({"date": "2026-11-06 12:00", "title": "國安基金宣布第十次進場 下周一開始護盤", "source": "經濟日報", "url": "u2"})  # 同一件事：不重寄
    news["限空令 OR 平盤下禁止放空 OR 平盤以下不得放空"] = [{"date": "2026-11-06 12:05", "title": "金管會宣布限空令 11月9日上路", "source": "工商", "url": "u4"}]
    news["台股 重挫 OR 台股 崩跌 OR 台股 最大跌點"] = [{"date": "2026-11-06 12:10", "title": "台股重挫1873點 失守4萬", "source": "C", "url": "u3"}]
    r = M.run(store, now="2026-11-06 20:30", **kw)                                   # 晚上：重大立刻寄，注意先累積
    assert r["majors"] == 1 and len(sent) == 1 and sent[0][0].startswith("【重大】限空令"), (r, sent)
    r = M.run(store, now="2026-11-07 07:10", **kw)                                   # 早上 7 點後：注意彙整一封
    assert len(sent) == 2 and sent[1][0].startswith("【注意】") and "台股重挫" in sent[1][1]
    r = M.run(store, now="2026-11-07 09:10", **kw)                                   # 同一天不再寄彙整
    assert len(sent) == 2
    site = json.loads((store / "market_events.json").read_text())
    assert site["events"] and site["events"][0]["time"] >= site["events"][-1]["time"]
    assert len(pd.read_csv(store / "events.csv")) == 4


if __name__ == "__main__":
    ok = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            ok += 1
            print("✔", name)
    print(f"全部通過：{ok} 項")
