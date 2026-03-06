#!/usr/bin/env bash
set -euo pipefail

APP_DIR="$HOME/.local/share/synapse-lite"
UNIT_DIR="$HOME/.config/systemd/user"
CFG_DIR="$HOME/.config/synapse-lite"
DESKTOP_DIR="$HOME/.local/share/applications"

ENABLE_SWITCHER=0

for arg in "$@"; do
  case "$arg" in
    --enable-switcher) ENABLE_SWITCHER=1 ;;
    *) echo "Unknown option: $arg" >&2; exit 1 ;;
  esac
done

echo "[1/6] Creating directories..."
mkdir -p "$APP_DIR" "$UNIT_DIR" "$CFG_DIR" "$DESKTOP_DIR"

echo "[1.5/6] Seeding default config (if missing)..."
if [ ! -f "$CFG_DIR/config.json" ]; then
  if [ -f "./default_config.json" ]; then
    cp -f "./default_config.json" "$CFG_DIR/config.json"
  fi
fi


echo "[2/6] Installing python files..."
cp -f ./*.py "$APP_DIR/"

if [ -d "./assets" ]; then
  echo "[3/6] Installing assets..."
  rm -rf "$APP_DIR/assets"
  cp -r "./assets" "$APP_DIR/assets"
else
  echo "[3/6] No ./assets directory found (GUI may be missing images/hotspots)."
fi

echo "[4/6] Installing systemd user units..."

# Support both layouts:
#   - ./systemd/*.service (recommended)
#   - ./*.service (current folder)
UNIT_SRC_DIR="./systemd"
if [ ! -d "$UNIT_SRC_DIR" ]; then
  UNIT_SRC_DIR="."
fi

if [ ! -f "$UNIT_SRC_DIR/synapse-lite.service" ]; then
  echo "ERROR: synapse-lite.service not found in ./systemd or project root." >&2
  exit 1
fi

cp -f "$UNIT_SRC_DIR/synapse-lite.service" "$UNIT_DIR/"

if [ -f "$UNIT_SRC_DIR/synapse-lite-profile-switcher.service" ]; then
  cp -f "$UNIT_SRC_DIR/synapse-lite-profile-switcher.service" "$UNIT_DIR/"
else
  echo "Note: synapse-lite-profile-switcher.service not found (that's fine)."
fi

echo "[5/6] systemd reload + enable mapper..."
systemctl --user daemon-reload
systemctl --user enable --now synapse-lite.service

if [ "$ENABLE_SWITCHER" -eq 1 ] && [ -f "$UNIT_DIR/synapse-lite-profile-switcher.service" ]; then
  echo "Enabling external profile switcher..."
  systemctl --user enable --now synapse-lite-profile-switcher.service
else
  echo "External profile switcher left disabled (recommended baseline)."
fi

echo "[6/6] Installing desktop launcher..."
cat > "$DESKTOP_DIR/synapse-lite.desktop" <<'EOF'
[Desktop Entry]
Type=Application
Name=Synapse Lite
Exec=python3 %h/.local/share/synapse-lite/synapse_lite_gui.py
Icon=utilities-terminal
Categories=Utility;
Terminal=false
EOF

echo "Done."
echo "Mapper is running: systemctl --user status synapse-lite.service --no-pager"
echo "Launch GUI from app menu: Synapse Lite (or run: python3 ~/.local/share/synapse-lite/synapse_lite_gui.py)"
