"""每日信件「今天要做的事」的檢查。執行：python -m tests.test_routine"""
from screener import routine as R


def main():
    cfg = {"routine": {"enabled": True, "investing": False, "us_account": False,
                       "broker_tw": "永豐金證券「大戶投」App", "broker_us": "永豐金證券複委託"}}
    base = dict(stops=[], near=[], rebalance=False, n_orders=0, n_events=0, n_earn=0, credit_change=False, list_changed=False)
    quiet = R.build(cfg, False, **base)
    assert quiet[0] == "📋 今天要做的事" and "還沒開始投入真錢" in quiet[1] and "✅ 今天不用做任何事" in quiet[2]
    assert quiet[3].startswith("   ↳ 為什麼：") and len(quiet) == 4, quiet
    busy = R.build(cfg, True, **{**base, "stops": ["2753 八方雲集｜連鎖餐飲"], "near": ["CAT 開拓重工｜工程機械"],
                                 "rebalance": True, "n_orders": 12, "n_events": 2, "n_earn": 5, "list_changed": True})
    txt = "\n".join(busy)
    assert "🛑 賣出（觸發停損）：2753 八方雲集｜連鎖餐飲" in txt and "（12 筆）" in txt and "5 則" in txt
    assert "✅" not in txt and "名單有變動" not in txt, "月初換股時不另外提名單變動"
    assert "🗓 本週例行" in txt and "🧾 本月下單步驟" in txt and "永豐金證券「大戶投」App → 零股 → 盤中零股，每檔下限價單" in txt
    assert "還沒開複委託" in txt
    # 每一個項目都附為什麼
    heads = [i for i, s in enumerate(busy) if s and s[:1] in "🛑🧾⚠📰🔄✅📉" or s[:2] in ("1.", "2.", "3.", "4.")]
    for i in heads:
        if busy[i].startswith(("🧾 本月下單步驟", "🗓")):
            continue
        assert busy[i + 1].startswith("   ↳ 為什麼："), busy[i]
    cfg2 = {"routine": {**cfg["routine"], "investing": True, "us_account": True}}
    t2 = "\n".join(R.build(cfg2, False, **{**base, "rebalance": True, "n_orders": 3}))
    assert "還沒開始投入" not in t2 and "用「金額」買（碎股）" in t2 and "🗓" not in t2
    assert R.headline([], False) == "今天不用動作" and R.headline(["x"], True) == "有停損要賣" and R.headline([], True) == "本月下單日"
    assert R.week_key("2026-11-02") == R.week_key("2026-11-08") != R.week_key("2026-11-09")
    print("routine ok\n" + txt)


if __name__ == "__main__":
    main()
