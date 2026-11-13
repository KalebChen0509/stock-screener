"""持股新聞（第二階段）：個股清單整合、重大訊息與 8-K 過濾、媒體新聞比對、AI／規則分級、通知內容、第一次只建基準。

執行：python -m tests.test_stock_news（不需網路、不呼叫真的 Claude）
"""
import json
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("SCREENER_CACHE", tempfile.mkdtemp())
os.environ["NEWS_AI_QUOTA"] = str(Path(tempfile.mkdtemp()) / "q.json")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pandas as pd  # noqa: E402

from screener import stock_news as N  # noqa: E402

NOW = pd.Timestamp("2026-11-13 13:55", tz="Asia/Taipei")
SITE = "https://x.github.io/s"


class R:
    def __init__(self, data=None, text="", content=b"", status=200):
        self._d, self.text, self.content, self.status_code = data, text, content, status

    def json(self):
        return self._d

    def raise_for_status(self):
        pass


ATOM = b"""<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom">
<entry><title>8-K - MICRON TECHNOLOGY INC (0000723125) (Filer)</title><link href="https://sec.gov/a"/>
<summary type="html">&lt;b&gt;Item 2.02: Results of Operations&lt;/b&gt; Item 9.01: Exhibits</summary></entry>
<entry><title>8-K - OTHER CO (0000000001) (Filer)</title><link href="https://sec.gov/b"/><summary>Item 5.07</summary></entry>
</feed>"""


class HTTP:
    def get(self, url, **kw):
        if url.endswith("orders.json"):
            return R({"orders": [{"kind": "stock", "market": "tw", "id": "3526.TWO", "name": "凡甲"},
                                 {"kind": "core", "market": "tw", "id": "0050.TW", "name": "元大台灣50"}]})
        if url.endswith("paper.json"):
            return R({"versions": [{"holdings": {"tw": [["6446", "藥華藥", 0.025], ["2344", "華邦電", 0.02]],
                                                 "us": [["MU", "MICRON TECHNOLOGY INC", 0.025]]}}]})
        if url.endswith("watchlist.yaml"):
            return R(text="tw: ['2330']\nus: []\n")
        if url == N.SEC_TICKERS:
            return R({"0": {"ticker": "MU", "cik_str": 723125}})
        if url == N.SEC_CURRENT:
            return R(content=ATOM)
        return R(status=404)

    def post(self, url, json=None, **kw):
        if url == N.MOPS_LIST:
            return R({"result": {"data": [
                {"companyId": "6446", "companyAbbreviation": "藥華藥", "time": "06:10", "subject": "本公司新藥BESREMi獲\r\n墨西哥核准",
                 "url": {"parameters": {"date": "1151113", "companyId": "6446", "serialNumber": 1}}},
                {"companyId": "2072", "companyAbbreviation": "世紀能源", "time": "07:00", "subject": "公告更名",
                 "url": {"parameters": {"date": "1151113", "companyId": "2072", "serialNumber": 1}}}]}})
        if url == N.MOPS_DETAIL:
            return R({"result": {"data": [[1, "115/11/13", "06:10:23", "x", "y", "z", "s", "第10款", "115/11/12", "1.事實發生日:115/11/12\n2.新藥核准"]]}})
        return R(status=404)


def news(q, lang="zh-TW", since=None, limit=10):
    if "華邦電" in q:
        return [{"date": "2026-11-13 00:30", "title": "華邦電10月營收年增85% 創新高", "source": "經濟日報"}]
    if "DRAM" in q:
        return [{"date": "2026-11-13 00:10", "title": "DRAM合約價第四季漲15%", "source": "工商時報"},
                {"date": "2026-11-13 00:11", "title": "今日天氣晴", "source": "x"}]
    return []


def test_watchlist_merge(tmp=None):
    home = Path(tempfile.mkdtemp())
    (home / ".sinopac" / "sim").mkdir(parents=True)
    (home / ".sinopac" / "sim" / "a.json").write_text(json.dumps({"orders": [{"code": "2344", "name": "華邦電", "status": "Filled"},
                                                                             {"code": "9999", "name": "x", "status": "失敗"}]}))
    W = N.build_watchlist(SITE, "o/r", home=home, http=HTTP())
    assert W["tw"]["3526"]["tier"] == "hold" and "0050" not in W["tw"]          # ETF 不算個股
    assert W["tw"]["2344"]["tier"] == "hold" and W["tw"]["6446"]["tier"] == "watch"   # 模擬持股升級成 hold
    assert "9999" not in W["tw"] and W["tw"]["2330"]["tier"] == "watch"
    assert W["us"]["MU"]["name"] == "Micron Technology"


def _W():
    return {"tw": {"6446": {"name": "藥華藥", "tier": "watch"}, "2344": {"name": "華邦電", "tier": "hold"}},
            "us": {"MU": {"name": "Micron Technology", "tier": "watch"}}}


