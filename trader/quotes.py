"""盤中提醒用：用永豐（sinopac-sync，正式環境、唯讀）抓台股即時快照，只輸出報價 JSON。

由 scripts/alerts.py 用子程序呼叫（Mac 上 mac_events.py 是專案 .venv，shioaji 在 ~/.sinopac-venv）：
    ~/.sinopac-venv/bin/python trader/quotes.py 2330 0050 6488
輸出（一行 JSON）：{"ok": true, "quotes": {"2330": {"date": "2026-11-10", "open":…, "high":…, "low":…, "close":…}}, "usage_mb": …}

安全：一開始就 silence()（shioaji 的連線訊息含身分證字號）；stdout 只有我們自己的 JSON；stderr 經過 redact()。
不印金鑰、帳號；不下單（sinopac-sync 本身也沒有交易權限）。一次登入抓完全部就登出（不佔連線）。
"""
import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import sinopac  # noqa: E402
from sinopac import login, logout, say, silence  # noqa: E402


def main(codes: list[str]) -> int:
    silence()
    codes = sorted({c.strip().split(".")[0] for c in codes if c.strip()})
    if not codes:
        say(json.dumps({"ok": True, "quotes": {}}))
        return 0
    api = None
    try:
        api, _ = login(simulation=False, service=sinopac.SERVICE)
        cs = []
        for c in codes:
            try:
                k = api.Contracts.Stocks[c]
            except Exception:  # noqa
                k = None
            if k is not None:
                cs.append(k)
        out = {}
        for i in range(0, len(cs), 200):                 # 一次最多 200 檔（官方上限 500）
            for s in api.snapshots(cs[i:i + 200]):
                close = float(getattr(s, "close", 0) or 0)
                if close <= 0:
                    continue
                # ts：奈秒；shioaji 把台北時間當成 UTC 存（不論哪種，日期都一樣）
                d = datetime.fromtimestamp(int(s.ts) / 1e9, tz=timezone.utc).strftime("%Y-%m-%d")
                out[str(s.code)] = {"date": d, "open": float(s.open or close), "high": float(s.high or close),
                                    "low": float(s.low or close), "close": close}
        res = {"ok": True, "quotes": out}
        try:
            u = api.usage()
            res["usage_mb"] = round(float(u.bytes) / 1e6, 1)
            res["limit_mb"] = round(float(u.limit_bytes) / 1e6, 1)
        except Exception:  # noqa
            pass
        say(json.dumps(res))
        return 0
    except SystemExit:
        say(json.dumps({"ok": False, "error": "鑰匙圈沒有 sinopac-sync 或 shioaji 沒裝"}))
        return 1
    except Exception as e:  # noqa  （只回報錯誤類型，不回報內容：內容可能含身分證字號）
        say(json.dumps({"ok": False, "error": e.__class__.__name__}))
        return 1
    finally:
        if api is not None:
            logout(api)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
