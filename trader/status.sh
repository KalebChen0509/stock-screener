#!/usr/bin/env bash
# 檢查永豐同步的設定是否都完成（不會顯示任何金鑰或金額）
ok(){ echo "✔ $1"; }; ng(){ echo "✘ $1"; }
[ -x "$HOME/.sinopac-venv/bin/python" ] && ok "shioaji 環境已安裝" || ng "shioaji 環境沒裝"
for a in api_key secret_key; do security find-generic-password -s sinopac-sync -a $a >/dev/null 2>&1 && ok "鑰匙圈：sinopac-sync / $a" || ng "鑰匙圈缺 sinopac-sync / $a"; done
for a in smtp_user smtp_pass email_to; do security find-generic-password -s stock-mail -a $a >/dev/null 2>&1 && ok "鑰匙圈：stock-mail / $a" || ng "鑰匙圈缺 stock-mail / $a"; done
[ -f "$HOME/.sinopac/report.html" ] && ok "私人明細已產生（$(date -r "$HOME/.sinopac/report.html" '+%m/%d %H:%M')）" || ng "還沒跑過 sync.py（沒有 ~/.sinopac/report.html）"
[ -f "$HOME/.sinopac/data/state.json" ] && grep -q last_digest "$HOME/.sinopac/data/state.json" && ok "對帳信寄過了" || ng "還沒真的寄過對帳信（跑 sync.py --force，不加 --dry-run）"
launchctl list 2>/dev/null | grep -q com.stock-screener.sinopac-sync && ok "每天自動同步已開啟" || ng "還沒開啟自動同步（bash trader/install_sync.sh）"
[ -f "$HOME/.sinopac/logs/sync.log" ] && { echo "— 自動同步最後幾行紀錄："; tail -3 "$HOME/.sinopac/logs/sync.log"; }
cd "$(dirname "$0")/.." && git ls-files --error-unmatch shioaji.log >/dev/null 2>&1 && ng "shioaji.log 還在 GitHub 上（執行：git rm --cached shioaji.log）" || ok "shioaji.log 沒有被推上 GitHub"
