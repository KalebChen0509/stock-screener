"""階段一：每天讀一次永豐帳戶（只讀，不下單），跟系統建議比對，寄對帳信給你。

- 台股庫存從永豐 API 讀；美股（複委託）API 可能讀不到，讀不到就用 ~/.sinopac/us_holdings.yaml（自己記）。
- 完整明細（股數、成本、損益、交割戶餘額）只存在這台 Mac：~/.sinopac/report.html、~/.sinopac/data/。
- 對帳信只寫「跟系統建議差多少」和停損距離，不寫總金額；內容跟上一封一樣就不寄。
- 由 launchd 每小時叫一次（開機、喚醒後也會跑），收盤（14:30）後每個交易日只同步一次。

手動執行：~/.sinopac-venv/bin/python ~/PycharmProjects/stock-screener/trader/sync.py [--force] [--dry-run]
  --force    不管今天同步過沒有都再跑一次
  --dry-run  只顯示結果，不寄信
"""
from __future__ import annotations

import datetime as dt
import html
import json
import os
import smtplib
import ssl
import sys
import urllib.request
from email.mime.text import MIMEText
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

HOME = Path(os.environ.get("SINOPAC_HOME", Path.home() / ".sinopac"))
DATA = HOME / "data"
SITE = os.environ.get("SITE_URL", "https://kalebchen0509.github.io/stock-screener")
TZ = dt.timezone(dt.timedelta(hours=8))
CLOSE = dt.time(14, 30)
NEAR_STOP = 0.05          # 離停損線 5% 以內就提醒


# ---------------------------------------------------------------- 時間
def last_session(now: dt.datetime | None = None) -> dt.date:
    """最近一個「已經收盤」的交易日（只排除週末；國定假日同步一次也無妨）。"""
    now = (now or dt.datetime.now(TZ)).astimezone(TZ)
    d = now.date()
    if now.time() < CLOSE:
        d -= dt.timedelta(days=1)
    while d.weekday() >= 5:
        d -= dt.timedelta(days=1)
    return d


# ---------------------------------------------------------------- 網路
def _ssl_ctx():
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        try:
            from pip._vendor import certifi  # python.org 版 Python 沒裝憑證時的備案
            return ssl.create_default_context(cafile=certifi.where())
        except ImportError:
            return ssl.create_default_context()


def get_json(path: str):
    req = urllib.request.Request(f"{SITE}/data/{path}", headers={"Cache-Control": "no-cache", "User-Agent": "stock-screener-sync"})
    with urllib.request.urlopen(req, timeout=30, context=_ssl_ctx()) as r:
        return json.loads(r.read().decode("utf-8"))


# ---------------------------------------------------------------- 讀帳戶
def read_account(api, accounts, sj_const) -> dict:
    """回傳 {tw: [{code, name, shares, cost, last}], balance, us_readable, us: [...]}"""
    Unit = sj_const("Unit")
    out = {"tw": [], "balance": None, "us_readable": None, "us": []}
    if api.stock_account is not None:
        for p in api.list_positions(api.stock_account, unit=Unit.Share):
            c = api.Contracts.Stocks[p.code]
            out["tw"].append({"code": p.code, "name": getattr(c, "name", "") if c else "", "shares": int(p.quantity),
                              "cost": float(p.price), "last": float(p.last_price)})
        try:
            b = api.account_balance()
            out["balance"] = float(getattr(b, "acc_balance", None)) if b is not None else None
        except Exception:  # noqa
            pass
    # 複委託：API 不能下單；試試看能不能讀庫存
    h = [a for a in accounts if str(getattr(a, "account_type", "")).split(".")[-1].startswith("H")]
    if h:
        try:
            pos = api.list_positions(h[0])
            out["us_readable"] = True
            for p in pos:
                out["us"].append({"code": p.code, "name": "", "shares": float(p.quantity), "cost": float(p.price), "last": float(p.last_price)})
        except Exception as e:  # noqa
            out["us_readable"] = False
            out["us_error"] = e.__class__.__name__
    return out


def manual_us() -> list[dict]:
    """~/.sinopac/us_holdings.yaml：VT: {shares: 1.234, cost: 130.5}（成本是每股平均，美元）"""
    p = HOME / "us_holdings.yaml"
    if not p.exists():
        return []
    import yaml
    d = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    return [{"code": str(k), "name": "", "shares": float((v or {}).get("shares") or 0), "cost": float((v or {}).get("cost") or 0),
             "last": None, "manual": True} for k, v in d.items()]


# ---------------------------------------------------------------- 比對
def expected_holdings(orders: dict | None) -> dict:
    """系統假設你照清單買之後，每檔應該有幾股：{(市場, 代號): {shares, name}}。台股代號不含 .TW。"""
    out = {}
    for o in (orders or {}).get("orders", []):
        key = o.get("key") or o.get("id")
        mkt = "tw" if str(key).endswith((".TW", ".TWO")) else "us"
        code = str(key).split(".")[0] if mkt == "tw" else str(key)
        out[(mkt, code)] = {"shares": o.get("holding_after") or 0, "name": o.get("name") or "", "kind": o.get("kind")}
    return out


