"""新聞通知整理：市場數字一定出現、AI 摘要（假的命令列）、AI 不能用時退回、LINE 不放網址、每日上限。

用使用者 11/12 的截圖當例子（台股重挫、國安基金）。執行：python -m tests.test_news_brief（不需網路、不呼叫真的 Claude）
"""
import json
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("SCREENER_CACHE", tempfile.mkdtemp())
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from screener import news_brief as B  # noqa: E402

READ = {"^TWII": {"price": 34191.6, "day_chg": -0.045, "day_pts": -1610.2, "week_chg": -0.1388, "week_pts": -5510.0,
                  "from_1y_high": -0.31, "date": "2026-11-10"},
        "^GSPC": {"price": 6500.0, "day_chg": -0.012, "day_pts": -79.0, "week_chg": -0.03, "week_pts": -201.0,
                  "from_1y_high": -0.08, "date": "2026-11-10"},
        "^VIX": {"price": 31.2, "date": "2026-11-10"},
        "flows": {"foreign_net": -1234.5, "margin_chg": -210.0, "date": "2026-11-10"}}
URL = "https://news.google.com/rss/articles/CBMiS0FVX3lxTE16WU1BUk1EZDJWOEUzaU0tdjBtcVVOV?oc=5"


def evs():
    return [{"kind": "stress", "topic": "^TWII_drop_20261110", "label": "台股加權指數單日重挫", "level": "注意",
             "time": "2026-11-10 14:00", "title": "台股加權指數11/10 單日 -4.5%（34,192）", "source": "系統計算", "url": ""},
            {"kind": "news", "topic": "tw_nsf", "label": "國安基金", "level": "注意", "time": "2026-11-11 06:28",
             "title": "鏡論／跟著國安基金一起衝浪", "source": "鏡週刊", "url": URL,
             "more": [{"title": "國安基金第10次進場 護盤", "source": "經濟日報", "url": URL}]}]


def test_market_lines_have_points_and_pct():
    m = B.market_lines(READ)
    assert m[0] == "台股加權指數 34,192（11-10 單日 -1,610 點、-4.5%；一週 -5,510 點、-13.9%；距一年高點 -31.0%）"
    assert any("VIX 恐慌指數 31.2" in x for x in m) and any("外資 11-10 賣超 1,234 億；融資 -210 億" in x for x in m)


def test_ai_summary_used_and_no_urls():
    seen = {}

    def runner(prompt):
        seen["p"] = prompt
        assert "國安基金" in prompt and "台股加權指數單日重挫" not in prompt     # 數字事件不用 AI
        return json.dumps({"result": json.dumps({"items": [{"id": "e1", "short": "國安基金 11/10 第 10 度進場，規模上限 5,000 億元",
                                                             "detail": ["11/10 國安基金委員會決議進場", "可動用額度 5,000 億元", "來源：經濟日報 11/10"]}]}, ensure_ascii=False)})
    st = {}
    mail, line = B.build(evs(), "過去一天的「注意」等級事件", READ, st, "2026-11-11", runner=runner)
    assert "國安基金 11/10 第 10 度進場，規模上限 5,000 億元" in line
    assert "台股加權指數11/10 單日 -4.5%" in line                                # 數字事件照原標題（本身有數字）
    assert "一週 -5,510 點" in line and "http" not in line                       # LINE：有台股數字、沒有網址
    assert "・可動用額度 5,000 億元" in mail and "【目前市場數字】" in mail and "http" not in mail.replace("https://kalebchen", "")
    assert st["news_ai"]["calls"] == 1


def test_fallback_when_ai_fails_or_quota():
    def boom(prompt):
        raise RuntimeError("timeout")
    st = {}
    mail, line = B.build(evs(), "h", READ, st, "2026-11-11", runner=boom)
    assert "國安基金：國安基金第10次進場 護盤" in line and "國安基金第10次進場 護盤（經濟日報）" in mail
    assert "http" not in line and "系統依新聞標題整理" in mail
    os.environ["NEWS_AI_MAX"] = "1"
    st = {"news_ai": {"day": "2026-11-11", "calls": 1}}
    called = []
    B.build(evs(), "h", READ, st, "2026-11-11", runner=lambda p: called.append(1) or "{}")
    assert called == []                                                          # 今天額度用完：不呼叫
    B.build(evs(), "h", READ, st, "2026-11-12", runner=lambda p: called.append(1) or "{}")
    assert called == [1]                                                         # 隔天重新計算
    os.environ.pop("NEWS_AI_MAX")


def test_parse_tolerates_extra_text():
    raw = json.dumps({"result": "好的，以下是整理：\n```json\n{\"items\":[{\"id\":\"e0\",\"short\":\"x\",\"detail\":[]}]}\n```"})
    assert B._parse(raw)["e0"]["short"] == "x"
    assert B._parse("not json") == {}


if __name__ == "__main__":
    ok = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            ok += 1
            print("✔", name)
    print(f"全部通過：{ok} 項")
