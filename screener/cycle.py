"""景氣階段判斷：領先指標「位置 × 方向」，美股再加殖利率曲線與信用利差。

位置：領先指標（已去除長期趨勢，100 = 趨勢）高於或低於 100
方向：近 3 個月的變化 ≥ 0（往上）或 < 0（往下）
    高於 100、往上 → 擴張（expansion）     高於 100、往下 → 趨緩（slowdown）
    低於 100、往上 → 復甦（recovery）      低於 100、往下 → 衰退（recession）
美股另外兩個訊號（只會把階段往後推，不會提早判成復甦）：
    殖利率曲線倒掛（10 年 − 3 個月 < 0）：擴張 → 趨緩
    信用利差 3 個月內擴大 ≥ credit_jump_pp：復甦／擴張 → 趨緩，趨緩 → 衰退
連續 confirm_months 個月是同一個階段才切換，避免來回跳。

資料（point-in-time）：
    台股  國發會「景氣指標與燈號」的領先指標不含趨勢指數（政府資料開放平臺，每月底公布上個月）
    美股  OECD 綜合領先指標（FRED USALOLITOAASTSAM，每月中公布上個月；有 FRED 金鑰時回測用「第一次公布值」）
          殖利率曲線 T10Y3M、信用利差 BAA10Y（市場資料，月底即可取得）
    領先指標延後 lag_months 個月使用（公布時間差）。
注意：台股領先指標每月會回溯修正，沒有當時公布的版本，回測會比實際稍微樂觀。
"""
from __future__ import annotations

import io
import json
import time
import zipfile

import numpy as np
import pandas as pd
import requests

from .common import CACHE_DIR, log

PHASES = ["recovery", "expansion", "slowdown", "recession"]
LABEL = {"recovery": "復甦", "expansion": "擴張", "slowdown": "趨緩", "recession": "衰退"}
LATER = {"recovery": "slowdown", "expansion": "slowdown", "slowdown": "recession", "recession": "recession"}
NDC_API = "https://data.gov.tw/api/v2/rest/dataset/6099"
NDC_CACHE = CACHE_DIR / "tw_ndc_leading.csv"


# ---------------------------------------------------------------- 資料
def fetch_tw_leading(max_age_hours: float = 20.0) -> pd.DataFrame:
    """國發會景氣指標：月底日期 → lead（領先指標不含趨勢指數）、score（燈號分數）、light（燈號）。失敗時用快取。"""
    fresh = NDC_CACHE.exists() and (time.time() - NDC_CACHE.stat().st_mtime) < max_age_hours * 3600
    if not fresh:
        try:
            meta = requests.get(NDC_API, timeout=30).json()
            url = meta["result"]["distribution"][0]["resourceDownloadUrl"]
            r = requests.get(url, timeout=60, headers={"User-Agent": "Mozilla/5.0"})
            r.raise_for_status()
            df = parse_ndc_zip(r.content)
            if len(df) > 100:
                NDC_CACHE.parent.mkdir(parents=True, exist_ok=True)
                df.to_csv(NDC_CACHE)
        except Exception as e:  # noqa
            log.warning("NDC leading index download failed: %s", e)
    if not NDC_CACHE.exists():
        return pd.DataFrame(columns=["lead", "score", "light"])
    return pd.read_csv(NDC_CACHE, index_col=0, parse_dates=True)


def parse_ndc_zip(content: bytes) -> pd.DataFrame:
    """ZIP 裡的「景氣指標與燈號.csv」：Date（yyyymm）、領先指標不含趨勢指數、景氣對策信號綜合分數、景氣對策信號。"""
    z = zipfile.ZipFile(io.BytesIO(content))
    name = next((n for n in z.namelist() if "燈號" in n and not n.startswith("schema")), None)
    if name is None:   # 檔名編碼不同時，找有領先指標欄位的檔案
        for n in z.namelist():
            head = z.read(n)[:400].decode("utf-8-sig", "ignore")
            if "領先指標不含趨勢指數" in head:
                name = n
                break
    raw = z.read(name)
    try:
        txt = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        txt = raw.decode("cp950")
    return parse_ndc_csv(txt)


