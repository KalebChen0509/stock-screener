"""盤中提醒搬到 Mac：開盤時段判斷、跟 GitHub 共用「今天已提醒」、兩邊同時推送時合併。

不連網、不寄信。執行：python -m tests.test_mac_alerts
"""
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import pandas as pd  # noqa: E402

import alerts as AL  # noqa: E402
import events_sync as E  # noqa: E402
import mac_events as F  # noqa: E402

T = lambda s: pd.Timestamp(s, tz="Asia/Taipei")  # noqa: E731


def test_alerts_due():
    assert F.alerts_due(T("2026-11-09 10:00"), None)                         # 台股盤中
    assert not F.alerts_due(T("2026-11-09 10:03"), "2026-11-09 10:00:00")    # 不到 5 分鐘
    assert F.alerts_due(T("2026-11-09 10:05"), "2026-11-09 10:00:00")
    assert not F.alerts_due(T("2026-11-09 15:00"), None)                     # 台股收盤、美股還沒開
    assert F.alerts_due(T("2026-11-09 23:00"), None)                         # 美股盤中（美東 10:00）
    assert not F.alerts_due(T("2026-11-08 10:00"), None)                     # 週日


def test_merge_alerts():
    assert E.merge_alerts({"2026-11-09": ["a"]}, {"2026-11-09": ["b"]}) == {"2026-11-09": ["a", "b"]}
    assert E.merge_alerts({"2026-11-10": ["c"]}, {"2026-11-09": ["a"]}) == {"2026-11-10": ["c"]}   # 只留最新一天
    assert E.merge_alerts({}, {}) == {}


def _bars(prices):
    idx = pd.bdate_range("2026-10-01", periods=len(prices))
    return pd.DataFrame({"open": prices, "high": prices, "low": prices, "close": prices}, index=idx)


def test_run_shared_state_no_duplicate():
    st = Path(tempfile.mkdtemp()) / "alerts_state.json"
    cfg = {"enabled": True, "rules": {"stop": False, "levels": True, "fair_value": False, "technical": False},
           "levels": {"tw": {"2330": {"below": 2200}}}}
    orig_read, orig_watch = AL.read_config, AL.load_watchlist
    AL.read_config = lambda name, repo: cfg if name == "alerts.yaml" else {}
    AL.load_watchlist = lambda: {}
    sent = []
    try:
        kw = dict(site="", repo="", now=T("2026-11-09 10:00"), download=lambda tick: {t: _bars([2300, 2150]) for t in tick},
                  send=lambda s, b: sent.append((s, b)) or {"email": True})
        r1 = AL.run(st, **kw)
        assert r1["new"] == 1 and len(sent) == 1 and "2,200" in sent[0][1]
        r2 = AL.run(st, **kw)                                                   # 同一天再檢查：不重寄
        assert r2["new"] == 0 and len(sent) == 1
        # 另一邊（GitHub）也寄過另一則 → 合併後兩邊都不重寄
        merged = E.merge_alerts(json.loads(st.read_text()), {"2026-11-09": ["tw:2330:above:9999"]})
        assert set(merged["2026-11-09"]) == {"tw:2330:below:2200", "tw:2330:above:9999"}
        closed = AL.run(st, **{**kw, "now": T("2026-11-09 15:00")})            # 收盤後不檢查
        assert closed == {"skipped": "closed"}
    finally:
        AL.read_config, AL.load_watchlist = orig_read, orig_watch


def test_events_sync_merges_alerts_state():
    import subprocess
    remote = Path(tempfile.mkdtemp()) / "r.git"
    subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True)
    base = Path(tempfile.mkdtemp())
    a, b = base / "a", base / "b"
    E.pull(a, str(remote))
    (a / "state.json").write_text("{}")
    (a / "alerts_state.json").write_text(json.dumps({"2026-11-09": ["x"]}))
    assert E.push(a, str(remote))
    E.pull(b, str(remote))
    (b / "alerts_state.json").write_text(json.dumps({"2026-11-09": ["x", "from_github"]}))
    assert E.push(b, str(remote))
    (a / "alerts_state.json").write_text(json.dumps({"2026-11-09": ["x", "from_mac"]}))
    assert E.push(a, str(remote))                                               # 被搶先 → 合併再推
    c = base / "c"
    E.pull(c, str(remote))
    assert json.loads((c / "alerts_state.json").read_text()) == {"2026-11-09": ["from_github", "from_mac", "x"]}


