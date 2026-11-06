"""階段二：把系統的月下單清單送到永豐「模擬環境」（不會動到真錢）。

流程：讀網站上的下單清單 → 列出台股要買的每一筆 → 你輸入 y 才送出 → 顯示委託狀態 → 寄結果信（含美股要手動下的部分）。
- 只送台股（含 0050 等台股 ETF）；美股／VT 永豐 API 不能下複委託，信裡提醒你自己下。
- 價格：昨收（參考價）+1%，對齊台股跳動單位，不超過漲停。盤後／盤前送單改用最新收盤價當參考價。
- 1000 股以上拆成整股（張）＋零股；零股用盤中零股。
- 用鑰匙圈「sinopac-sim」那把 Key（沒勾正式環境，只能模擬）。

執行（建議在台股盤中 09:00～13:30）：
  ~/.sinopac-venv/bin/python ~/PycharmProjects/stock-screener/trader/sim_order.py
  ~/.sinopac-venv/bin/python ~/PycharmProjects/stock-screener/trader/sim_order.py --check   # 查今天送過的模擬單有沒有成交
"""
from __future__ import annotations

import datetime as dt
import json
import math
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from sync import HOME, TZ, get_json  # noqa: E402

SIM_DIR = HOME / "sim"
SERVICE = "sinopac-sim"
MARKUP = 0.01            # 昨收 +1%


# ---------------------------------------------------------------- 價格
def tick(price: float) -> float:
    """台股股票跳動單位。"""
    for lim, t in ((10, 0.01), (50, 0.05), (100, 0.1), (500, 0.5), (1000, 1.0)):
        if price < lim:
            return t
    return 5.0


def etf_tick(price: float) -> float:
    """ETF 跳動單位（50 元以下 0.01，以上 0.05）。"""
    return 0.01 if price < 50 else 0.05


def limit_price(ref: float, limit_up: float | None = None, etf: bool = False) -> float:
    """昨收 +1%，往上對齊跳動單位，不超過漲停。"""
    raw = ref * (1 + MARKUP)
    t = (etf_tick if etf else tick)(raw)
    p = math.ceil(round(raw / t, 6)) * t
    if limit_up:
        p = min(p, limit_up)
    return round(p, 2)


def next_limit_up(ref: float, etf: bool = False) -> float:
    """下一盤的漲停價：參考價 +10%，往下對齊跳動單位。"""
    raw = ref * 1.10
    t = (etf_tick if etf else tick)(raw)
    return round(math.floor(round(raw / t, 6)) * t, 2)


def latest_close(api, codes: list[str]) -> dict:
    """最新收盤價（快照）。抓不到就回傳空的，呼叫端改用商品檔的參考價。"""
    try:
        cs = [api.Contracts.Stocks[c] for c in codes]
        snaps = api.snapshots([c for c in cs if c is not None])
        return {s.code: float(s.close) for s in snaps if getattr(s, "close", 0)}
    except Exception:  # noqa
        return {}


# ---------------------------------------------------------------- 清單
def plan(orders: dict) -> tuple[list[dict], list[dict]]:
    """回傳 (台股要送的單, 美股要手動下的)。"""
    tw, us = [], []
    for o in (orders or {}).get("orders", []):
        if o.get("action") != "buy" or not o.get("shares"):
            continue
        key = str(o.get("key") or o.get("id"))
        if key.endswith((".TW", ".TWO")):
            sh = int(o["shares"])
            if sh <= 0:
                continue
            tw.append({"code": key.split(".")[0], "name": o.get("name") or "", "shares": sh, "ref_hint": o.get("price"),
                       "etf": o.get("kind") in ("core", "fill", "etf") or key.split(".")[0].startswith("00")})
        else:
            us.append({"code": key, "name": o.get("name") or "", "shares": o["shares"], "price": o.get("price"),
                       "usd": round(float(o["shares"]) * float(o.get("price") or 0), 2), "reason": o.get("reason") or ""})
    return tw, us


