"""市場重大事件偵測（2026/11/07）：政策新聞＋市場壓力數字，分兩級通知。

為什麼：11/06 國安基金進場、金管會限空令，系統完全不知道（只有個股層級的新聞與重大訊息）。

1. 政策／事件新聞（Google 新聞 RSS，免費、免金鑰）：台灣（國安基金、限空令、金管會、央行、暫停交易、台海、強震）、
   美國（聯準會、熔斷、銀行危機、政策衝擊）、全球地緣政治。
   「重大」＝標題有確定的字（宣布、決議、啟動、上路…）而且沒有推測的字（可能、恐、傳、是否、？…）；其他相關新聞＝「注意」。
2. 市場壓力數字（系統自己算，不靠新聞）：台股／美股單日重挫、距一年高點跌幅（國安基金歷史進場區間）、VIX、
   外資單日賣超、融資單日減少（斷頭潮）、新台幣大幅變動、月底大盤趨勢翻轉。
3. 通知：「重大」不分日夜立刻寄（Email＋LINE）；「注意」每天早上 7 點後彙整一封。同一件事一天只寄一次。
4. 紀錄：events.csv（全部歷史）、market_events.json（網站用，近 60 天）、state.json（去重用）放在 events 分支。

只是資訊，不是買賣建議，也不會下單。
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import numpy as np
import pandas as pd
import requests

from .common import log

TZ = "Asia/Taipei"
UA = {"User-Agent": "Mozilla/5.0 (stock-screener; personal research)"}

# (代號, 名稱, Google 新聞搜尋字, 語言, 分類, 可以是「重大」嗎)
TOPICS = [
    ("tw_nsf", "國安基金", "國安基金", "zh", "台灣政策", True),
    ("tw_short", "限空令／禁止放空", "限空令 OR 平盤下禁止放空 OR 平盤以下不得放空", "zh", "台灣政策", True),
    ("tw_fsc", "金管會穩定市場措施", "金管會 穩定市場措施 OR 金管會 救市 OR 金管會 護盤", "zh", "台灣政策", True),
    ("tw_cbc", "央行利率／匯率", "央行 理監事會 利率 OR 央行 升息 OR 央行 降息 OR 央行 存款準備率 OR 央行 進場 新台幣", "zh", "台灣政策", True),
    ("tw_halt", "台股暫停交易／系統異常", "證交所 暫停交易 OR 台股 停止交易 OR 證交所 系統異常 OR 櫃買 暫停交易", "zh", "台灣市場", True),
    ("tw_crash", "台股重挫", "台股 重挫 OR 台股 崩跌 OR 台股 最大跌點", "zh", "台灣市場", False),
    ("tw_foreign", "外資大賣／匯出", "外資 賣超 創 OR 外資 匯出 創 OR 外資 提款", "zh", "台灣市場", False),
    ("tw_geo", "台海情勢", "台海 軍演 OR 解放軍 演習 台灣 OR 兩岸 封鎖 OR 台海 衝突", "zh", "地緣政治", True),
    ("tw_quake", "台灣強震／停電", "強震 晶圓廠 OR 地震 台積電 停工 OR 台灣 大停電", "zh", "天災", True),
    ("us_fed", "美國聯準會", "Federal Reserve emergency rate cut OR Fed cuts rates OR Fed raises rates OR FOMC decision", "en", "美國政策", True),
    ("us_halt", "股市熔斷（美國與各國）", "market-wide circuit breaker OR stock trading halted NYSE OR circuit breaker triggered stocks", "en", "美國市場", True),
    ("us_crash", "美股重挫", "stocks plunge worst day S&P 500 OR Dow plunges", "en", "美國市場", False),
    ("us_bank", "銀行危機", "bank failure FDIC OR bank run OR bank collapse", "en", "美國金融", True),
    ("us_policy", "美國政策衝擊", "new tariffs announced OR government shutdown begins OR debt ceiling default", "en", "美國政策", True),
    ("gl_geo", "全球地緣政治", "invasion launched OR war declared OR missile strikes markets", "en", "地緣政治", True),
]
CONFIRM_ZH = ["宣布", "決議", "拍板", "啟動", "正式", "上路", "實施", "進場", "出手", "調升", "調降", "升息", "降息", "暫停交易",
              "停止交易", "生效", "發布", "祭出", "開始", "動用", "召開臨時"]
CONFIRM_EN = ["announces", "announced", "approves", "approved", "cuts rates", "raises rates", "halted", "triggered", "declares",
              "imposes", "collapses", "collapsed", "seized", "fails", "failed", "launches", "launched", "begins", "takes effect"]
SPEC_ZH = ["可能", "恐", "是否", "傳", "若", "不排除", "預期", "會不會", "嗎", "？", "?", "有望", "盼", "籲", "呼籲", "猜", "如果", "考慮", "評估"]
SPEC_EN = ["may ", "could", "might", "expected", "considering", "would", " if ", "?", "odds", "bets", "weighs", "mulls", "fears", "poll",
           "nearly", "watch", "threshold", "edge", "risk of", "how ", "what ", "why "]
# 標題一定要有的字（Google 新聞的 OR 搜尋很寬，會混進不相關的新聞）
MUST = {
    "tw_nsf": ["國安基金"], "tw_short": ["限空", "放空", "禁空"], "tw_fsc": ["金管會"], "tw_cbc": ["央行"],
    "tw_halt": ["暫停交易", "停止交易", "系統異常", "交易中斷"], "tw_crash": ["重挫", "崩", "跌點", "大跌", "暴跌"],
    "tw_foreign": ["外資"], "tw_geo": ["軍演", "演習", "封鎖", "台海", "衝突", "解放軍"], "tw_quake": ["地震", "強震", "停電"],
    "us_fed": ["fed", "federal reserve", "fomc", "powell"], "us_halt": ["circuit breaker", "halt", "trading freeze", "熔斷"],
    "us_crash": ["plunge", "worst", "tumble", "sell-off", "selloff", "rout", "crash"], "us_bank": ["bank"],
    "us_policy": ["tariff", "shutdown", "default", "debt ceiling"], "gl_geo": ["war", "invasion", "missile", "strike", "attack"],
}


def relevant(topic: str, title: str) -> bool:
    t = title.lower()
    return any(w in t for w in MUST.get(topic, [""]))


# ---------------------------------------------------------------- 新聞
def classify(title: str, lang: str, can_major: bool) -> str:
    """「重大」：有確定的字、沒有推測的字；其他＝「注意」。"""
    t = title if lang == "zh" else f" {title.lower()} "
    conf = any(w in t for w in (CONFIRM_ZH if lang == "zh" else CONFIRM_EN))
    spec = any(w in t for w in (SPEC_ZH if lang == "zh" else SPEC_EN))
    return "重大" if (can_major and conf and not spec) else "注意"


def news_events(now: pd.Timestamp, lookback_h: float = 36, fetch=None) -> list[dict]:
    """搜尋每個主題最近的新聞 → 事件清單（每則新聞一筆，之後再依主題、日期合併）。"""
    from .events import google_news
    fetch = fetch or google_news
    since = (now.tz_convert("UTC") - pd.Timedelta(hours=lookback_h)).tz_localize(None)
    out = []
    for key, label, q, lang, cat, can_major in TOPICS:
        try:
            items = fetch(q, lang="zh-TW" if lang == "zh" else "en", since=since, limit=8)
        except Exception as e:  # noqa
            log.warning("market events news %s: %s", key, e)
            continue
        for it in items:
            title = (it.get("title") or "").strip()
            if not title or not relevant(key, title):
                continue
            t = pd.to_datetime(it.get("date"), errors="coerce")
            ts = (t.tz_localize("UTC").tz_convert(TZ) if t is not None and not pd.isna(t) else now)
            out.append({"kind": "news", "topic": key, "label": label, "category": cat, "level": classify(title, lang, can_major),
                        "time": ts.strftime("%Y-%m-%d %H:%M"), "title": title, "source": it.get("source") or "", "url": it.get("url") or ""})
    return out


# ---------------------------------------------------------------- 市場壓力數字
def _twse_json(url: str, http=None):
    try:
        r = (http or requests).get(url, headers=UA, timeout=30)
        return r.json() if r.status_code == 200 else None
    except Exception:  # noqa
        return None


def twse_flows(day: pd.Timestamp, http=None) -> dict:
    """某一天：外資（不含外資自營商）買賣超金額（億元）與融資餘額（億元）、較前一日變化。沒有資料回傳空 dict。"""
    d8 = day.strftime("%Y%m%d")
    out = {}
    js = _twse_json(f"https://www.twse.com.tw/rwd/zh/fund/BFI82U?type=day&dayDate={d8}&response=json", http)
    if js and js.get("stat") == "OK":
        for r in js.get("data") or []:
            if "外資及陸資" in str(r[0]) and "不含" in str(r[0]):
                out["foreign_net"] = float(str(r[3]).replace(",", "")) / 1e8
    mj = _twse_json(f"https://www.twse.com.tw/rwd/zh/marginTrading/MI_MARGN?date={d8}&selectType=MS&response=json", http)
    for t in (mj or {}).get("tables") or []:
        for r in t.get("data") or []:
            if str(r[0]).startswith("融資金額"):
                today_, prev = float(str(r[5]).replace(",", "")), float(str(r[4]).replace(",", ""))
                out["margin"], out["margin_chg"] = today_ / 1e5, (today_ - prev) / 1e5      # 千元 → 億元
    return out


def price_history(tickers: list[str], download=None) -> pd.DataFrame:
    """日收盤寬表（含今天盤中最新價）。"""
    # 一檔一檔下載：一起下載時 Yahoo 會把台股最新一天對不上日期而漏掉（11/06 加權指數就漏了）
    if download is None:
        import yfinance as yf

        def download(t):
            d = yf.download(t, period="15mo", interval="1d", progress=False, auto_adjust=False)
            c = d["Close"]
            return c.iloc[:, 0] if isinstance(c, pd.DataFrame) else c
    cols = {}
    for t in tickers:
        try:
            c = download(t)
            c.index = pd.to_datetime(c.index).tz_localize(None).normalize()
            cols[t] = c[~c.index.duplicated(keep="last")]
        except Exception as e:  # noqa
            log.warning("price %s: %s", t, e)
    return pd.DataFrame(cols).sort_index()


def twse_taiex(state: dict, now: pd.Timestamp, months: int = 13, http=None) -> pd.Series:
    """證交所官方加權指數收盤（Yahoo 的 ^TWII 會漏最新一天，11/06 就漏了）。
    存在 state["taiex"]，之後每次只重抓這個月和上個月。"""
    cache = state.setdefault("taiex", {})
    done = set(state.setdefault("taiex_months", []))
    cur = now.tz_localize(None).to_period("M")
    for i in range(months):
        m = cur - i
        if str(m) in done and i > 1:
            continue
        js = _twse_json(f"https://www.twse.com.tw/rwd/zh/TAIEX/MI_5MINS_HIST?date={m.to_timestamp():%Y%m01}&response=json", http)
        if not js or js.get("stat") != "OK":
            continue
        for r in js.get("data") or []:
            y, mo, d = str(r[0]).split("/")
            cache[f"{int(y) + 1911}-{mo}-{d}"] = float(str(r[4]).replace(",", ""))
        if i > 1:
            done.add(str(m))
    state["taiex_months"] = sorted(done)[-24:]
    keep = sorted(cache)[-400:]
    state["taiex"] = {k: cache[k] for k in keep}
    s = pd.Series(state["taiex"], dtype=float)
    s.index = pd.to_datetime(s.index)
    return s.sort_index()


def stress_events(now: pd.Timestamp, px: pd.DataFrame, flows: dict, state: dict) -> tuple[list[dict], dict]:
    """依數字產生事件；回傳 (事件, 目前讀數)。跌幅類用「一個波段只通知一次」（回到門檻上方才重新啟動）。"""
    ev, read = [], {}
    arm = state.setdefault("armed", {})
    day = now.strftime("%Y-%m-%d")

    def add(rule, label, level, text, cat):
        ev.append({"kind": "stress", "topic": rule, "label": label, "category": cat, "level": level,
                   "time": now.strftime("%Y-%m-%d %H:%M"), "title": text, "source": "系統計算", "url": ""})

    for tk, name, cat in (("^TWII", "台股加權指數", "台灣市場"), ("^GSPC", "美股 S&P 500", "美國市場")):
        s = px[tk].dropna() if tk in px else pd.Series(dtype=float)
        if len(s) < 30:
            continue
        chg = s.iloc[-1] / s.iloc[-2] - 1
        dd = s.iloc[-1] / s.iloc[-252:].max() - 1
        read[tk] = {"price": float(s.iloc[-1]), "day_chg": float(chg), "from_1y_high": float(dd), "date": s.index[-1].strftime("%Y-%m-%d")}
        if chg <= -0.03:
            add(f"{tk}_drop_{s.index[-1]:%Y%m%d}", f"{name}單日重挫", "重大" if chg <= -0.05 else "注意",
                f"{name}{s.index[-1]:%m/%d} 單日 {chg:+.1%}（{s.iloc[-1]:,.0f}）", cat)
        fired = False
        for th, lvl, rearm in ((-0.20, "重大", -0.15), (-0.10, "注意", -0.07)):
            k = f"{tk}_dd{int(-th * 100)}"
            if dd <= th and arm.get(k, True):
                if not fired:                           # 同時跨過 −20% 和 −10% 只發比較嚴重的那一則
                    note = "；國安基金歷史上進場時，大盤距一年高點約 −15%～−43%" if tk == "^TWII" and th == -0.20 else ""
                    add(k + f"_{day}", f"{name}距一年高點 {int(th * 100)}%", lvl, f"{name}距一年高點已跌 {dd:.1%}{note}", cat)
                    fired = True
                arm[k] = False
            elif dd > rearm:
                arm[k] = True
        # 月底趨勢：上一個完整月份月底收盤 vs 10 個月均價（回測的避險規則）
        me = s.resample("ME").last()
        if len(me) >= 12:
            last_full = me.iloc[:-1] if me.index[-1] >= s.index[-1].normalize() + pd.offsets.MonthEnd(0) else me
            below = bool(last_full.iloc[-1] < last_full.iloc[-10:].mean())
            k = f"{tk}_trend"
            if state.get(k) is not None and state[k] != below:
                add(f"{k}_{last_full.index[-1]:%Y%m}", f"{name}月底趨勢翻轉", "注意",
                    f"{name}{last_full.index[-1]:%Y-%m} 月底收盤{'跌破' if below else '站回'} 10 個月均線（回測：只對長空頭有保護作用）", cat)
            state[k] = below
            read[tk]["below_10m_ma"] = below
    v = px["^VIX"].dropna() if "^VIX" in px else pd.Series(dtype=float)
    if len(v):
        read["^VIX"] = {"price": float(v.iloc[-1]), "date": v.index[-1].strftime("%Y-%m-%d")}
        for th, lvl in ((40, "重大"), (30, "注意")):
            k = f"vix{th}"
            if v.iloc[-1] >= th and arm.get(k, True):
                add(f"{k}_{day}", f"VIX ≥ {th}", lvl, f"美國 VIX 恐慌指數 {v.iloc[-1]:.1f}（≥ {th}）", "美國市場")
                arm[k] = False
                break
            if v.iloc[-1] < th - 5:
                arm[k] = True
    f = px["TWD=X"].dropna() if "TWD=X" in px else pd.Series(dtype=float)
    if len(f) >= 2:
        c = f.iloc[-1] / f.iloc[-2] - 1
        read["TWD=X"] = {"price": float(f.iloc[-1]), "day_chg": float(c), "date": f.index[-1].strftime("%Y-%m-%d")}
        if abs(c) >= 0.01:
            add(f"twd_{f.index[-1]:%Y%m%d}", "新台幣大幅變動", "注意", f"新台幣{'貶值' if c > 0 else '升值'} {abs(c):.1%}（1 美元＝{f.iloc[-1]:.2f}）", "台灣市場")
    if flows.get("date"):
        read["flows"] = flows
        fn, mc = flows.get("foreign_net"), flows.get("margin_chg")
        if fn is not None and fn <= -800:
            add(f"foreign_{flows['date']}", "外資單日大賣", "重大" if fn <= -2000 else "注意",
                f"外資 {flows['date'][5:]} 賣超 {abs(fn):,.0f} 億元", "台灣市場")
        if mc is not None and mc <= -200:
            add(f"margin_{flows['date']}", "融資大減（斷頭潮）", "重大" if mc <= -400 else "注意",
                f"融資餘額 {flows['date'][5:]} 單日減少 {abs(mc):,.0f} 億元（剩 {flows.get('margin', 0):,.0f} 億），可能是融資追繳斷頭", "台灣市場")
    return ev, read


# ---------------------------------------------------------------- 合併、去重、通知
def _key(e: dict) -> str:
    """同一主題、同一天（台北）、同一級＝同一件事。數字事件本身的 topic 已含日期。"""
    if e["kind"] == "stress":
        return e["topic"]
    return f"{e['topic']}|{e['time'][:10]}|{e['level']}"


def _hid(title: str) -> str:
    return hashlib.md5(re.sub(r"\W+", "", title).encode()).hexdigest()[:12]


def merge(new: list[dict], state: dict) -> tuple[list[dict], list[dict]]:
    """回傳 (第一次出現的事件, 要記到歷史的全部新項目)。同一件事的多則新聞合併成一則事件（附最多 3 個標題）。"""
    seen_t = set(state.setdefault("titles", []))
    seen_k = set(state.setdefault("keys", []))
    fresh, hist = {}, []
    for e in sorted(new, key=lambda x: x["time"]):
        h = _hid(e["title"])
        if h in seen_t:
            continue
        seen_t.add(h)
        hist.append(e)
        k = _key(e)
        # 已經有同主題同天的「重大」，就不再為「注意」另發
        major_k = f"{e['topic']}|{e['time'][:10]}|重大"
        if e["kind"] == "news" and e["level"] == "注意" and (major_k in seen_k or major_k in fresh):
            continue
        if k in seen_k:
            continue
        if k in fresh:
            fresh[k].setdefault("more", []).append({"title": e["title"], "source": e["source"], "url": e["url"]})
            continue
        fresh[k] = dict(e)
    for k in fresh:
        seen_k.add(k)
    state["titles"] = list(seen_t)[-3000:]
    state["keys"] = list(seen_k)[-3000:]
    return list(fresh.values()), hist


def render(evs: list[dict], header: str, pages_url: str = "") -> str:
    lines = [header, ""]
    for e in evs:
        lines.append(f"【{e['level']}】{e['label']}｜{e['time']}")
        lines.append(f"  {e['title']}" + (f"（{e['source']}）" if e.get("source") else ""))
        if e.get("url"):
            lines.append(f"  {e['url']}")
        for m in (e.get("more") or [])[:3]:
            lines.append(f"  ・{m['title']}（{m['source']}）")
        lines.append("")
    if pages_url:
        lines.append(f"網站「市場訊號」：{pages_url}/#signals")
    lines.append("這是系統自動整理的資訊，不是買賣建議。新聞分級靠標題用字判斷，可能有誤。")
    return "\n".join(lines)


def notify(majors: list[dict], digest: list[dict], pages_url: str = "", send=None) -> dict:
    """重大＝立刻寄；digest＝每日彙整。send(subject, text) 預設用 Email＋LINE。"""
    if send is None:
        from .notify import send_email, send_line

        def send(subject, text):
            a = send_email(subject, text)
            b = send_line(f"{subject}\n\n{text}")
            return a or b
    sent = {"major": 0, "digest": 0}
    if majors:
        subj = "【重大】" + "、".join(dict.fromkeys(e["label"] for e in majors))
        if send(subj[:80], render(majors, "市場重大事件（立即通知）", pages_url)):
            sent["major"] = len(majors)
    if digest:
        if send(f"【注意】市場事件彙整 {len(digest)} 則", render(digest, "過去一天的「注意」等級事件", pages_url)):
            sent["digest"] = len(digest)
    return sent


def run(store: Path, now=None, fetch_news=None, px=None, flows=None, send=None, pages_url: str = "") -> dict:
    """完整流程（workflow 每 30 分鐘跑一次）。store＝events 分支的資料夾。"""
    store = Path(store)
    store.mkdir(parents=True, exist_ok=True)
    now = pd.Timestamp(now).tz_localize(TZ) if now is not None and pd.Timestamp(now).tz is None else (pd.Timestamp(now) if now is not None else pd.Timestamp.now(tz=TZ))
    sp = store / "state.json"
    state = json.loads(sp.read_text()) if sp.exists() else {}
    first_run = not sp.exists()
    evs = news_events(now, fetch=fetch_news)
    reading = {}
    try:
        if px is None:
            px = price_history(["^TWII", "^GSPC", "^VIX", "TWD=X"])
            try:                                        # 台股指數以證交所官方收盤為準，Yahoo 只補今天盤中
                off = twse_taiex(state, now)
                if len(off):
                    px = px.reindex(px.index.union(off.index))
                    px["^TWII"] = off.reindex(px.index).combine_first(px["^TWII"])
            except Exception as e:  # noqa
                log.warning("twse taiex failed: %s", e)
        if flows is None:
            flows = {}
            for back in range(0, 6):                    # 最近一個有資料的交易日（收盤後約 15:00 才有）
                d = (now - pd.Timedelta(days=back)).tz_localize(None).normalize()
                if d.weekday() >= 5:
                    continue
                f = twse_flows(d)
                if f.get("foreign_net") is not None:
                    flows = {**f, "date": d.strftime("%Y-%m-%d")}
                    break
        sev, reading = stress_events(now, px, flows, state)
        evs += sev
    except Exception as e:  # noqa
        log.warning("market stress failed: %s", e)
    fresh, hist = merge(evs, state)
    majors = [e for e in fresh if e["level"] == "重大"]
    pend = state.setdefault("pending", [])
    pend += [e for e in fresh if e["level"] == "注意"]
    # 第一次執行只建立基準（不把過去 36 小時的舊聞全部寄出）
    digest = []
    today = now.strftime("%Y-%m-%d")
    # 「注意」彙整：每天早上 7～12 點寄一封；GitHub 延遲錯過早上時，累積超過 20 小時才補寄
    oldest = min((pd.Timestamp(e["time"]) for e in pend), default=None)
    late = oldest is not None and (now.tz_localize(None) - oldest) > pd.Timedelta(hours=20)
    if not first_run and pend and state.get("digest_day") != today and (7 <= now.hour < 12 or (now.hour >= 12 and late)):
        digest, state["pending"], state["digest_day"] = pend[-40:], [], today
    sent = {"major": 0, "digest": 0} if first_run else notify(majors, digest, pages_url, send)
    if first_run:
        state["pending"] = []
    # 歷史與網站
    hp = store / "events.csv"
    if hist:
        new = pd.DataFrame(hist)
        old = pd.read_csv(hp) if hp.exists() else pd.DataFrame()
        pd.concat([old, new], ignore_index=True).to_csv(hp, index=False)
    allh = pd.read_csv(hp) if hp.exists() else pd.DataFrame(columns=["time"])
    keep = allh[pd.to_datetime(allh["time"], errors="coerce") >= (now.tz_localize(None) - pd.Timedelta(days=60))] if len(allh) else allh
    site = {"updated": now.strftime("%Y-%m-%d %H:%M"), "reading": reading,
            "events": keep.sort_values("time", ascending=False).head(300).replace({np.nan: None}).to_dict("records") if len(keep) else []}
    (store / "market_events.json").write_text(json.dumps(site, ensure_ascii=False, default=str))
    sp.write_text(json.dumps(state, ensure_ascii=False))
    res = {"new_events": len(fresh), "majors": len(majors), "digest": len(digest), "sent": sent, "first_run": first_run}
    log.info("market events: %s", res)
    return res
