#!/usr/bin/env bash
# 安裝「市場事件＋總經指標偵測（Mac 主力）」：launchd 每 2 分鐘叫一次 mac_events.py
#   每 10 分鐘完整檢查；指標公布時段每 2 分鐘檢查總經指標；其他時間不連網、馬上結束。
# 移除：bash scripts/install_mac_events.sh --remove
set -e
LABEL="com.stock-screener.mac-events"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
REPO="$(cd "$(dirname "$0")/.." && pwd)"
PY="$REPO/.venv/bin/python"
LOGDIR="$HOME/.stock-screener/logs"
mkdir -p "$LOGDIR" && chmod 700 "$HOME/.stock-screener"
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
if [ "$1" = "--remove" ]; then rm -f "$PLIST"; echo "已移除 Mac 事件偵測（GitHub 備援照常）"; exit 0; fi
if [ ! -x "$PY" ]; then
  echo "找不到 $PY，請先執行："
  echo "  cd $REPO && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"
  exit 1
fi
mkdir -p "$HOME/Library/LaunchAgents"
cat > "$PLIST" <<PL
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key><array><string>$PY</string><string>$REPO/scripts/mac_events.py</string></array>
  <key>WorkingDirectory</key><string>$REPO</string>
  <key>RunAtLoad</key><true/>
  <key>StartInterval</key><integer>120</integer>
  <key>StandardOutPath</key><string>$LOGDIR/mac_events.log</string>
  <key>StandardErrorPath</key><string>$LOGDIR/mac_events.log</string>
</dict></plist>
PL
launchctl bootstrap "gui/$(id -u)" "$PLIST"
echo "✔ 已安裝：每 10 分鐘完整檢查；週一～五 08:55-10:35、15:55-17:35、20:25-23:05 每 2 分鐘檢查總經指標。"
echo "  紀錄：$LOGDIR/mac_events.log"
