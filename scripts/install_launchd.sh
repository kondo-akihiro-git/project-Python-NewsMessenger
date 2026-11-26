#!/bin/bash
# launchd エージェントのインストール／アンインストール
# 使い方: ./scripts/install_launchd.sh [install|uninstall|status]
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LABEL="com.aki.news-telegram"
SRC="$PROJECT_ROOT/launchd/$LABEL.plist"
DST="$HOME/Library/LaunchAgents/$LABEL.plist"
UID_NUM="$(id -u)"

case "${1:-install}" in
  install)
    mkdir -p "$HOME/Library/LaunchAgents" "$PROJECT_ROOT/logs" "$PROJECT_ROOT/data"
    cp "$SRC" "$DST"
    launchctl bootout "gui/$UID_NUM/$LABEL" >/dev/null 2>&1 || true
    launchctl bootstrap "gui/$UID_NUM" "$DST"
    launchctl enable "gui/$UID_NUM/$LABEL"
    echo "installed: $DST"
    launchctl print "gui/$UID_NUM/$LABEL" | head -20
    ;;
  uninstall)
    launchctl bootout "gui/$UID_NUM/$LABEL" >/dev/null 2>&1 || true
    rm -f "$DST"
    echo "uninstalled: $DST"
    ;;
  status)
    launchctl print "gui/$UID_NUM/$LABEL" | head -30
    ;;
  *)
    echo "usage: $0 [install|uninstall|status]" >&2
    exit 2
    ;;
esac
