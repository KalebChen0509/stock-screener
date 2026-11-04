#!/usr/bin/env bash
# 安裝「每天自動同步永豐持股」：launchd 每小時叫一次 sync.py（開機、喚醒後也會跑），收盤後每個交易日只真正同步一次。
# 移除：bash install_sync.sh --remove
set -e
LABEL="com.stock-screener.sinopac-sync"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
DIR="$(cd "$(dirname "$0")" && pwd)"
PY="$HOME/.sinopac-venv/bin/python"
mkdir -p "$HOME/.sinopac/logs" && chmod 700 "$HOME/.sinopac"
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
if [ "$1" = "--remove" ]; then rm -f "$PLIST"; echo "已移除自動同步"; exit 0; fi
mkdir -p "$HOME/Library/LaunchAgents"
cat > "$PLIST" <<PL
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key><array><string>$PY</string><string>$DIR/sync.py</string></array>
  <key>RunAtLoad</key><true/>
  <key>StartInterval</key><integer>3600</integer>
  <key>StandardOutPath</key><string>$HOME/.sinopac/logs/sync.log</string>
  <key>StandardErrorPath</key><string>$HOME/.sinopac/logs/sync.log</string>
</dict></plist>
PL
launchctl bootstrap "gui/$(id -u)" "$PLIST"
echo "✔ 已安裝：每小時檢查一次，收盤後每個交易日同步一次並寄對帳信。紀錄在 ~/.sinopac/logs/sync.log"
