"""預覽新聞通知（不寄出）：拿 GitHub events 分支裡最近的事件，用跟正式通知一樣的方式整理，印在畫面上。

    .venv/bin/python scripts/news_preview.py          # 有裝 Claude 命令列就用 AI 整理（會用掉 1 次每日額度以外的訂閱用量）
    .venv/bin/python scripts/news_preview.py --no-ai  # 只看不用 AI 的版本
"""
import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pandas as pd  # noqa: E402

import events_sync  # noqa: E402
from screener import news_brief  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-ai", action="store_true")
    ap.add_argument("-n", type=int, default=4, help="整理幾件事（預設 4）")
    a = ap.parse_args()
    if a.no_ai:
        os.environ["NEWS_AI_MAX"] = "0"
    elif not news_brief.find_claude():
        print("找不到 Claude 命令列（claude）。先安裝並登入，或加 --no-ai 只看不用 AI 的版本。")
        return 1
    d = Path(tempfile.mkdtemp(prefix="evprev_")) / "ev"
    events_sync.pull(d)
    site = json.loads((d / "market_events.json").read_text())
    ev = pd.DataFrame(site["events"])
    ev = ev[ev["kind"].isin(["news", "stress"])].head(60)
    grp = []
    for _, g in ev.groupby([ev["topic"], ev["time"].str[:10], ev["level"]], sort=False):   # 跟正式流程一樣：同主題同天合併
        r = g.iloc[0].to_dict()
        r["more"] = g.iloc[1:5][["title", "source"]].to_dict("records")
        grp.append(r)
    print(f"整理最近 {min(a.n, len(grp))} 件事" + ("" if a.no_ai else "（AI 上網查新聞，約 1～3 分鐘）") + "…\n", flush=True)
    mail, line = news_brief.build(grp[:a.n], "預覽（不會寄出）", site.get("reading"), {}, "preview",
                                  "https://kalebchen0509.github.io/stock-screener")
    print("===== LINE =====\n" + line + "\n\n===== Email =====\n" + mail)
    return 0


if __name__ == "__main__":
    sys.exit(main())
