"""events 分支同步：兩邊（Mac、GitHub）同時推送時合併「已通知」紀錄，不會互相蓋掉。

用暫存的本機 bare repo 當遠端，不連 GitHub。執行：python -m tests.test_events_sync
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import pandas as pd  # noqa: E402

import events_sync as E  # noqa: E402


def _remote():
    d = Path(tempfile.mkdtemp()) / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", str(d)], check=True)
    return str(d)


def test_merge_state_rules():
    ours = {"titles": ["a", "b"], "keys": ["k1"], "digest_day": "2026-11-07", "pending": [{"t": 1}],
            "macro": {"last": {"tw_pmi": "2026-10-01"}, "pmi": {"tw": {"2026-10-01": 62.5}}, "baseline_done": True}}
    theirs = {"titles": ["c"], "keys": ["k2"], "digest_day": "2026-11-08", "pending": [],
              "macro": {"last": {"tw_pmi": "2026-09-01", "us_cli": "2026-09-01"}, "pmi": {"cn": {"2026-10-01": 49.3}}}}
    m = E.merge_state(ours, theirs)
    assert set(m["titles"]) == {"a", "b", "c"} and set(m["keys"]) == {"k1", "k2"}
    assert m["digest_day"] == "2026-11-08" and m["pending"] == []          # 對方比較晚寄過彙整 → 用對方的待寄
    assert m["macro"]["last"] == {"tw_pmi": "2026-10-01", "us_cli": "2026-09-01"}
    assert set(m["macro"]["pmi"]) == {"tw", "cn"} and m["macro"]["baseline_done"]


def test_pull_push_and_race():
    remote = _remote()
    base = Path(tempfile.mkdtemp())
    a, b = base / "a", base / "b"
    assert E.pull(a, remote) == ""                                           # 遠端還沒有分支
    (a / "state.json").write_text(json.dumps({"titles": ["t0"], "keys": []}))
    pd.DataFrame([{"time": "2026-11-07 10:00", "topic": "x", "title": "t0"}]).to_csv(a / "events.csv", index=False)
    assert E.push(a, remote)
    # 另一邊（GitHub）下載、加一筆、先推
    E.pull(b, remote)
    sb = json.loads((b / "state.json").read_text())
    sb["titles"].append("from_b")
    (b / "state.json").write_text(json.dumps(sb))
    pd.DataFrame([{"time": "2026-11-07 10:00", "topic": "x", "title": "t0"},
                  {"time": "2026-11-07 11:00", "topic": "y", "title": "from_b"}]).to_csv(b / "events.csv", index=False)
    assert E.push(b, remote)
    # 這邊（Mac）拿的是舊版本，也加一筆再推 → 被拒絕 → 合併後再推
    sa = json.loads((a / "state.json").read_text())
    sa["titles"].append("from_a")
    (a / "state.json").write_text(json.dumps(sa))
    pd.DataFrame([{"time": "2026-11-07 10:00", "topic": "x", "title": "t0"},
                  {"time": "2026-11-07 11:05", "topic": "z", "title": "from_a"}]).to_csv(a / "events.csv", index=False)
    assert E.push(a, remote)
    c = base / "c"
    E.pull(c, remote)
    st = json.loads((c / "state.json").read_text())
    assert set(st["titles"]) == {"t0", "from_a", "from_b"}
    ev = pd.read_csv(c / "events.csv")
    assert sorted(ev["title"]) == ["from_a", "from_b", "t0"]
    log = subprocess.run(["git", "--git-dir", remote, "rev-list", "--count", "events"], capture_output=True, text=True).stdout.strip()
    assert log == "1"                                                        # 永遠只有一個 commit
    assert not (c / ".c.base").exists() and not any(p.name.endswith(".base") for p in c.iterdir())


if __name__ == "__main__":
    ok = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            ok += 1
            print("✔", name)
    print(f"全部通過：{ok} 項")
