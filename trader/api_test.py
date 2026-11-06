"""永豐 Shioaji「API 測試」：在模擬環境登入、下一筆測試單，通過後新理財網的「python 測試與否」會變成已測試。

只連模擬環境（simulation=True）：不會真的下單、不會動到你的錢。
金鑰在執行時貼上（畫面不會顯示），不會存成任何檔案；也不要貼給任何人，包括 Claude。
畫面上的帳號只顯示末四碼，可以放心截圖給 Claude 看。

執行（Mac 的「終端機」）：
    ~/.sinopac-venv/bin/python ~/PycharmProjects/stock-screener/trader/api_test.py
可以測試的時間：週一到週五 08:00～18:00（18:00～20:00 只限台灣 IP）。
"""
import getpass
import sys
import time

import inspect
import os
import warnings

warnings.filterwarnings("ignore", category=DeprecationWarning)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sinopac import ask, say, silence  # noqa: E402  先關掉 shioaji 的畫面輸出（連線訊息含身分證字號）

silence()
print = say  # noqa: A001  這支程式自己的訊息照常顯示
try:
    import shioaji as sj
except ImportError:
    sys.exit("還沒安裝 shioaji，請先執行：  ~/.sinopac-venv/bin/pip install -U shioaji")


def _const(name):
    """新版放在 sj.X，舊版在 sj.constant.X。"""
    if hasattr(sj, name):
        return getattr(sj, name)
    from shioaji import constant
    return getattr(constant, name)


Action, OrderType, StockPriceType = _const("Action"), _const("OrderType"), _const("StockPriceType")
FuturesOCType, FuturesPriceType = _const("FuturesOCType"), _const("FuturesPriceType")


def make_order(api, kind, **kw):
    if hasattr(api, "Order"):
        return api.Order(**kw)
    cls = getattr(sj, kind, None) or getattr(sj.order, kind)
    return cls(**kw)


def version_ok(v: str) -> bool:
    try:
        return tuple(int(x) for x in v.split(".")[:2]) >= (1, 2)
    except ValueError:
        return True


def short(acc) -> str:
    kind = {"S": "證券", "F": "期貨", "H": "複委託"}.get(str(getattr(acc, "account_type", "")).split(".")[-1][:1], "")
    return f"{kind or getattr(acc, 'account_type', '')}帳戶（末四碼 {str(getattr(acc, 'account_id', ''))[-4:]}）"


def show(trade, label: str) -> bool:
    st = trade.status
    s = str(getattr(st, "status", "")).split(".")[-1]
    msg = getattr(st, "msg", "") or ""
    ok = s != "Failed"
    print(f"{'✔' if ok else '✘'} {label}：狀態 {s} {msg}")
    return ok


def main():
    print(f"shioaji 版本：{sj.__version__}")
    if not version_ok(sj.__version__):
        sys.exit("版本要 1.2 以上，請執行：  ~/.sinopac-venv/bin/pip install -U shioaji")
    key = getpass.getpass("貼上 API Key（畫面不會顯示，貼上後按 Enter）：").strip()
    secret = getpass.getpass("貼上 Secret Key（畫面不會顯示，貼上後按 Enter）：").strip()
    if not key or not secret:
        sys.exit("沒有輸入金鑰，結束。")

    api = sj.Shioaji(simulation=True)            # 模擬環境：不會真的下單
    params = inspect.signature(api.login).parameters
    extra = {"contracts_timeout": 30000} if "contracts_timeout" in params else {}
    accounts = api.login(api_key=key, secret_key=secret, **extra)
    del key, secret
    print("✔ 登入成功（模擬環境）。看得到的帳戶：" + ("、".join(short(a) for a in accounts) or "（沒有）"))

    ok = True
    # ---- 證券測試單：2890 永豐金，1 張，參考價限價（模擬環境，不是真的單）
    if api.stock_account is None:
        print("✘ 沒有看到證券帳戶：請確認 API 條款已簽署、這把 API Key 有勾「交易」權限。")
        ok = False
    else:
        c = None
        for _ in range(30):
            c = api.Contracts.Stocks["2890"]
            if c is not None:
                break
            time.sleep(1)
        if c is None:
            sys.exit("✘ 讀不到 2890 的商品資料（可能是這把 Key 沒勾「行情／資料」）。截圖給 Claude。")
        order = make_order(api, "StockOrder", price=(c.reference if c and c.reference else 18), quantity=1, action=Action.Buy,
                          price_type=StockPriceType.LMT, order_type=OrderType.ROD, account=api.stock_account)
        trade = api.place_order(c, order)
        time.sleep(2)
        api.update_status(api.stock_account)
        ok &= show(trade, "證券測試單")

    # ---- 期貨測試單（有期貨帳戶才需要；證券和期貨要分開測）
    if api.futopt_account is not None:
        if ask("你有期貨帳戶。要一起測試期貨嗎？只有之後要用 API 做期貨才需要（y/N）：").strip().lower() == "y":
            time.sleep(1.5)                        # 證券和期貨的測試單要間隔 1 秒以上
            fc = min([x for x in api.Contracts.Futures.TXF if x.code[-2:] not in ("R1", "R2")], key=lambda x: x.delivery_date)
            fo = make_order(api, "FuturesOrder", action=Action.Buy, price=(fc.reference or 15000), quantity=1, price_type=FuturesPriceType.LMT,
                           order_type=OrderType.ROD, octype=FuturesOCType.Auto, account=api.futopt_account)
            ft = api.place_order(fc, fo)
            time.sleep(2)
            api.update_status(api.futopt_account)
            ok &= show(ft, "期貨測試單")

    try:
        api.logout()
    except Exception:  # noqa
        pass
    if ok:
        print("\n完成。大約 5 分鐘後，重新整理新理財網「API 管理」頁面，「python 測試與否」應該會變成已測試。")
    else:
        print("\n有一筆沒成功：把這個畫面截圖給 Claude（帳號只顯示末四碼，可以放心截）。")


if __name__ == "__main__":
    main()
