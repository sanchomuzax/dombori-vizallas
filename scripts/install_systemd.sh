#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET_DIR="$HOME/.config/systemd/user"

echo "Installing dombori systemd units to $TARGET_DIR..."

mkdir -p "$TARGET_DIR"

cp "$SCRIPT_DIR/systemd/"*.service "$TARGET_DIR/"
cp "$SCRIPT_DIR/systemd/"*.timer "$TARGET_DIR/"

systemctl --user daemon-reload

systemctl --user enable --now dombori-collect.timer dombori-hydroinfo.timer dombori-daily.timer

echo ""
echo "Dombori systemd units installed and started:"
echo "  - dombori-collect.timer (every 15 minutes)"
echo "  - dombori-hydroinfo.timer (hourly at :07)"
echo "  - dombori-daily.timer (daily at 03:30)"
echo ""
echo "View status with: systemctl --user status dombori-*.timer"
echo "View logs with: journalctl --user -u dombori-collect.service"
