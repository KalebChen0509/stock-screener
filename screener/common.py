"""共用工具：設定檔、路徑、記錄。"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = ROOT / "config"
CACHE_DIR = Path(os.environ.get("SCREENER_CACHE", ROOT / "data" / "cache"))
SITE_DATA = ROOT / "site" / "data"

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("screener")


def _numify(o):
    """PyYAML 會把 3.0e9 這種寫法讀成字串，這裡轉回數字。"""
    if isinstance(o, dict):
        return {k: _numify(v) for k, v in o.items()}
    if isinstance(o, list):
        return [_numify(v) for v in o]
    if isinstance(o, str):
        import re
        if re.fullmatch(r"[-+]?\d+(\.\d+)?[eE][-+]?\d+", o.strip()):
            return float(o)
    return o


def load_strategy() -> dict:
    with open(CONFIG_DIR / "strategy.yaml", encoding="utf-8") as f:
        return _numify(yaml.safe_load(f))


def load_etf_universe() -> list[dict]:
    with open(CONFIG_DIR / "etf_universe.yaml", encoding="utf-8") as f:
        return _numify(yaml.safe_load(f))["etfs"]


def load_watchlist() -> dict:
    p = CONFIG_DIR / "watchlist.yaml"
    if not p.exists():
        return {"tw": [], "us": [], "etf": []}
    with open(p, encoding="utf-8") as f:
        d = yaml.safe_load(f) or {}
    return {k: [str(x) for x in (d.get(k) or [])] for k in ("tw", "us", "etf")}


def ensure_dir(p: Path) -> Path:
    p.mkdir(parents=True, exist_ok=True)
    return p


class NpEncoder(json.JSONEncoder):
    def default(self, o):
        if isinstance(o, (np.integer,)):
            return int(o)
        if isinstance(o, (np.floating,)):
            return None if not np.isfinite(o) else float(o)
        if isinstance(o, (np.bool_,)):
            return bool(o)
        if isinstance(o, (pd.Timestamp,)):
            return o.strftime("%Y-%m-%d")
        if isinstance(o, np.ndarray):
            return o.tolist()
        return super().default(o)


def _clean(o):
    """把 NaN/inf 轉成 None，讓 JSON 合法。"""
    if isinstance(o, float):
        return o if np.isfinite(o) else None
    if isinstance(o, dict):
        return {k: _clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_clean(v) for v in o]
    return o


def write_json(path: Path, obj, compact: bool = True) -> None:
    ensure_dir(path.parent)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(_clean(obj), f, cls=NpEncoder, ensure_ascii=False,
                  separators=(",", ":") if compact else None,
                  indent=None if compact else 2)


def month_end(ts) -> pd.Timestamp:
    return (pd.Timestamp(ts) + pd.offsets.MonthEnd(0)).normalize()
