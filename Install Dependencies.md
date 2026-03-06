## Dependencies

### Required
- Python 3
- systemd user services (`systemctl --user`)

### Optional (only needed for profile autoswitch)
- `kdotool` (active window polling on KDE/Wayland)

## Install dependencies

### Ubuntu / Debian
Python 3:
```bash
sudo apt update
sudo apt install -y python3 python3-venv

kdotool (choose one):

Cargo (recommended):

sudo apt install -y cargo
cargo install kdotool
~/.cargo/bin/kdotool --help

Or download a prebuilt kdotool binary from upstream Releases and place it in ~/.local/bin:

mkdir -p ~/.local/bin
install -m 0755 kdotool ~/.local/bin/kdotool
~/.local/bin/kdotool --help


Fedora

Python 3 + kdotool:

sudo dnf install -y python3 kdotool


Arch Linux / Manjaro

Python 3:

sudo pacman -S --needed python

kdotool (AUR):

yay -S kdotool
# or: paru -S kdotool
Verify
python3 --version
kdotool --help

## Input permissions

Synapse Lite reads events from `/dev/input/event*`. On many distros that requires extra permissions.

### Option A (simple): add your user to the `input` group
> Note: some distros intentionally restrict the `input` group. If this doesn’t work or you prefer tighter access, use Option B.

```bash
sudo usermod -aG input "$USER"

Then log out and log back in (or reboot), and verify:

groups | grep -q input && echo "OK: in input group" || echo "Not in input group"
Option B (recommended): udev rule for uinput + input devices

Create a udev rule file:

sudo tee /etc/udev/rules.d/99-synapse-lite.rules >/dev/null <<'EOF'
# Allow access to uinput for user-space virtual devices
KERNEL=="uinput", MODE="0660", GROUP="input", OPTIONS+="static_node=uinput"

# Example: allow read access to all event devices (broad).
# If you want tighter rules, restrict this to your device/vendor.
KERNEL=="event*", SUBSYSTEM=="input", MODE="0660", GROUP="input"
EOF

Reload rules:

sudo udevadm control --reload-rules
sudo udevadm trigger

Then log out/in, and test the mapper again.

Quick troubleshooting

If the mapper still can’t read devices, check permissions:

ls -l /dev/input/event* | head
ls -l /dev/uinput
