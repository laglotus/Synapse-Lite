LICENSE: GPL-3.0-or-later

# Synapse Lite (Linux)

Synapse Lite is a lightweight, Linux-first replacement for Synapse-style mouse software (Naga-first).  
It provides a **mapper daemon** (evdev → uinput) plus a **GUI editor** for profiles, bindings, and macros.

## Highlights

- Device detection (Razer Naga V2 Pro tested)
- Layered bindings with non-interfering precedence: **Shift → Ctrl → Alt → Normal**
- Macro engine (repeat/stop modes + overlap prevention + stuck-key safety)
- GUI editor for profiles / bindings / macros
- Per-profile persistence
- Game-safe execution (validated in WoW)
- **Global hotkey support** (ships with `KEY_F24 → cycle_subprofile` by default for “bottom button” setups)

---

## Dependencies

### Required (runtime)
- **Python 3**
- **systemd user services** (`systemctl --user`)
- Linux input + uinput access (`/dev/input/event*`, `/dev/uinput`)

### Optional
- `kdotool` — only needed if you use autoswitch (active window polling on KDE/Wayland)

---

## Install dependencies (Ubuntu / Fedora / Arch)

### Ubuntu / Debian

**Python 3**
```bash
sudo apt update
sudo apt install -y python3 python3-venv
