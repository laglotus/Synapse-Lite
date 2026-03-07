LICENSE: GPL-3.0-or-later

# Synapse Lite (Linux)

Synapse Lite is a lightweight, Linux-first replacement for Synapse-style mouse software (Naga-first).  
It provides a **mapper daemon** (evdev → uinput) plus a **GUI editor** for profiles, bindings, and macros.

## Highlights
Synapse Lite is a lightweight, Linux replacement for Synapse-style mouse software (only naga pro v2 officially supported at the moment..  
It runs a **mapper daemon** (evdev → uinput) plus a **GUI editor** for profiles, bindings, and macros.

**Highlights**
- Device detection (Razer Naga V2 Pro tested)
- Layered bindings with non-interfering precedence: **Shift → Ctrl → Alt → Normal**
- Macro engine (repeat/stop modes + overlap prevention + stuck-key safety)
- GUI editor for profiles / bindings / macros
- Per-profile persistence
- Game-safe execution (validated in WoW)
- **Global hotkey support** (ships with `KEY_F24 → cycle_subprofile` by default for “bottom button” setups)
- **Global hotkey support** (for naga pro v2 the bottom button cycles subprofiles by default)

---

  <img src="assets/welcome.png" alt="Synapse Lite GUI" width="800">
  <img src="assets/mouse.png" alt="Synapse Lite GUI" width="800">
  <img src="assets/keyboard.png" alt="Synapse Lite GUI" width="800">
  <img src="assets/macros.png" alt="Synapse Lite GUI" width="800">
  <img src="assets/switcher.png" alt="Synapse Lite GUI" width="800">
  <img src="assets/rgb.png" alt="Synapse Lite GUI" width="800">

---


## Dependencies
### Required
- Python 3
- systemd user services (`systemctl --user`)
- Linux input + uinput access (`/dev/input/event*`, `/dev/uinput`)
- OpenRGB
### Optional
- `kdotool` (only needed for autoswitch / active window polling on KDE/Wayland)

---

# ----Install dependencies----
# ** Ubuntu / Debian **
### Python 3
```bash
sudo apt update
sudo apt install -y python3 python3-venv
```

### kdotool via Cargo
```bash
sudo apt install -y cargo
cargo install kdotool
~/.cargo/bin/kdotool --help
```

### OpenRGB install (recommended: Flatpak, works on most distros)

##### Install Flatpak (if you don’t have it)
```bash
sudo apt update && sudo apt install -y flatpak
```
##### Add Flathub + install OpenRGB
```bash
flatpak remote-add --if-not-exists flathub https://flathub.org/repo/flathub.flatpakrepo
flatpak install -y flathub org.openrgb.OpenRGB
```
##### Run
```bash
flatpak run org.openrgb.OpenRGB
```

---

# ** Fedora **
### Python 3 + Kdotool
```bash
sudo dnf install -y python3 kdotool
```

### OpenRGB install (recommended: Flatpak, works on most distros)

##### Install Flatpak (if you don’t have it)
```bash
sudo dnf install -y flatpak
```
##### Add Flathub + install OpenRGB
```bash
flatpak remote-add --if-not-exists flathub https://flathub.org/repo/flathub.flatpakrepo
flatpak install -y flathub org.openrgb.OpenRGB
```
##### Run
```bash
flatpak run org.openrgb.OpenRGB
```

---

# ** Arch / Manjaro **
### Python 3
```bash
sudo pacman -S --needed python
```

### kdotool via AUR
```bash
yay -S kdotool
```
##### or
```bash
paru -S kdotool
```
### kdotool via Cargo
```bash
sudo pacman -S --needed rust cargo
cargo install kdotool
```

### OpenRGB install (recommended: Flatpak, works on most distros)

##### Install Flatpak (if you don’t have it)
```bash
sudo pacman -S --needed flatpak
```
##### Add Flathub + install OpenRGB
```bash
flatpak remote-add --if-not-exists flathub https://flathub.org/repo/flathub.flatpakrepo
flatpak install -y flathub org.openrgb.OpenRGB
```
##### Run
```bash
flatpak run org.openrgb.OpenRGB
```

---

# ----Input permissions----

### Option A (recommended) — udev rule
```bash
sudo tee /etc/udev/rules.d/99-synapse-lite.rules >/dev/null <<'EOF'
# Allow access to uinput for user-space virtual devices
KERNEL=="uinput", MODE="0660", GROUP="input", OPTIONS+="static_node=uinput"

# Broad rule: allow read access to event devices (all keyboards/mice).
# For tighter rules, restrict by vendor/product for your device(s).
KERNEL=="event*", SUBSYSTEM=="input", MODE="0660", GROUP="input"
EOF

sudo udevadm control --reload-rules
sudo udevadm trigger
```
##### reload udev rules
```bash
sudo udevadm control --reload-rules
sudo udevadm trigger
```
##### Relog or reboot and check 
```bash
groups | grep -q input && echo "OK: in input group" || echo "Not in input group"
```

### Option B — add user to input group
```bash
sudo usermod -aG input "$USER"
```
##### Relog or reboot and check 
```bash
groups | grep -q input && echo "OK: in input group" || echo "Not in input group"
```

---

# ----Install Synapse Lite----
### From the project folder with install.sh file
```bash
chmod +x install.sh
./install.sh
```
### Service status / restart / logs
```bash
systemctl --user status synapse-lite.service --no-pager
systemctl --user restart synapse-lite.service
journalctl --user -u synapse-lite.service -n 120 --no-pager
```
### Launch GUI
```bash
python3 ~/.local/share/synapse-lite/synapse_lite_gui.py
```

# ----Uninstall----
### From the project folder with uninstall.sh file
```bash
chmod +x uninstall.sh
./uninstall.sh
```
### Remove config (optional)
```bash
rm -rf ~/.config/synapse-lite
```