def compare(acct: dict, orders: dict | None, port: dict | None) -> dict:
    exp = expected_holdings(orders)
    have = {("tw", p["code"]): p for p in acct["tw"]}
    have.update({("us", p["code"]): p for p in acct["us"]})
    diffs = []
    for k in sorted(set(exp) | set(have)):
        e = exp.get(k, {}).get("shares") or 0
        h = (have.get(k) or {}).get("shares") or 0
        if abs(h - e) < 1e-6:
            continue
        name = exp.get(k, {}).get("name") or (have.get(k) or {}).get("name") or ""
        unit = "股"
        if k in exp and k not in have:
            what = f"系統建議持有 {e:g} {unit}，你沒有"
        elif k in have and k not in exp:
            what = f"你有 {h:g} {unit}，系統目前沒有建議持有"
        else:
            what = f"你有 {h:g} {unit}，系統建議 {e:g} {unit}（{'多' if h > e else '少'} {abs(h - e):g} {unit}）"
        diffs.append({"market": k[0], "code": k[1], "name": name, "have": h, "expect": e, "text": what})
    pct = float(((port or {}).get("stop_loss") or {}).get("pct", 0.15))
    stops = []
    for p in acct["tw"] + acct["us"]:
        if not p.get("cost") or not p.get("last"):
            continue
        ret = p["last"] / p["cost"] - 1
        dist = ret + pct                     # 離停損線還有多少（負的代表已經跌破）
        if dist <= NEAR_STOP:
            stops.append({"code": p["code"], "name": p.get("name", ""), "ret": ret, "dist": dist,
                          "text": (f"已跌破停損線（以你的成本計 {ret:+.1%}，停損 −{pct:.0%}）" if dist <= 0
                                   else f"接近停損線：以你的成本計 {ret:+.1%}，再跌 {dist:.1%} 就到 −{pct:.0%}")})
    return {"diffs": diffs, "stops": stops, "pct": pct}


# ---------------------------------------------------------------- 輸出
def email_body(day: dt.date, cmp_: dict, acct: dict, orders: dict | None) -> tuple[str, str]:
    n = len(cmp_["diffs"]) + len(cmp_["stops"])
    subj = f"持股對帳 {day:%m/%d}：" + (f"{n} 項要注意" if n else "跟系統一致")
    lines = [f"永豐帳戶 {day} 收盤後的持股，跟系統建議（{(orders or {}).get('month', '')} 下單清單）比對：", ""]
    if cmp_["stops"]:
        lines.append("🛑 停損")
        lines += [f"  • {s['code']} {s['name']}：{s['text']}" for s in cmp_["stops"]]
        lines.append("")
    if cmp_["diffs"]:
        lines.append("📋 跟系統建議不同")
        lines += [f"  • {d['code']} {d['name']}：{d['text']}" for d in cmp_["diffs"]]
        lines.append("")
    if not n:
        lines += ["✔ 持股跟系統建議一致，也沒有接近停損的股票。", ""]
    if acct.get("us_readable") is False:
        lines.append("美股（複委託）永豐 API 讀不到，用的是你在 Mac 上 ~/.sinopac/us_holdings.yaml 記的持股。")
    lines += ["完整明細（股數、成本、損益）只在你的 Mac 上：打開 ~/.sinopac/report.html", "",
              "這封信由你的 Mac 寄出，只讀帳戶、不會下單。內容跟上一封一樣時不會重寄。"]
    return subj, "\n".join(lines)


