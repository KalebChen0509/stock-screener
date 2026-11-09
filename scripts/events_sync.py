"""events 分支（市場事件、總經指標、盤中提醒的紀錄）的下載與上傳：Mac 常駐程式和 GitHub workflow 共用。

    python scripts/events_sync.py pull --dir evbr [--remote URL]
    python scripts/events_sync.py push --dir evbr [--remote URL]

events 分支永遠只有一個 commit（不會越來越大）。兩邊可能同時在跑：
上傳時用 --force-with-lease（只有在遠端還是我下載時那一版才覆蓋）；被別人搶先 → 下載對方的版本、
把「已經通知過」的紀錄合併進來再上傳，這樣同一件事不會兩邊各寄一次。
"""
from __future__ import annotations

import argparse
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
BRANCH = "events"
REF = "refs/evsync/base"
IDENT = {"GIT_AUTHOR_NAME": "events-bot", "GIT_AUTHOR_EMAIL": "events-bot@users.noreply.github.com",
         "GIT_COMMITTER_NAME": "events-bot", "GIT_COMMITTER_EMAIL": "events-bot@users.noreply.github.com"}


def git(*args, env=None, check=True, input=None) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, env={**os.environ, **(env or {})},
                          check=check, input=input)


def _base_file(d: Path) -> Path:
    return d.parent / f".{d.name}.base"          # 放在資料夾外面，才不會被一起上傳


def fetch(remote: str) -> str:
    """下載遠端 events 分支 → 回傳 commit（分支不存在回傳 ""）。"""
    r = git("fetch", "-q", "--depth", "1", remote, f"+refs/heads/{BRANCH}:{REF}", check=False)
    if r.returncode != 0:
        msg = r.stderr.decode(errors="ignore")
        if "couldn't find remote ref" in msg or "not found" in msg.lower():
            return ""
        raise RuntimeError(f"git fetch events failed: {msg.strip()[:300]}")
    return git("rev-parse", REF).stdout.decode().strip()


def _safe_dir(d: Path) -> Path:
    d = d.resolve()
    if d in (Path("/"), Path.home(), ROOT) or len(d.parts) < 3:
        raise ValueError(f"不安全的資料夾：{d}")
    return d


def extract(sha: str, d: Path) -> None:
    d = _safe_dir(d)
    if d.exists():
        shutil.rmtree(d)
    d.mkdir(parents=True)
    if sha:
        data = git("archive", "--format=tar", sha).stdout
        with tarfile.open(fileobj=io.BytesIO(data)) as tf:
            tf.extractall(d, filter="data")


def pull(d: Path, remote: str = "origin") -> str:
    sha = fetch(remote)
    extract(sha, d)
    _base_file(Path(d)).write_text(sha)
    return sha


# ---------------------------------------------------------------- 合併
def _union(a: list, b: list, cap: int = 3000) -> list:
    seen, out = set(), []
    for x in list(b) + list(a):
        k = json.dumps(x, sort_keys=True, ensure_ascii=False) if isinstance(x, (dict, list)) else x
        if k not in seen:
            seen.add(k)
            out.append(x)
    return out[-cap:]


def merge_state(ours: dict, theirs: dict) -> dict:
    """以我的為主，把對方「已經通知過／已經看過」的紀錄合併進來。"""
    m = dict(ours)
    for k in ("titles", "keys"):
        m[k] = _union(ours.get(k, []), theirs.get(k, []))
    # 「注意」彙整：誰比較晚寄過彙整就用誰的待寄清單（避免把對方已經寄出的再寄一次）
    if (theirs.get("digest_day") or "") > (ours.get("digest_day") or ""):
        m["digest_day"], m["pending"] = theirs.get("digest_day"), theirs.get("pending", [])
    elif theirs.get("digest_day") == ours.get("digest_day"):
        m["pending"] = _union(ours.get("pending", []), theirs.get("pending", []), cap=200)
    mo, mt = ours.get("macro") or {}, theirs.get("macro") or {}
    if mt:
        mm = dict(mo)
        last = dict(mt.get("last", {}))
        for k, v in (mo.get("last") or {}).items():
            last[k] = max(v, last.get(k, ""))
        mm["last"] = last
        mm["baseline_done"] = bool(mo.get("baseline_done") or mt.get("baseline_done"))
        pmi = {a: dict(h) for a, h in (mt.get("pmi") or {}).items()}
        for a, h in (mo.get("pmi") or {}).items():
            pmi.setdefault(a, {}).update(h)
        mm["pmi"] = pmi
        mm["private"] = {**(mt.get("private") or {}), **(mo.get("private") or {})}   # 有版權序列的狀態
        m["macro"] = mm
    return m


