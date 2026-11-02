"""每日信件最上面的「今天要做的事」，加上每週、每月例行步驟（每一步附一句為什麼）。

設定在 config/strategy.yaml 的 routine：
  enabled     每天都寄日報，最上面放今天要做的事
  investing   已經開始投入真錢（false：停損、下單當練習看）
  us_account  已開美股複委託（false：月初步驟會提醒先開戶）
  broker_tw / broker_us  下單用的券商（寫在步驟裡）
"""
from __future__ import annotations

import pandas as pd

WHY = "   ↳ 為什麼："


def _item(head: str, why: str) -> list[str]:
    return [head, f"{WHY}{why}"]


def week_key(today) -> str:
    y, w, _ = pd.Timestamp(today).isocalendar()
    return f"{y}-W{w:02d}"


def todo(cfg: dict, *, stops: list[str], near: list[str], rebalance: bool, n_orders: int, n_events: int,
         n_earn: int, credit_change: bool, list_changed: bool) -> list[str]:
    """今天要做的事。stops／near：已排好的「代號 中文名｜說明」字串。"""
    r = cfg.get("routine") or {}
    out = ["📋 今天要做的事"]
    if not r.get("investing"):
        out.append("（你還沒開始投入真錢：停損和下單先當練習，熟悉流程就好）")
    if stops:
        out += _item("🛑 賣出（觸發停損）：" + "、".join(stops),
                     "停損是唯一要當天處理的事；照規則賣，不要等反彈")
    if rebalance:
        out += _item(f"🧾 本月下單：照信件下方的下單清單（{n_orders} 筆），步驟在下面",
                     "系統每月只在月初用新投入的錢調整一次，平常不換股")
    if near:
        out += _item("⚠️ 接近停損，先不用動作：" + "、".join(near),
                     "這只是預告；真的跌到停損線時會再通知你")
    if n_events:
        out += _item(f"⚠️ 持股或追蹤清單有 {n_events} 則重大事件或負面新聞：花 2 分鐘看標題（在信件下方）",
                     "確認買它的理由有沒有改變；系統不會因為新聞自動賣出")
    if n_earn:
        out += _item(f"📰 另一封「財報快報」有 {n_earn} 則：看財報後的漲跌和營收、獲利",
                     "了解公司實際表現；不需要動作，除非之後觸發停損")
    if credit_change:
        out += _item("📉 信用利差訊號改變（信件下方）：看一下就好",
                     "回測顯示照這個訊號減碼沒有比較好，所以只當參考")
    if list_changed and not rebalance:
        out += _item("🔄 通過條件的名單有變動（信件下方）：不用動作",
                     "換股只在月初做，避免頻繁買賣的手續費和稅")
    if len(out) <= 2:
        out += _item("✅ 今天不用做任何事", "系統的規則是每月調整一次，平常只處理停損")
    return out


def headline(stops: list[str], rebalance: bool) -> str:
    """信件標題的提示，手機通知一眼看出今天要不要動作。"""
    if stops:
        return "有停損要賣"
    if rebalance:
        return "本月下單日"
    return "今天不用動作"


def weekly(cfg: dict) -> list[str]:
    return ["🗓 本週例行（約 15 分鐘，用手機開網站就好）",
            *_item("1. 「並行模擬」頁：記下四個版本的累計報酬",
                   "累積紀錄才看得出差異；幾個月的差距多半是運氣，不要因此改做法"),
            *_item("2. 「財報」頁：看這週公布財報的持股",
                   "確認持股的營收、獲利是不是還在成長，也就是買它的理由還在"),
            *_item("3. 「總覽」頁：大盤在不在 10 個月均線之上、景氣階段",
                   "了解大環境；系統目前只顯示、不據此加減碼，所以不用操作")]


def monthly(cfg: dict) -> list[str]:
    r = cfg.get("routine") or {}
    tw = r.get("broker_tw") or "券商 App"
    us = r.get("broker_us") or "複委託"
    out = ["🧾 本月下單步驟（台股開盤時間 09:00～13:30）",
           *_item("1. 先看信件下方的下單清單；有「賣出」的先賣",
                  "先賣才有錢買；停損或被剔除的股票不再留著"),
           *_item(f"2. 台股：{tw} → 零股 → 盤中零股，每檔下限價單，價格用 App 上當時的賣價（和清單參考價差不多就好）",
                  "盤中零股只能下限價單、每分鐘撮合一次；用當時賣價比較容易成交，當天沒成交隔天再掛")]
    if r.get("us_account"):
        out += _item(f"3. 美股：{us} → VT、SPY 等用「金額」買（碎股）",
                     "碎股可以把每月幾千元剛好用完；美股 ETF 是長期核心，不設停損")
    else:
        out += _item(f"3. 美股：還沒開複委託 → 先到{us}線上申請（通常幾個工作天開好），這個月美股的錢先放著",
                     "系統一半以上的錢要買 VT、SPY 等美股 ETF，沒有帳戶就買不到")
    out += _item("4. 成交和清單不一樣（沒買到、股數不同）→ 告訴 Claude 實際成交的股數",
                 "系統假設你照清單買；記錄不對，停損和下個月的清單都會算錯")
    return out


def build(cfg: dict, week_first: bool, **kw) -> list[str]:
    """完整的例行段落：今天要做的事＋（每週第一封）每週例行＋（月初）下單步驟。"""
    out = todo(cfg, **kw)
    if week_first:
        out += [""] + weekly(cfg)
    if kw.get("rebalance"):
        out += [""] + monthly(cfg)
    return out