def parse_ndc_csv(txt: str) -> pd.DataFrame:
    d = pd.read_csv(io.StringIO(txt), dtype=str)
    d.columns = [c.strip().strip('"') for c in d.columns]
    ym = d["Date"].str.replace(r"\D", "", regex=True).str[:6]
    idx = pd.to_datetime(ym, format="%Y%m", errors="coerce") + pd.offsets.MonthEnd(0)
    out = pd.DataFrame({
        "lead": pd.to_numeric(d.get("領先指標不含趨勢指數"), errors="coerce").values,
        "score": pd.to_numeric(d.get("景氣對策信號綜合分數"), errors="coerce").values,
        "light": d.get("景氣對策信號", pd.Series(None, index=d.index)).values,
    }, index=idx)
    out = out[out.index.notna()].sort_index()
    out.index.name = "month"
    return out[out["lead"].notna()]


def fetch_fred_initial(sid: str) -> pd.Series:
    """FRED（ALFRED）「第一次公布值」：回測用，避免用到事後修正的數字。需要 FRED_API_KEY；失敗回傳空值。"""
    import os
    key = os.environ.get("FRED_API_KEY")
    if not key:
        return pd.Series(dtype=float)
    try:
        r = requests.get("https://api.stlouisfed.org/fred/series/observations",
                         params={"series_id": sid, "api_key": key, "file_type": "json", "output_type": 4,
                                 "realtime_start": "1776-07-04", "realtime_end": "9999-12-31"},
                         timeout=(15, 120))
        r.raise_for_status()
        obs = r.json().get("observations", [])
        s = pd.Series({pd.Timestamp(o["date"]): o["value"] for o in obs})
        s = pd.to_numeric(s, errors="coerce").dropna().sort_index()
        s.index = s.index + pd.offsets.MonthEnd(0)
        return s
    except Exception as e:  # noqa
        log.warning("FRED initial release %s failed: %s", sid, e)
        return pd.Series(dtype=float)


def load_inputs(macro: dict | None = None) -> dict:
    """回傳 {"tw": {"lead": 月序列, "ndc": 表}, "us": {"lead", "lead_final", "curve", "spread"}}。"""
    from .macro import fetch_fred, to_monthly
    out = {"tw": {}, "us": {}}
    ndc = fetch_tw_leading()
    out["tw"] = {"lead": ndc["lead"] if len(ndc) else pd.Series(dtype=float), "ndc": ndc}
    final = fetch_fred("USALOLITOAASTSAM")
    if isinstance(final.index, pd.DatetimeIndex) and len(final):
        final.index = final.index + pd.offsets.MonthEnd(0)
    else:
        final = pd.Series(dtype=float)
    init = fetch_fred_initial("USALOLITOAASTSAM")
    # 回測：有第一次公布值就用，沒有的月份用最終值補
    lead = init.combine_first(final) if len(init) else final
    out["us"] = {"lead": lead, "lead_final": final, "n_initial": int(len(init)),
                 "curve": to_monthly(fetch_fred("T10Y3M")),
                 "spread": (macro or {}).get("baa", pd.Series(dtype=float))}
    return out


# ---------------------------------------------------------------- 判斷
def raw_phase(level: float, chg3: float) -> str | None:
    if not (np.isfinite(level) and np.isfinite(chg3)):
        return None
    if level >= 100:
        return "expansion" if chg3 >= 0 else "slowdown"
    return "recovery" if chg3 >= 0 else "recession"


