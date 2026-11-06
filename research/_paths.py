"""研究程式共用的路徑：從專案根目錄執行，資料用 data 分支的快取。

    git fetch origin data
    mkdir -p /tmp/databr && git archive origin/data | tar -x -C /tmp/databr
    SCREENER_CACHE=/tmp/databr/cache .venv/bin/python research/factor_data.py

SCREENER_CACHE：快取資料夾（data 分支的 cache/）；沒設定就用 data/cache。
RESEARCH_OUT：中間結果與輸出（預設 research/out/，不上傳 GitHub）。
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
CACHE = os.environ.get("SCREENER_CACHE", str(ROOT / "data" / "cache"))
OUT = Path(os.environ.get("RESEARCH_OUT", ROOT / "research" / "out"))
OUT.mkdir(parents=True, exist_ok=True)
