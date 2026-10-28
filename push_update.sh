#!/usr/bin/env bash
# 把這個資料夾的最新修改推上 GitHub（第一次請先執行 setup_github.sh）
# 推送中斷時直接重跑即可
set -e
cd "$(dirname "$0")"
if [ -d _workflows ]; then mkdir -p .github/workflows && mv -f _workflows/*.yml .github/workflows/ && rmdir _workflows; fi
git add -A
git -c user.name="KalebChen0509" -c user.email="KalebChen0509@users.noreply.github.com" commit -qm "更新 $(date +%Y-%m-%d\ %H:%M)" || echo "沒有新的修改"
# 就算沒有新的修改也推一次：上次推到一半斷線的版本會在這裡補推上去
if git push -q origin main; then
  echo "已推送到 GitHub ✔"
else
  echo "推送失敗（可能是網路中斷）。網路恢復後再執行一次這個指令就好。"
  exit 1
fi