def phase_table(lead: pd.Series, cfg: dict | None = None, curve: pd.Series | None = None,
                spread: pd.Series | None = None) -> pd.DataFrame:
    """每個月底（決策日）可用的階段。lead 的月份 m 在 m + lag_months 的月底才使用。"""
    cfg = cfg or {}
    lag = int(cfg.get("lag_months", 1))
    confirm = int(cfg.get("confirm_months", 2))
    ov = cfg.get("us_overlays") or {}
    lead = pd.Series(lead).dropna().sort_index()
    if len(lead) < 5:
        return pd.DataFrame(columns=["data_month", "level", "chg3", "raw", "signal", "phase"])
    d = pd.DataFrame({"level": lead, "chg3": lead - lead.shift(3)})
    d["data_month"] = d.index
    d.index = d.index + pd.offsets.MonthEnd(lag)          # 決策日
    d["raw"] = pd.Series([raw_phase(a, b) for a, b in zip(d["level"], d["chg3"])], index=d.index, dtype=object)
    d["signal"] = ""
    adj = d["raw"].copy()
    if curve is not None and len(curve) and ov.get("curve_inversion", False):
        cv = pd.Series(curve).dropna()
        c_at = cv.reindex(d.index, method="ffill")
        d["curve"] = c_at
        inv = (c_at < 0) & (adj == "expansion")
        adj[inv] = "slowdown"
        d.loc[inv, "signal"] = "殖利率曲線倒掛"
    if spread is not None and len(spread) and ov.get("credit_jump_pp"):
        sp = pd.Series(spread).dropna()
        s_at = sp.reindex(d.index, method="ffill")
        chg = s_at - sp.reindex(d.index - pd.offsets.MonthEnd(3), method="ffill").values
        d["spread_chg3"] = chg
        jump = chg >= float(ov["credit_jump_pp"])
        jump &= adj.notna()
        adj[jump] = adj[jump].map(LATER)
        adj = adj.astype(object)
        d.loc[jump, "signal"] = (d.loc[jump, "signal"] + "；信用利差急升").str.lstrip("；")
    d["adj"] = adj
    # 連續 confirm 個月相同才切換
    phase, cur, run, cand = [], None, 0, None
    for p in adj:
        if p is None or (isinstance(p, float) and np.isnan(p)):
            phase.append(cur)
            continue
        if cur is None:
            cur = p
        elif p != cur:
            run = run + 1 if p == cand else 1
            cand = p
            if run >= confirm:
                cur, run, cand = p, 0, None
        else:
            run, cand = 0, None
        phase.append(cur)
    d["phase"] = phase
    return d


def phase_at(tbl: pd.DataFrame | None, t, live: bool = False) -> dict | None:
    """決策日 t 當下的階段。回測（live=False）只用決策日 ≤ t 的列；
    每日更新（live=True）資料本來就只含已公布的月份，直接用最新一列。"""
    if tbl is None or len(tbl) == 0:
        return None
    sub = tbl if live else tbl[tbl.index <= pd.Timestamp(t)]
    if not len(sub) or pd.isna(sub["phase"].iloc[-1]):
        return None
    r = sub.iloc[-1]
    raw = r["raw"] if isinstance(r["raw"], str) else None
    return {"phase": r["phase"], "label": LABEL.get(r["phase"], ""), "raw": raw, "level": float(r["level"]),
            "chg3": float(r["chg3"]), "data_month": pd.Timestamp(r["data_month"]).strftime("%Y-%m"),
            "signal": r.get("signal", "") or ""}


def tables(inputs: dict, cfg: dict) -> dict:
    """兩個市場的階段表。"""
    c = cfg.get("cycle") or {}
    out = {}
    tw = (inputs or {}).get("tw") or {}
    out["tw"] = phase_table(tw.get("lead", pd.Series(dtype=float)), {**c, "us_overlays": {}})
    us = (inputs or {}).get("us") or {}
    out["us"] = phase_table(us.get("lead", pd.Series(dtype=float)), c, us.get("curve"), us.get("spread"))
    return out


def apply_phase(cfg: dict, phase: str | None) -> dict:
    """依階段改寫設定：哪一套篩選規則、金融股可否買。cycle 未啟用或沒有階段時原樣回傳。"""
    c = cfg.get("cycle") or {}
    if not c.get("enabled") or not phase:
        return cfg
    rule = (c.get("phase_rules") or {}).get(phase) or {}
    from .common import apply_patch
    return apply_patch(cfg, {"industry_rules": {"enabled": rule.get("rule_set", "old") == "new",
                                                "financials": bool(rule.get("financials", False))}})


def summary(tbl: pd.DataFrame, months: int = 36) -> dict:
    """網站顯示用：最新階段與近 N 個月歷史（不含信用利差原始數值）。"""
    if tbl is None or len(tbl) == 0:
        return {}
    last = phase_at(tbl, tbl.index[-1], live=True)
    hist = [{"m": i.strftime("%Y-%m"), "phase": p if isinstance(p, str) else None, "level": round(float(lv), 2)}
            for i, p, lv in zip(tbl.index[-months:], tbl["phase"].iloc[-months:], tbl["level"].iloc[-months:])]
    return {**(last or {}), "history": hist}


def to_json(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, default=str)
