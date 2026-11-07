"""Mac 常駐（主力）：市場重大事件＋總經指標轉折偵測。GitHub 的排程常常好幾個小時才跑一次，只當備援。

由 launchd 每 2 分鐘叫一次（安裝：bash scripts/install_mac_events.sh）：
    每 10 分鐘   完整檢查（15 個新聞主題、市場壓力數字、總經指標）
    公布時段     每 2 分鐘只檢查總經指標（台北時間，週一～五）
                 08:55～10:35  台灣 PMI（中經院）、中國官方 PMI
                 15:55～17:35  國發會景氣燈號（月底前後）、日本／歐元區 PMI
                 20:25～23:05  美國 8:30／10:00 公布的數據（初領失業金、ISM…）
    其他時間     不連網、馬上結束
每次都先下載 GitHub 的 events 分支、跑完再傳回去（scripts/events_sync.py），跟 GitHub 共用「已經通知過」的紀錄，
同一件事不會寄兩次；網站也讀同一份資料。Mac 睡眠或關機時由 GitHub 接手（比較慢）。

金鑰都在 macOS 鑰匙圈（使用者自己輸入；這支程式只在執行時讀取、不會印出來）：
    stock-mail  smtp_user／smtp_pass／email_to（對帳信用的，已經有了）
    stock-line  token／user_id（選用：LINE）
    stock-fred  api_key（選用：沒有就不抓美國 FRED 數據，留給 GitHub）
紀錄：~/.stock-screener/logs/mac_events.log；工作資料夾 ~/.stock-screener/evbr
手動跑一次：.venv/bin/python scripts/mac_events.py --full
"""
import argparse
import fcntl
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pandas as pd  # noqa: E402

HOME = Path.home() / ".stock-screener"
EVBR = HOME / "evbr"
RUN_STATE = HOME / "mac_events.json"
PAGES = "https://kalebchen0509.github.io/stock-screener"
FULL_EVERY_MIN = 10
WINDOWS = [((8, 55), (10, 35), ("news",)), ((15, 55), (17, 35), ("news", "ndc")), ((20, 25), (23, 5), ("news", "fred"))]
FAST_FRED = {"us_claims", "us_cfnai", "us_sahm", "us_nfci"}     # 公布時段只抓這幾個「重大」等級的美國數據


def window(now: pd.Timestamp):
    """現在在哪個公布時段 → 要快速檢查的來源；不在時段回傳 None。"""
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
    """鑰匙圈 → 這個程式自己的環境變數（screener.notify 讀環境變數）；回傳 FRED 金鑰（可能是空的）。"""
    for env, (svc, acct) in {"SMTP_USER": ("stock-mail", "smtp_user"), "SMTP_PASS": ("stock-mail", "smtp_pass"),
                             "EMAIL_TO": ("stock-mail", "email_to"), "LINE_CHANNEL_ACCESS_TOKEN": ("stock-line", "token"),
                             "LINE_USER_ID": ("stock-line", "user_id")}.items():
        v = keychain(svc, acct)
        if v:
            os.environ[env] = v
    return keychain("stock-fred", "api_key")


def plan(now: pd.Timestamp, last_full: str | None, force_full: bool = False):
    """→ ("full", None)、("light", 來源) 或 (None, None)。"""
    if force_full or last_full is None or (now.tz_localize(None) - pd.Timestamp(last_full)) >= pd.Timedelta(minutes=FULL_EVERY_MIN - 0.5):
        return "full", None
    w = window(now)
    return ("light", w) if w else (None, None)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--full", action="store_true", help="立刻做一次完整檢查")
    args = ap.parse_args(argv)
    HOME.mkdir(parents=True, exist_ok=True)
    lock = open(HOME / "mac_events.lock", "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)        # 上一次還沒跑完就不重疊
    except BlockingIOError:
        return 0
    now = pd.Timestamp.now(tz="Asia/Taipei")
    rs = json.loads(RUN_STATE.read_text()) if RUN_STATE.exists() else {}
    mode, win = plan(now, rs.get("last_full"), args.full)
    if mode is None:
        return 0

    import events_sync
    from screener import market_events
    fred_key = load_secrets()
    if mode == "full":
        macro = {"sources": ("ndc", "fred", "oecd", "news") if fred_key else ("ndc", "oecd", "news")}
    else:
        src = tuple(s for s in win if s != "fred" or fred_key)
        macro = {"sources": src, "only": FAST_FRED}
    if fred_key:
        macro["fred_key"] = fred_key
    try:
        events_sync.pull(EVBR)
    except Exception as e:  # noqa  （網路斷線、剛從睡眠醒來）
        print(f"{now:%m-%d %H:%M} 下載 events 失敗：{str(e)[:120]}", flush=True)
        return 0
    res = market_events.run(EVBR, pages_url=PAGES, macro=macro, light=(mode == "light"))
    pushed = None
    if mode == "full" or res.get("new_events"):
        pushed = events_sync.push(EVBR)
    if mode == "full":
        rs["last_full"] = now.tz_localize(None).strftime("%Y-%m-%d %H:%M:%S")
        RUN_STATE.write_text(json.dumps(rs))
    print(f"{now:%m-%d %H:%M} {mode} {','.join(macro['sources'])}：新事件 {res.get('new_events')}、重大 {res.get('majors')}、"
          f"寄出 {res.get('sent')}" + ("" if pushed is None else f"、上傳 {'✔' if pushed else '✘'}"), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