def test_overlay():
    b = _bars([100, 101, 102])                                          # 最後一天 2026-10-03
    q = {"date": str(b.index[-1].date()), "open": 1, "high": 2, "low": 0.5, "close": 1.5}
    r = AL.overlay(b, q)
    assert len(r) == 3 and r["close"].iloc[-1] == 1.5 and b["close"].iloc[-1] == 102   # 同一天 → 取代（不改原本的）
    r = AL.overlay(b, {**q, "date": "2026-10-06"})
    assert len(r) == 4 and r["close"].iloc[-1] == 1.5 and r["close"].iloc[-2] == 102    # Yahoo 還沒有今天 → 加一列
    assert AL.overlay(b, {**q, "date": "2026-10-01"}) is None                           # 快照比較舊 → 不用
    assert AL.overlay(b, None) is None and AL.overlay(None, q) is None


def test_run_live_source_and_fallback():
    st = Path(tempfile.mkdtemp()) / "alerts_state.json"
    cfg = {"enabled": True, "rules": {"stop": False, "levels": True, "fair_value": False, "technical": False},
           "levels": {"tw": {"2330": {"below": 2200}, "2317": {"below": 300}}}}
    orig_read, orig_watch = AL.read_config, AL.load_watchlist
    AL.read_config = lambda name, repo: cfg if name == "alerts.yaml" else {}
    AL.load_watchlist = lambda: {}
    sent, asked = [], []
    try:
        dl = lambda tick: {t: _bars([2300, 2300] if t.startswith("2330") else [290, 290]) for t in tick}  # noqa: E731
        last = str(_bars([1, 1]).index[-1].date())

        def live(codes):
            asked.append(sorted(codes))
            return {"2330": {"date": last, "open": 2300, "high": 2300, "low": 2100, "close": 2150}}   # 2317 拿不到
        r = AL.run(st, site="", repo="", now=T("2026-11-09 10:00"), download=dl, live=live,
                   send=lambda s, b: sent.append(b) or {"email": True})
        assert asked == [["2317", "2330"]] and r["new"] == 2 and r["live"] == 1
        body = sent[0]
        assert "2330" in body and "2,150" in body and "［永豐即時］" in body     # Yahoo 是 2300、沒跌破；永豐 2150 才觸發
        assert "2317" in body and "［Yahoo 延遲］" in body                       # 永豐沒有 → 退回 Yahoo
        # 永豐整個失敗（回傳空的）→ 全部用 Yahoo，照樣檢查
        st2 = Path(tempfile.mkdtemp()) / "s.json"
        sent.clear()
        r = AL.run(st2, site="", repo="", now=T("2026-11-09 10:00"), download=dl, live=lambda c: {},
                   send=lambda s, b: sent.append(b) or {"email": True})
        assert r["new"] == 1 and r["live"] == 0 and "2317" in sent[0] and "2330" not in sent[0]
    finally:
        AL.read_config, AL.load_watchlist = orig_read, orig_watch


def test_sinopac_quotes_never_leaks():
    """子程序失敗時：不轉印任何輸出（可能含身分證字號），只回傳空的。"""
    import subprocess
    orig_run, orig_py = subprocess.run, AL.SINOPAC_PY
    logs = []
    fake1, fake2 = "A" + "1" + "23456789", "B" + "2" + "23456789"     # 假的身分證格式（拆開寫，免得被推送前的掃描擋下）
    orig_warn = AL.log.warning
    try:
        AL.SINOPAC_PY = Path(sys.executable)
        AL.log.warning = lambda *a: logs.append(a[0] % a[1:])
        subprocess.run = lambda *a, **k: subprocess.CompletedProcess(a, 1, stdout=fake1 + " 連線中\n",
                                                                      stderr="Traceback… person_id=" + fake1)
        assert AL.sinopac_quotes(["2330"]) == {}
        subprocess.run = lambda *a, **k: subprocess.CompletedProcess(a, 1, stdout='{"ok": false, "error": "%s"}' % fake2, stderr="")
        assert AL.sinopac_quotes(["2330"]) == {}
        assert logs and not any(fake1[1:] in x or fake2[1:] in x for x in logs)
        subprocess.run = lambda *a, **k: subprocess.CompletedProcess(
            a, 0, stdout='{"ok": true, "quotes": {"2330": {"date": "2026-11-09", "open": 1, "high": 1, "low": 1, "close": 1}}}', stderr="")
        assert AL.sinopac_quotes(["2330"])["2330"]["close"] == 1
        assert AL.sinopac_quotes([]) == {}
    finally:
        subprocess.run, AL.SINOPAC_PY, AL.log.warning = orig_run, orig_py, orig_warn


if __name__ == "__main__":
    ok = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            ok += 1
            print("✔", name)
    print(f"全部通過：{ok} 項")
