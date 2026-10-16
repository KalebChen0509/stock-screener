#!/usr/bin/env bash
# 一次性設定：把程式推上 GitHub、設定 Secrets、開啟網站、啟動第一次工作。
set -e
cd "$(dirname "$0")"
REPO="KalebChen0509/stock-screener"
command -v git >/dev/null || { echo "需要先安裝 git：在終端機執行 xcode-select --install"; exit 1; }
command -v python3 >/dev/null || { echo "需要 python3"; exit 1; }

echo "請貼上 GitHub token（github_pat_ 開頭，貼上時畫面不會顯示），按 Enter："
read -rs GH_PAT; echo
echo "請貼上 FinMind token（eyJ 開頭），按 Enter："
read -rs FINMIND_TOKEN; echo
export GH_PAT FINMIND_TOKEN GH_REPO="$REPO"

echo "▶ 推送程式到 GitHub…"
if [ -d _workflows ]; then mkdir -p .github/workflows && mv _workflows/*.yml .github/workflows/ && rmdir _workflows; fi
rm -rf .git
git init -q -b main
git add -A
git -c user.name="KalebChen0509" -c user.email="KalebChen0509@users.noreply.github.com" commit -qm "價值選股系統"
git push -q -f "https://x-access-token:${GH_PAT}@github.com/${REPO}.git" main
git remote remove origin 2>/dev/null || true
git remote add origin "https://x-access-token@github.com/${REPO}.git"
git config credential.helper osxkeychain
printf "protocol=https\nhost=github.com\nusername=x-access-token\npassword=%s\n\n" "$GH_PAT" | git credential approve
echo "  推送完成（token 已存進 macOS 鑰匙圈，之後更新只要執行 ./push_update.sh）"

echo "▶ 安裝加密套件（只用在這次設定）…"
VENV="${TMPDIR:-/tmp}/screener-setup-venv"
python3 -m venv "$VENV"
"$VENV/bin/pip" -q install pynacl certifi
echo "▶ 設定 Secrets、Pages、啟動工作…"
"$VENV/bin/python" scripts/gh_setup.py
unset GH_PAT FINMIND_TOKEN
