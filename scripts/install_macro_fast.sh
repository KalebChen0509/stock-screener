#!/usr/bin/env bash
# 安裝「總經指標快速偵測」：launchd 每 2 分鐘叫一次 macro_fast.py；不在公布時段時程式馬上結束、不連網。
# 移除：bash scripts/install_macro_fast.sh --remove
set -e
LABEL="com.stock-screener.macro-fast"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
REPO="$(cd "$(dirname "$0")/.." && pwd)"
PY="$REPO/.venv/bin/python"
LOGDIR="$HOME/.stock-screener/logs"
mkdir -p "$LOGDIR" && chmod 700 "$HOME/.stock-screener"
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
if [ "$1" = "--remove" ]; then rm -f "$PLIST"; echo "已移除總經指標快速偵測"; exit 0; fi
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
  <key>ProgramArguments</key><array><string>$PY</string><string>$REPO/scripts/macro_fast.py</string></array>
  <key>WorkingDirectory</key><string>$REPO</string>
  <key>StartInterval</key><integer>120</integer>
  <key>StandardOutPath</key><string>$LOGDIR/macro_fast.log</string>
  <key>StandardErrorPath</key><string>$LOGDIR/macro_fast.log</string>
</dict></plist>
PL
launchctl bootstrap "gui/$(id -u)" "$PLIST"
echo "✔ 已安裝：週一～五 08:55-10:35、15:55-17:35、20:25-23:05 每 2 分鐘檢查轉折訊號。紀錄在 $LOGDIR/macro_fast.log"
