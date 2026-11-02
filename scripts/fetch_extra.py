"""台股籌碼（三大法人、融資融券）與美股分析師預估。只用在網站與通知，不影響選股。"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd  # noqa: E402

from screener import chips, estimates  # noqa: E402
from screener.common import CACHE_DIR, ROOT, SITE_DATA, load_watchlist, log, write_json  # noqa: E402


def us_coverage() -> tuple[list[str], list[str]]:
    """分析師預估要抓的美股：財報快報追蹤的公司（持股、追蹤清單、模擬持股、市值前 100）＋原有持股；
    以及最近 5 天公布財報的（要拿「超乎預期」數字）。"""
    tick = []
    try:
        tick += json.loads((CACHE_DIR / "earnings_watch.json").read_text()).get("us", [])
    except Exception:  # noqa
        pass
    tick += [str(t) for t in (load_watchlist().get("us") or [])]
    try:
        import yaml
        pos = yaml.safe_load((ROOT / "config" / "positions.yaml").read_text(encoding="utf-8")) or {}
        tick += [str(t) for t in (pos.get("us") or {})]
    except Exception:  # noqa
        pass
    recent = []
    try:
        st = json.loads((CACHE_DIR / "earnings_state.json").read_text())
        cut = pd.Timestamp.today().normalize() - pd.Timedelta(days=5)
        recent = [e["id"] for e in st.get("events", []) if e.get("market") == "us" and pd.Timestamp(e.get("date", "2000-01-01")) >= cut]
    except Exception:  # noqa
        pass
    return list(dict.fromkeys(tick)), recent


def main():
    try:
        chips.update()
    except Exception as e:  # noqa
        log.warning("chips update failed: %s", e)
    try:
        write_json(SITE_DATA / "chips_tw.json", chips.summary())
    except Exception as e:  # noqa
        log.warning("chips summary failed: %s", e)
    tick, recent = us_coverage()
    try:
        if tick:
            estimates.update(tick, recent=recent)
    except Exception as e:  # noqa
        log.warning("estimates update failed: %s", e)
    try:
        write_json(SITE_DATA / "estimates_us.json", {"as_of": (estimates.load().get("_meta") or {}).get("updated"),
                                                     "rows": estimates.site_table()})
    except Exception as e:  # noqa
        log.warning("estimates site failed: %s", e)


if __name__ == "__main__":
    main()
