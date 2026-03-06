#!/usr/bin/env bash
set -euo pipefail

APP_DIR="$HOME/.local/share/synapse-lite"
UNIT_DIR="$HOME/.config/systemd/user"
DESKTOP_FILE="$HOME/.local/share/applications/synapse-lite.desktop"

echo "[1/6] Stopping services (if running)..."
systemctl --user stop synapse-lite.service 2>/dev/null || true
systemctl --user stop synapse-lite-profile-switcher.service 2>/dev/null || true

echo "[2/6] Disabling services (if enabled)..."
systemctl --user disable synapse-lite.service 2>/dev/null || true
systemctl --user disable synapse-lite-profile-switcher.service 2>/dev/null || true

echo "[3/6] Removing systemd unit files..."
rm -f "$UNIT_DIR/synapse-lite.service"
rm -f "$UNIT_DIR/synapse-lite-profile-switcher.service"

echo "[4/6] Reloading systemd user daemon..."
systemctl --user daemon-reload 2>/dev/null || true

echo "[5/6] Removing installed application files..."
rm -rf "$APP_DIR"

echo "[6/6] Removing desktop launcher..."
rm -f "$DESKTOP_FILE"

echo "Done."
echo "Note: config is kept at: $HOME/.config/synapse-lite/"
echo "If you want to delete it too: rm -rf "$HOME/.config/synapse-lite""
