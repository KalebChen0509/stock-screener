"""每月下單清單：用「新投入的錢」補足低於目標比例的部位，原則上不賣股。

只有兩種情況會賣：停損觸發、或股票已不在系統建議組合中（被剔除）。
賣出的錢、上個月買不滿一股剩下的零錢，都併入本月可用資金。

entries（目標組合）每一筆：
  key      唯一代號（用 Yahoo 代號，例如 2330.TW、AAPL、VT）
  sleeve   core / tw / us / etf
  kind     core / stock / fill / etf
  id, name
  market   tw / us
  weight   目標比重（全部加總 = 1，或略小於 1；剩下的是現金）
  no_buy   True = 只持有、不再加碼（例如自己原本的持股）
  fractional True = 可買零碎股（美股定期定額），否則台股以 1 股、美股以 1 股為單位
"""
from __future__ import annotations

import math


def plan_orders(entries: list[dict], holdings: dict, prices: dict, fx: float,
                contribution_twd: float, cash_twd: float = 0.0,
                sell_keys: set | None = None, names: dict | None = None) -> dict:
    sell_keys = set(sell_keys or [])
    names = names or {}
    ent = {e["key"]: e for e in entries}

    def to_twd(key, local, market):
        return local * (fx if market == "us" else 1.0)

    def market_of(key):
        if key in ent:
            return ent[key]["market"]
        return "tw" if str(key).endswith((".TW", ".TWO")) else "us"

    orders = []
    # ---------- 1. 賣出：停損、被剔除 ----------
    proceeds = 0.0
    kept = {}
    for key, sh in holdings.items():
        if not sh:
            continue
        px = prices.get(key)
        mkt = market_of(key)
        if key in sell_keys or key not in ent:
            val = to_twd(key, sh * px, mkt) if px else 0.0
            proceeds += val
            orders.append({"key": key, "id": ent.get(key, {}).get("id", key), "name": ent.get(key, {}).get("name") or names.get(key, ""),
                           "sleeve": ent.get(key, {}).get("sleeve", mkt), "kind": ent.get(key, {}).get("kind", "stock"),
                           "market": mkt, "action": "sell", "reason": "停損" if key in sell_keys else "已剔除",
                           "price": px, "shares": -sh, "amount_twd": -round(val), "holding_after": 0,
                           "target_weight": 0.0, "weight_after": 0.0})
        else:
            kept[key] = sh

    # ---------- 2. 目前價值與目標 ----------
    cur = {k: (to_twd(k, kept.get(k, 0) * prices[k], ent[k]["market"]) if prices.get(k) else 0.0) for k in ent}
    budget = contribution_twd + cash_twd + proceeds
    total = sum(cur.values()) + budget
    buyable = [k for k, e in ent.items() if not e.get("no_buy") and prices.get(k)]
    gaps = {k: max(0.0, ent[k]["weight"] * total - cur[k]) for k in buyable}
    gsum = sum(gaps.values())
    alloc = {}
    if gsum >= budget and gsum > 0:
        alloc = {k: g * budget / gsum for k, g in gaps.items()}
    else:
        wsum = sum(ent[k]["weight"] for k in buyable) or 1.0
        extra = budget - gsum
        alloc = {k: gaps[k] + extra * ent[k]["weight"] / wsum for k in buyable}

    # ---------- 3. 換成股數 ----------
    spent = 0.0
    for k, e in ent.items():
        px = prices.get(k)
        have = kept.get(k, 0)
        a = alloc.get(k, 0.0)
        o = {"key": k, "id": e["id"], "name": e.get("name", ""), "sleeve": e["sleeve"], "kind": e["kind"],
             "market": e["market"], "price": px, "target_weight": e["weight"],
             "current_weight": (cur[k] / total) if total else 0.0, "no_buy": bool(e.get("no_buy"))}
        if not px:
            o.update(action="hold", shares=0, amount_twd=0, holding_after=have, reason="無報價")
        elif e.get("fractional"):
            usd = a / fx if e["market"] == "us" else a
            sh = math.floor(usd / px * 1e4) / 1e4
            cost = to_twd(k, sh * px, e["market"])
            spent += cost
            o.update(action="buy" if sh > 0 else "hold", shares=sh, amount_local=round(sh * px, 2),
                     amount_twd=round(cost), holding_after=round(have + sh, 4),
                     reason="定期定額（可買零碎股）")
        else:
            unit_twd = to_twd(k, px, e["market"])
            sh = int(a // unit_twd) if unit_twd > 0 else 0
            cost = sh * unit_twd
            spent += cost
            reason = "台股零股" if e["market"] == "tw" else "複委託整股"
            if sh == 0 and a > 0:
                reason = "金額不足 1 股，錢先保留，下個月合併再買"
            if e.get("no_buy"):
                reason = "原有持股，只持有不加碼"
            o.update(action="buy" if sh > 0 else "hold", shares=sh, amount_local=round(sh * px, 2),
                     amount_twd=round(cost), holding_after=have + sh, reason=reason)
        o["weight_after"] = None
        orders.append(o)
    left = budget - spent
    after_total = sum(to_twd(o["key"], (o["holding_after"] or 0) * (o["price"] or 0), o["market"])
                      for o in orders if o["action"] != "sell") + left
    for o in orders:
        if o["action"] != "sell" and after_total:
            o["weight_after"] = to_twd(o["key"], (o["holding_after"] or 0) * (o["price"] or 0), o["market"]) / after_total
    return {"orders": orders, "budget_twd": round(budget), "contribution_twd": round(contribution_twd),
            "proceeds_twd": round(proceeds), "cash_before_twd": round(cash_twd), "cash_after_twd": round(left),
            "value_before_twd": round(sum(cur.values()) + proceeds), "value_after_twd": round(after_total)}
