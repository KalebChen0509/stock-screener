#!/usr/bin/env bash
# 程式已推上 GitHub 後，只做：設定 Secrets、開啟網站、啟動第一次工作
set -e
cd "$(dirname "$0")"
echo "請貼上 GitHub token（github_pat_ 開頭，畫面不會顯示），按 Enter："
read -rs GH_PAT; echo
echo "請貼上 FinMind token（eyJ 開頭），按 Enter："
read -rs FINMIND_TOKEN; echo
export GH_PAT FINMIND_TOKEN GH_REPO="KalebChen0509/stock-screener"
VENV="${TMPDIR:-/tmp}/screener-setup-venv"
[ -x "$VENV/bin/python" ] || python3 -m venv "$VENV"
"$VENV/bin/pip" -q install pynacl certifi
"$VENV/bin/python" scripts/gh_setup.py