def test_first_run_baseline_then_rules_then_ai():
    sp = Path(tempfile.mkdtemp()) / "st.json"
    sent = []
    send = lambda s, t, line_text=None: sent.append((s, t, line_text)) or True   # noqa: E731
    r = N.run(sp, NOW, SITE, "o/r", send=send, http=HTTP(), fetch_news=news, sec_ua="ua x@y", W=_W(),
              ai_runner=lambda p: (_ for _ in ()).throw(RuntimeError("no")))
    assert r["first_run"] and sent == []
    st = json.loads(sp.read_text())
    assert any("6446" in k for k in st["ann_seen"]) and not any("2072" in k for k in st["ann_seen"])   # 只留關注的公司
    # 新的一則公告＋新聞；AI 失敗 → 規則：新藥「核准」＝重大
    st["ann_seen"] = []
    st["news_seen"] = []
    st["last_media"] = None
    sp.write_text(json.dumps(st))
    r = N.run(sp, NOW, SITE, "o/r", send=send, http=HTTP(), fetch_news=news, sec_ua="ua x@y", W=_W(),
              ai_runner=lambda p: (_ for _ in ()).throw(RuntimeError("no")))
    subj, mail, line = sent[-1]
    assert "藥華藥（6446）重大訊息" in subj and "MU" in subj                   # 8-K Item 2.02 也是重大
    assert "公告內容：1.事實發生日" in mail and "http" not in line
    st = json.loads(sp.read_text())
    assert any("華邦電10月營收" in p["title"] for p in st["pending"])             # 新聞沒 AI → 進彙整
    assert not any("天氣" in p["title"] for p in st["pending"])                   # 產業新聞要有關鍵字
    # AI 版：把華邦電新聞判成重大、DRAM 新聞標出影響華邦電
    st["news_seen"], st["last_media"], st["pending"] = [], None, []
    sp.write_text(json.dumps(st))

    def ai(prompt):
        rows = json.loads(prompt.split("事件：\n", 1)[1])
        items = []
        for x in rows:
            if "華邦電10月" in x["標題"]:
                items.append({"id": x["id"], "level": "重大", "short": "華邦電10月營收年增85%，創新高", "detail": ["營收 x 億元"], "affects": ["2344"]})
            elif "DRAM" in x["標題"]:
                items.append({"id": x["id"], "level": "一般", "short": "DRAM第四季合約價漲15%", "detail": ["工商時報"], "affects": ["2344", "MU"]})
        return json.dumps({"result": json.dumps({"items": items}, ensure_ascii=False)})
    N.run(sp, NOW, SITE, "o/r", send=send, http=HTTP(), fetch_news=news, W=_W(), ai_runner=ai)
    subj, mail, line = sent[-1]
    assert "華邦電10月營收年增85%，創新高" in line and "華邦電（2344，持股）" in line
    st = json.loads(sp.read_text())
    dram = [p for p in st["pending"] if "DRAM" in p["title"]][0]
    assert dram["_ai"]["affects"] == ["2344", "MU"]
    # 早上 7～12 點寄彙整
    N.run(sp, pd.Timestamp("2026-11-14 09:30", tz="Asia/Taipei"), SITE, "o/r", send=send, http=HTTP(),
          fetch_news=lambda *a, **k: [], W=_W(), ai_runner=ai)
    subj, mail, line = sent[-1]
    assert subj.startswith("【注意】持股與產業新聞彙整") and "DRAM第四季合約價漲15%" in line and "Micron Technology（MU）" in line


def test_rule_levels():
    assert N.rule_level({"kind": "ann_tw", "title": "公告本公司董事會決議召開股東會"}) == "注意"
    assert N.rule_level({"kind": "ann_tw", "title": "本公司接獲重大訂單"}) == "重大"
    assert N.rule_level({"kind": "ann_us", "items": ["5.07", "9.01"]}) == "注意"
    assert N.rule_level({"kind": "ann_us", "items": ["5.02"]}) == "重大"


def test_clean_dup_and_skip():
    assert N._clean("來源：民視新聞，2026-11-13；https://www.ftvnews.com.tw/x") == "來源：民視新聞，2026-11-13"
    assert N._clean("媒體轉載重大訊息（同s0），核准機關為墨西哥") == "媒體轉載重大訊息，核准機關為墨西哥"
    sp = Path(tempfile.mkdtemp()) / "st.json"
    sp.write_text(json.dumps({"ann_seen": [], "news_seen": []}))
    def news2(q, lang="zh-TW", since=None, limit=10):
        if "藥華藥" in q:
            return [{"date": "2026-11-13 00:30", "title": "藥華藥新藥獲墨西哥核准", "source": "富聯網"},
                    {"date": "2026-11-13 00:31", "title": "藥華藥員工旅遊照片曝光", "source": "x"}]
        return []
    def ai(prompt):
        rows = json.loads(prompt.split("事件：\n", 1)[1])
        ann = [x["id"] for x in rows if x["類型"] == "台股重大訊息"][0]
        out = []
        for x in rows:
            if x["類型"] == "台股重大訊息":
                out.append({"id": x["id"], "level": "重大", "short": "新藥獲墨西哥核准", "detail": ["見公告 https://a.b"], "affects": []})
            elif "核准" in x["標題"]:
                out.append({"id": x["id"], "level": "重大", "dup_of": ann, "short": "同上", "detail": [], "affects": []})
            else:
                out.append({"id": x["id"], "level": "略過", "short": "八卦", "detail": [], "affects": []})
        return json.dumps({"result": json.dumps({"items": out}, ensure_ascii=False)})
    sent = []
    r = N.run(sp, NOW, SITE, "o/r", send=lambda s, t, line_text=None: sent.append((s, t, line_text)) or True,
              http=HTTP(), fetch_news=news2, W=_W(), ai_runner=ai)
    subj, mail, line = sent[-1]
    assert line.count("藥華藥") >= 1 and line.count("【重大】") == 1                 # 公告＋轉載新聞合併成一則
    assert "公開資訊觀測站、富聯網" in mail and "https" not in mail and "員工旅遊" not in mail + line
    assert r["skipped"] == 2


if __name__ == "__main__":
    ok = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            ok += 1
            print("✔", name)
    print(f"全部通過：{ok} 項")