def split_lots(shares: int) -> list[tuple[str, int]]:
    """[('Common', 張數), ('IntradayOdd', 股數)]。"""
    out = []
    if shares >= 1000:
        out.append(("Common", shares // 1000))
    if shares % 1000:
        out.append(("IntradayOdd", shares % 1000))
    return out


def in_session(now: dt.datetime | None = None) -> bool:
    now = (now or dt.datetime.now(TZ)).astimezone(TZ)
    return now.weekday() < 5 and dt.time(9, 0) <= now.time() <= dt.time(13, 30)


# ---------------------------------------------------------------- 主程式
def place(argv=None):
    from sinopac import login, logout, say
    orders = get_json("orders.json")
    tw, us = plan(orders)
    month = orders.get("month", "")
    if not tw:
        say(f"{month} 下單清單裡沒有台股要買的。")
    import shioaji as sj

    def C(name):
        if hasattr(sj, name):
            return getattr(sj, name)
        from shioaji import constant
        return getattr(constant, name)

    api, accounts = login(simulation=True, service=SERVICE)
    try:
        if api.stock_account is None:
            sys.exit("模擬環境沒有證券帳戶：確認 sinopac-sim 這把 Key 有勾「交易」、帳戶有勾證券帳戶。")
        rows = []
        # 盤後／盤前送單：商品檔的參考價可能還是「今天這一盤」的（＝前一天收盤），
        # 下一盤真正的參考價是最新收盤價 → 改用快照的收盤價，漲停也自己算（參考價 +10%，往下對齊跳動單位）。
        # （11/05 收盤後送單，3406 用了 11/04 的收盤價，11/05 跌停後就超過 11/06 的漲停，被退單）
        latest = {} if in_session() else latest_close(api, [x["code"] for x in tw])
        for x in tw:
            c = api.Contracts.Stocks[x["code"]]
            if c is None:
                say(f"⚠️ 找不到 {x['code']} 的商品資料，略過")
                continue
            if latest.get(x["code"]):
                ref = latest[x["code"]]
                up = next_limit_up(ref, x["etf"])
            else:
                ref = float(c.reference or x["ref_hint"] or 0)
                up = float(c.limit_up) if getattr(c, "limit_up", None) else None
            px = limit_price(ref, up, x["etf"])
            for lot, qty in split_lots(x["shares"]):
                n_sh = qty * (1000 if lot == "Common" else 1)
                rows.append({**x, "name": getattr(c, "name", "") or x["name"], "lot": lot, "qty": qty, "ref": ref, "price": px,
                             "amount": round(px * n_sh), "contract": c})
        say(f"\n==== {month} 下單清單 → 永豐「模擬環境」（不會動到真錢）====")
        say(f"{'代號':<6}{'名稱':<10}{'數量':>10}{'昨收':>9}{'限價':>9}{'金額':>9}  盤別")
        for r in rows:
            q = f"{r['qty']} 張" if r["lot"] == "Common" else f"{r['qty']} 股"
            say(f"{r['code']:<6}{r['name'][:6]:<10}{q:>10}{r['ref']:>9.2f}{r['price']:>9.2f}{r['amount']:>9,}  {'整股' if r['lot'] == 'Common' else '盤中零股'}")
        total = sum(r["amount"] for r in rows)
        say(f"合計約 NT${total:,}（限價是昨收 +1%，實際成交可能更低；手續費另計）")
        if us:
            say("\n美股（API 不能下，請到豐存美股或永豐 App 自己下）：")
            for u in us:
                say(f"  {u['code']} {u['name']}：{u['shares']:g} 股，約 US${u['usd']:,.2f}（{u['reason']}）")
        if not rows:
            return
        if not in_session():
            say("\n⚠️ 現在不是台股盤中（週一到週五 09:00～13:30）：盤中零股的模擬單可能被退回。建議盤中再跑。")
        # 防止同一個月的清單重複下單（之後真單也會用同一套檢查）
        sent = []
        for f in sorted(SIM_DIR.glob("*.json")) if SIM_DIR.exists() else []:
            try:
                d = json.loads(f.read_text())
                if d.get("month") == month and any(x["status"] not in ("Failed", "Error", "Cancelled") for x in d.get("orders", [])):
                    sent.append(f.stem.replace("_", " "))
            except Exception:  # noqa
                pass
        need = "y"
        if sent:
            say(f"\n⚠️ {month} 的清單已經送過 {len(sent)} 次（{'、'.join(sent)}）。再送一次會重複下單。")
            need = "again"
        ans = input(f"\n要把上面的台股送到「模擬環境」嗎？輸入 {need} 送出，其他鍵取消：").strip().lower()
        if ans != need:
            say("已取消，沒有送出任何單。")
            return
        Lot = C("StockOrderLot")
        results = []
        for r in rows:
            order = api.Order(price=r["price"], quantity=r["qty"], action=C("Action").Buy, price_type=C("StockPriceType").LMT,
                              order_type=C("OrderType").ROD, order_lot=getattr(Lot, r["lot"]), account=api.stock_account)
            try:
                t = api.place_order(r["contract"], order)
                results.append((r, t, None))
            except Exception as e:  # noqa
                results.append((r, None, f"{e.__class__.__name__}: {str(e)[:120]}"))
            time.sleep(0.3)
        time.sleep(2)
        try:
            api.update_status(api.stock_account)
        except Exception:  # noqa
            pass
        say("\n==== 委託結果（模擬）====")
        log = []
        for r, t, err in results:
            if err:
                st, msg = "Error", err
            else:
                st = str(getattr(t.status, "status", "")).split(".")[-1]
                msg = getattr(t.status, "msg", "") or ""
            mark = "✘" if st in ("Failed", "Error", "Cancelled") else "✔"
            say(f"{mark} {r['code']} {r['name'][:6]} {r['qty']}{'張' if r['lot'] == 'Common' else '股'} @ {r['price']}：{st} {msg}")
            log.append({"code": r["code"], "name": r["name"], "lot": r["lot"], "qty": r["qty"], "price": r["price"],
                        "amount": r["amount"], "status": st, "msg": msg,
                        "order_id": getattr(getattr(t, "order", None), "id", None) if t else None})
    finally:
        logout(api)
    SIM_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    now = dt.datetime.now(TZ)
    (SIM_DIR / f"{now:%Y-%m-%d_%H%M}.json").write_text(json.dumps({"month": month, "time": str(now), "orders": log, "us_manual": us},
                                                                  ensure_ascii=False, indent=1))
    ok = sum(1 for x in log if x["status"] not in ("Failed", "Error", "Cancelled"))
    body = [f"{month} 下單清單已送到永豐「模擬環境」（不是真的單）：{ok}/{len(log)} 筆委託成功送出。", ""]
    body += [f"  {'✔' if x['status'] not in ('Failed', 'Error', 'Cancelled') else '✘'} {x['code']} {x['name']}：{x['qty']}"
             f"{'張' if x['lot'] == 'Common' else '股'} @ {x['price']}（{x['status']} {x['msg']}）" for x in log]
    if us:
        body += ["", "美股要你自己下（永豐 API 不能下複委託）："]
        body += [f"  • {u['code']} {u['name']}：{u['shares']:g} 股，約 US${u['usd']:,.2f}" for u in us]
    body += ["", "收盤後可以查成交：sim_order.py --check", "這是模擬，不會動到你的錢。"]
    try:
        from sync import send_mail
        send_mail(f"模擬下單 {now:%m/%d}：{ok}/{len(log)} 筆送出", "\n".join(body))
        say("\n✔ 已寄出結果信")
    except SystemExit as e:
        say(f"\n⚠️ 結果信沒寄出：{e}")


def check():
    from sinopac import login, logout, say
    api, _ = login(simulation=True, service=SERVICE)
    try:
        api.update_status(api.stock_account)
        trades = api.list_trades()
        if not trades:
            say("模擬環境今天查不到委託（模擬單通常只保留當天）。")
        for t in trades:
            st = str(getattr(t.status, "status", "")).split(".")[-1]
            deals = getattr(t.status, "deals", None) or []
            filled = sum(getattr(d, "quantity", 0) for d in deals)
            say(f"{t.contract.code} {getattr(t.contract, 'name', '')}：{st}，成交 {filled}／委託 {t.order.quantity}"
                f"{'張' if 'Common' in str(t.order.order_lot) else '股'} @ {t.order.price}")
    finally:
        logout(api)


if __name__ == "__main__":
    if "--check" in sys.argv[1:]:
        check()
    else:
        place()