def merge_alerts(ours: dict, theirs: dict) -> dict:
    """盤中提醒「今天已提醒」：{日期: [key…]}，只留最新的一天；同一天取聯集（兩邊寄過的都不再寄）。"""
    days = sorted(set(ours) | set(theirs))
    if not days:
        return {}
    d = days[-1]
    return {d: sorted(set(ours.get(d, [])) | set(theirs.get(d, [])))}


def merge_dir(d: Path, sha: str) -> None:
    """把遠端 sha 版本的 state.json、events.csv 合併進本機資料夾 d。"""
    tmp = Path(tempfile.mkdtemp(prefix="evsync_"))
    try:
        extract(sha, tmp)
        sp, tp = d / "state.json", tmp / "state.json"
        if tp.exists():
            ours = json.loads(sp.read_text()) if sp.exists() else {}
            sp.write_text(json.dumps(merge_state(ours, json.loads(tp.read_text())), ensure_ascii=False))
        ap, ta = d / "alerts_state.json", tmp / "alerts_state.json"
        if ta.exists():
            ours = json.loads(ap.read_text()) if ap.exists() else {}
            ap.write_text(json.dumps(merge_alerts(ours, json.loads(ta.read_text()))))
        ep, te = d / "events.csv", tmp / "events.csv"
        if te.exists():
            frames = [pd.read_csv(te)] + ([pd.read_csv(ep)] if ep.exists() else [])
            al = pd.concat(frames, ignore_index=True)
            al.drop_duplicates(subset=[c for c in ("time", "topic", "title") if c in al], keep="last").to_csv(ep, index=False)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------- 上傳
def commit_dir(d: Path, msg: str) -> str:
    idx = Path(tempfile.mkdtemp(prefix="evidx_")) / "index"
    env = {"GIT_INDEX_FILE": str(idx), "GIT_WORK_TREE": str(Path(d).resolve()), **IDENT}
    try:
        subprocess.run(["git", "--git-dir", str(ROOT / ".git"), "add", "-A", "-f", "."], cwd=d, check=True,
                       capture_output=True, env={**os.environ, **env})
        tree = subprocess.run(["git", "--git-dir", str(ROOT / ".git"), "write-tree"], cwd=d, check=True,
                              capture_output=True, env={**os.environ, **env}).stdout.decode().strip()
        return git("commit-tree", tree, "-m", msg, env=IDENT).stdout.decode().strip()
    finally:
        shutil.rmtree(idx.parent, ignore_errors=True)


def push(d: Path, remote: str = "origin", msg: str | None = None, tries: int = 3) -> bool:
    d = Path(d)
    # 保護：沒有成功下載過、或資料夾裡沒有 state.json → 不上傳（不然會把遠端的紀錄清空）
    if not _base_file(d).exists() or not (d / "state.json").exists():
        print("push events skipped: 沒有成功下載或沒有 state.json", file=sys.stderr)
        return False
    base = _base_file(d).read_text().strip()
    msg = msg or f"events {pd.Timestamp.now(tz='UTC'):%Y-%m-%dT%H:%MZ}"
    for _ in range(tries):
        c = commit_dir(d, msg)
        r = git("push", "-q", f"--force-with-lease=refs/heads/{BRANCH}:{base}", remote, f"{c}:refs/heads/{BRANCH}", check=False)
        if r.returncode == 0:
            _base_file(d).write_text(c)
            return True
        err = r.stderr.decode(errors="ignore")
        if "stale info" not in err and "rejected" not in err and "fetch first" not in err:
            print("push events failed:", err.strip()[:300], file=sys.stderr)
            return False
        new = fetch(remote)                         # 別人先推了：合併對方的紀錄後再推
        if new:
            merge_dir(d, new)
        base = new
    print("push events failed after retries", file=sys.stderr)
    return False


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["pull", "push"])
    ap.add_argument("--dir", default="evbr")
    ap.add_argument("--remote", default="origin")
    a = ap.parse_args(argv)
    if a.cmd == "pull":
        print("events:", pull(Path(a.dir), a.remote) or "（還沒有這個分支）")
        return 0
    return 0 if push(Path(a.dir), a.remote) else 1


if __name__ == "__main__":
    sys.exit(main())
