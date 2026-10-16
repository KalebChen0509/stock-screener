"""在你的電腦上執行一次：設定 GitHub Secrets、開啟 Pages、啟動第一次工作。
金鑰只從環境變數讀取，不會寫進任何檔案。"""
import base64
import json
import os
import sys
import time
import urllib.error
import urllib.request

TOKEN = os.environ["GH_PAT"]
REPO = os.environ["GH_REPO"]


def gh(path, method="GET", body=None):
    req = urllib.request.Request(
        "https://api.github.com" + path, method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Authorization": f"Bearer {TOKEN}", "Accept": "application/vnd.github+json",
                 "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "stock-screener-setup"})
    try:
        with urllib.request.urlopen(req) as r:
            t = r.read().decode()
            return r.status, (json.loads(t) if t else {})
    except urllib.error.HTTPError as e:
        t = e.read().decode()
        try:
            return e.code, json.loads(t)
        except Exception:
            return e.code, {"message": t}


def set_secret(name, value):
    from nacl import encoding, public
    _, key = gh(f"/repos/{REPO}/actions/secrets/public-key")
    pk = public.PublicKey(key["key"].encode(), encoding.Base64Encoder())
    enc = base64.b64encode(public.SealedBox(pk).encrypt(value.encode())).decode()
    s, r = gh(f"/repos/{REPO}/actions/secrets/{name}", "PUT", {"encrypted_value": enc, "key_id": key["key_id"]})
    print(f"  Secret {name}: {'OK' if s in (201, 204) else ('失敗 ' + str(s) + ' ' + str(r.get('message')))}")


def main():
    s, r = gh(f"/repos/{REPO}")
    if s != 200:
        print("無法讀取 repo：", s, r.get("message")); sys.exit(1)
    print("Repo:", r["full_name"])
    for name in ("FINMIND_TOKEN",):
        v = os.environ.get(name)
        if v:
            set_secret(name, v)
    s, r = gh(f"/repos/{REPO}/pages", "POST", {"build_type": "workflow"})
    if s in (201, 409):
        gh(f"/repos/{REPO}/pages", "PUT", {"build_type": "workflow"})
        print("  GitHub Pages: OK")
    else:
        print(f"  GitHub Pages 自動開啟失敗（{s} {r.get('message')}）→ 請到 repo 的 Settings → Pages，Source 選「GitHub Actions」")
    time.sleep(8)
    for wf, inputs in (("debug.yml", {"stock": "2330"}), ("backfill.yml", None), ("daily.yml", None)):
        body = {"ref": "main"}
        if inputs:
            body["inputs"] = inputs
        s, r = gh(f"/repos/{REPO}/actions/workflows/{wf}/dispatches", "POST", body)
        print(f"  啟動 {wf}: {'OK' if s == 204 else ('失敗 ' + str(s) + ' ' + str(r.get('message')))}")
        time.sleep(3)
    print(f"\n完成！網站（第一次更新跑完後出現）：https://{REPO.split('/')[0].lower()}.github.io/{REPO.split('/')[1]}/")


if __name__ == "__main__":
    main()
