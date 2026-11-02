#!/usr/bin/env bash
# 把這個資料夾的最新修改推上 GitHub（第一次請先執行 setup_github.sh）
# 推送中斷時直接重跑即可
set -e
cd "$(dirname "$0")"
if [ -d _workflows ]; then mkdir -p .github/workflows && mv -f _workflows/*.yml .github/workflows/ && rmdir _workflows; fi
git add -A

# ---- 安全檢查：金鑰、憑證、身分證字號絕對不能推上 GitHub（這個 repo 是公開的）
bad_files=$(git diff --cached --name-only | grep -Ei '\.(pfx|p12|pem|key|crt|cer)$|(^|/)\.env($|\.)|(^|/)trader/(secrets|private)/' || true)
bad_lines=$(git diff --cached -U0 | grep -E '^\+' | grep -Ev '^\+\+\+' \
  | grep -E '(SJ_|SINOPAC_|SHIOAJI_)?(API_KEY|SECRET_KEY|CA_PASSWD|CA_PASSWORD|PERSON_ID)[[:space:]]*[:=][[:space:]]*["'"'"']?[A-Za-z0-9+/=_-]{6,}|(^|[^A-Za-z0-9])[A-Z][12][0-9]{8}([^0-9]|$)' || true)
if [ -n "$bad_files$bad_lines" ]; then
  git reset -q
  echo "⛔ 已停止推送：發現疑似金鑰、憑證或身分證字號，這些只能留在你的 Mac，不能放上公開的 GitHub。"
  [ -n "$bad_files" ] && echo "  檔案：" && echo "$bad_files" | sed 's/^/    /'
  [ -n "$bad_lines" ] && echo "  內容（只顯示前 40 個字）：" && echo "$bad_lines" | cut -c1-40 | sed 's/^/    /'
  echo "  請把它移出這個資料夾（建議放 ~/.sinopac/），或加進 .gitignore 後再執行一次。"
  exit 1
fi

git -c user.name="KalebChen0509" -c user.email="KalebChen0509@users.noreply.github.com" commit -qm "更新 $(date +%Y-%m-%d\ %H:%M)" || echo "沒有新的修改"
# 你在 GitHub 網頁上改過的設定（例如網站「🔔 提醒」貼上的價位）先合併進來，再推送
if ! git -c user.name="KalebChen0509" -c user.email="KalebChen0509@users.noreply.github.com" pull -q --rebase origin main; then
  if [ -d .git/rebase-merge ] || [ -d .git/rebase-apply ]; then
    git rebase --abort 2>/dev/null || true
    echo "GitHub 網頁上的修改和這台電腦的修改衝突（同一個檔案的同一段都改了）。請把上面的訊息貼給 Claude 處理。"
  else
    echo "連不上 GitHub（可能是網路中斷）。網路恢復後再執行一次這個指令就好。"
  fi
  exit 1
fi
# 就算沒有新的修改也推一次：上次推到一半斷線的版本會在這裡補推上去
if git push -q origin main; then
  echo "已推送到 GitHub ✔"
else
  echo "推送失敗（可能是網路中斷）。網路恢復後再執行一次這個指令就好。"
  exit 1
fi
