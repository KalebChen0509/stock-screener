"""Mac 常駐：指標公布時段每 2 分鐘檢查一次「轉折訊號」，比 GitHub（每 30 分鐘、常延遲）快。

由 launchd 每 2 分鐘叫一次（安裝：bash scripts/install_macro_fast.sh）；不在公布時段就馬上結束、不連網。
公布時段（台北時間，週一～五）：
    08:55～10:35  台灣 PMI（中經院）、中國官方 PMI（月底 09:30）
    15:55～17:35  國發會景氣燈號（月底前後 16:00）、日本／歐元區 PMI
    20:25～23:05  美國 8:30／10:00 公布的數據（初領失業金、ISM…）
檢查的東西：各國 PMI 新聞（全部時段）、國發會資料集（下午時段）、FRED 重要數據（晚上時段，要有 FRED 金鑰）。
只寄「重大」轉折（跟 GitHub 用同一套規則）；GitHub 之後確認時也會再寄一次（標題沒有【Mac 快訊】）。

金鑰都在 macOS 鑰匙圈（由使用者自己輸入，這支程式只在執行時讀取、不會印出來）：
    stock-mail  smtp_user／smtp_pass／email_to（已經有了：對帳信用的）
    stock-line  token／user_id（選用；LINE Messaging API）
    stock-fred  api_key（選用；沒有就跳過美國數據，等 GitHub）
資料與紀錄：~/.stock-screener/macro_fast/（state.json、macro.json）、~/.stock-screener/logs/macro_fast.log
測試：.venv/bin/python scripts/macro_fast.py --force   （不管時段，立刻檢查一次）
"""
import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd  # noqa: E402

HOME = Path.home() / ".stock-screener"
STORE = HOME / "macro_fast"
WINDOWS = [((8, 55), (10, 35), ("news",)), ((15, 55), (17, 35), ("news", "ndc")), ((20, 25), (23, 5), ("news", "fred"))]
FAST_FRED = {"us_claims", "us_cfnai", "us_sahm", "us_nfci"}     # 晚上時段只抓這幾個「重大」等級的美國數據


def window(now: pd.Timestamp):
    """現在在哪個公布時段 → 要檢查的來源；不在時段回傳 None。"""
    if now.weekday() >= 5:
        return None
    hm = (now.hour, now.minute)
    for a, b, src in WINDOWS:
        if a <= hm < b:
            return src
    return None


def keychain(service: str, account: str) -> str:
    """讀鑰匙圈；沒有這個項目回傳空字串（不印內容）。"""
    r = subprocess.run(["security", "find-generic-password", "-s", service, "-a", account, "-w"], capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else ""


def load_secrets() -> str:
    """把鑰匙圈的寄信設定放進這個程式自己的環境變數（screener.notify 讀環境變數），回傳 FRED 金鑰（可能是空的）。"""
    for env, (svc, acct) in {"SMTP_USER": ("stock-mail", "smtp_user"), "SMTP_PASS": ("stock-mail", "smtp_pass"),
                             "EMAIL_TO": ("stock-mail", "email_to"), "LINE_CHANNEL_ACCESS_TOKEN": ("stock-line", "token"),
                             "LINE_USER_ID": ("stock-line", "user_id")}.items():
        v = keychain(svc, acct)
        if v:
            os.environ[env] = v
    return keychain("stock-fred", "api_key")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="不管時段，立刻檢查（新聞＋國發會＋FRED）")
    args = ap.parse_args(argv)
    now = pd.Timestamp.now(tz="Asia/Taipei")
    src = ("news", "ndc", "fred") if args.force else window(now)
    if not src:
        return 0
    from screener import macro_ind
    from screener.notify import notify
    fred_key = load_secrets()
    if not fred_key:
        src = tuple(s for s in src if s != "fred")
    if not src:
        return 0
    STORE.mkdir(parents=True, exist_ok=True)
    sp = STORE / "state.json"
    state = json.loads(sp.read_text()) if sp.exists() else {}
    events, _ = macro_ind.check(STORE, state, now, sources=src, fred_key=fred_key or None, only=FAST_FRED)
    majors = [e for e in events if e["kind"] == "macro" and e["level"] == "重大"]
    sent = None
    if majors:
        text = "\n\n".join(f"{e['title']}\n來源：{e['source']} {e.get('url', '')}" for e in majors)
        text += "\n\n（Mac 常駐程式在公布時段每 2 分鐘檢查；GitHub 之後確認時會再通知一次。這不是買賣建議。）"
        sent = notify("【Mac 快訊】" + "、".join(dict.fromkeys(e["label"] for e in majors))[:70], text, scope="events")
    sp.write_text(json.dumps(state, ensure_ascii=False))
    print(f"{now:%Y-%m-%d %H:%M} 來源 {','.join(src)}：轉折 {len(majors)} 則" + (f"，通知 {sent}" if sent else ""), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