def report_html(day: dt.date, acct: dict, cmp_: dict) -> str:
    def row(p, cur):
        mv = p["shares"] * (p["last"] or 0)
        pl = (p["last"] - p["cost"]) * p["shares"] if p.get("last") and p.get("cost") else None
        r = p["last"] / p["cost"] - 1 if p.get("last") and p.get("cost") else None
        last = "" if p["last"] is None else f"{p['last']:,.2f}"
        pls = "" if pl is None else f"{pl:+,.0f}"
        rs = "" if r is None else f"{r:+.1%}"
        cls = "pos" if (pl or 0) >= 0 else "neg"
        return (f"<tr><td>{html.escape(p['code'])}</td><td>{html.escape(p.get('name') or '')}</td><td class=n>{p['shares']:,g}</td>"
                f"<td class=n>{p['cost']:,.2f}</td><td class=n>{last}</td>"
                f"<td class=n>{cur}{mv:,.0f}</td><td class='n {cls}'>{pls}</td><td class=n>{rs}</td></tr>")
    head = "<tr><th>代號</th><th>名稱</th><th>股數</th><th>平均成本</th><th>現價</th><th>市值</th><th>損益</th><th>報酬</th></tr>"
    tw = "".join(row(p, "NT$") for p in acct["tw"]) or "<tr><td colspan=8>沒有台股持股</td></tr>"
    us = "".join(row(p, "US$") for p in acct["us"]) or "<tr><td colspan=8>沒有美股持股</td></tr>"
    tw_mv = sum(p["shares"] * (p["last"] or 0) for p in acct["tw"])
    bal = "–" if acct.get("balance") is None else f"NT${acct['balance']:,.0f}"
    notes = "".join(f"<li>{html.escape(x['code'])} {html.escape(x['name'])}：{html.escape(x['text'])}</li>" for x in cmp_["stops"] + cmp_["diffs"])
    return f"""<!doctype html><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>我的持股（私人）</title>
<style>body{{font-family:-apple-system,"PingFang TC",sans-serif;max-width:900px;margin:24px auto;padding:0 16px;color:#1f2430}}
table{{border-collapse:collapse;width:100%;margin:8px 0 20px}}td,th{{border-bottom:1px solid #ddd;padding:6px 8px;text-align:left}}
.n{{text-align:right;font-variant-numeric:tabular-nums}}.pos{{color:#15803d}}.neg{{color:#dc2626}}.hint{{color:#777;font-size:13px}}</style>
<h1>我的持股（私人，只在這台 Mac）</h1>
<p class=hint>永豐帳戶 {day} 收盤後；產生時間 {dt.datetime.now(TZ):%Y-%m-%d %H:%M}。這個檔案不會上傳到任何地方。</p>
<p>台股市值 NT${tw_mv:,.0f}　交割戶餘額 {bal}</p>
<h2>要注意</h2><ul>{notes or '<li>沒有</li>'}</ul>
<h2>台股（永豐）</h2><table>{head}{tw}</table>
<h2>美股{'（複委託，API 讀取）' if acct.get('us_readable') else '（自己記錄：~/.sinopac/us_holdings.yaml）'}</h2><table>{head}{us}</table>"""


def send_mail(subj: str, body: str):
    from sinopac import keychain
    user, pw, to = keychain("smtp_user", "stock-mail"), keychain("smtp_pass", "stock-mail"), keychain("email_to", "stock-mail")
    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"], msg["From"], msg["To"] = subj, user, to
    with smtplib.SMTP("smtp.gmail.com", 587, timeout=30) as s:
        s.starttls(context=_ssl_ctx())
        s.login(user, pw.replace(" ", ""))
        s.sendmail(user, [to], msg.as_string())


# ---------------------------------------------------------------- 主程式
def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    force, dry = "--force" in argv, "--dry-run" in argv
    from sinopac import login, logout, say
    HOME.mkdir(mode=0o700, exist_ok=True)
    DATA.mkdir(mode=0o700, exist_ok=True)
    os.chmod(HOME, 0o700)
    state_p = DATA / "state.json"
    state = json.loads(state_p.read_text()) if state_p.exists() else {}
    day = last_session()
    if not force and state.get("synced") == str(day):
        say(f"{day} 已經同步過了（要重跑請加 --force）")
        return
    try:
        orders, port = get_json("orders.json"), get_json("portfolio.json")
    except Exception as e:  # noqa
        say(f"⚠️ 讀不到網站上的系統建議（{e.__class__.__name__}），先只記錄持股")
        orders = port = None

    import shioaji as sj

    def sj_const(name):
        if hasattr(sj, name):
            return getattr(sj, name)
        from shioaji import constant
        return getattr(constant, name)

    api, accounts = login(simulation=False)
    try:
        acct = read_account(api, accounts, sj_const)
    finally:
        logout(api)
    if not acct["us"]:
        acct["us"] = manual_us()
    cmp_ = compare(acct, orders, port)
    (DATA / f"{day}.json").write_text(json.dumps({"day": str(day), "account": acct, "compare": cmp_}, ensure_ascii=False, indent=1))
    rep = HOME / "report.html"
    rep.write_text(report_html(day, acct, cmp_), encoding="utf-8")
    os.chmod(rep, 0o600)
    subj, body = email_body(day, cmp_, acct, orders)
    say(f"✔ 同步完成：台股 {len(acct['tw'])} 檔、美股 {len(acct['us'])} 檔；{subj}")
    us_msg = {True: "可以", False: "讀不到（改用自己記錄）", None: "沒有複委託帳戶"}[acct["us_readable"]]
    say(f"  複委託 API 讀取：{us_msg}")
    say(f"  私人明細：{rep}")
    digest = json.dumps([cmp_["diffs"], [(s["code"], round(s["dist"], 2)) for s in cmp_["stops"]]], ensure_ascii=False, sort_keys=True)
    if dry:
        say("\n---- 對帳信（--dry-run 不寄）----\n" + subj + "\n\n" + body)
    elif digest != state.get("last_digest") or force:
        send_mail(subj, body)
        state["last_digest"] = digest
        say("✔ 已寄出對帳信")
    else:
        say("內容跟上一封一樣，不寄信")
    state["synced"] = str(day)
    state_p.write_text(json.dumps(state))


if __name__ == "__main__":
    main()
