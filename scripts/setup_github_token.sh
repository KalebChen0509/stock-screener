#!/usr/bin/env bash
# 重新設定推送 GitHub 用的權杖（舊的到期後，git 會自動從鑰匙圈刪掉 → 推送時一直問 Password）。
# 權杖只存進 macOS 鑰匙圈，不會進 repo、不會印在畫面上。存完馬上推送一次確認。
#   bash scripts/setup_github_token.sh
set -e
cd "$(dirname "$0")/.."
git config credential.helper osxkeychain
echo "請貼上新的 GitHub 權杖（github_pat_ 開頭，貼上時畫面不會顯示），按 Enter："
read -rs GH_PAT; echo
GH_PAT="$(printf '%s' "$GH_PAT" | tr -d '[:space:]')"
case "$GH_PAT" in
  github_pat_*|ghp_*) ;;
  "") echo "✘ 沒有讀到任何內容：剪貼簿可能是空的。請在 GitHub 按權杖旁邊的複製按鈕，再重跑這個指令、按 ⌘V 貼上。沒有存。"; exit 1 ;;
  http*) echo "✘ 貼上的是網址，不是權杖。請複製頁面上 github_pat_ 開頭那一串。沒有存。"; exit 1 ;;
  *) echo "✘ 讀到 ${#GH_PAT} 個字元，但開頭不是 github_pat_（權杖約 93 個字元）。可能複製到別的文字。沒有存。"; exit 1 ;;
esac
printf "protocol=https\nhost=github.com\nusername=x-access-token\n\n" | git credential reject 2>/dev/null || true
printf "protocol=https\nhost=github.com\nusername=x-access-token\npassword=%s\n\n" "$GH_PAT" | git credential approve
unset GH_PAT
echo "✔ 已存進鑰匙圈（長度不顯示）。接著推送…"
bash push_update.sh
