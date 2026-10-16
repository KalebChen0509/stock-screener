#!/usr/bin/env bash
# 把這個資料夾的最新修改推上 GitHub（第一次請先執行 setup_github.sh）
set -e
cd "$(dirname "$0")"
if [ -d _workflows ]; then mkdir -p .github/workflows && mv -f _workflows/*.yml .github/workflows/ && rmdir _workflows; fi
git add -A
git -c user.name="KalebChen0509" -c user.email="KalebChen0509@users.noreply.github.com" commit -qm "更新 $(date +%Y-%m-%d\ %H:%M)" || { echo "沒有新的修改"; exit 0; }
git push -q origin main
echo "已推送到 GitHub ✔"
