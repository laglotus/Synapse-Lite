LICENSE: GPL-3.0-or-later

# Synapse Lite (Linux)

Synapse Lite is a lightweight Linux replacement for Synapse-style mouse software (Naga-first):
- Layered bindings (Normal / Shift / Ctrl / Alt) with non-interfering precedence
- Macro playback (repeat modes, overlap prevention, stuck-key safety)
- GUI editor for profiles/bindings/macros
- User-level systemd service (no root required)



## Quick install (recommended)

From the project folder:

```bash
chmod +x install.sh
./install.sh
```

This will:
- install files to `~/.local/share/synapse-lite/`
- install systemd user units to `~/.config/systemd/user/`
- enable + start the mapper service: `synapse-lite.service`
- install a desktop launcher: `Synapse Lite`

### Optional: enable external profile switcher (advanced)
By default it is **disabled** (recommended). To enable it:

```bash
./install.sh --enable-switcher
```

## Launch

- From your desktop/app launcher: **Synapse Lite**
- Or from a terminal:

```bash
python3 ~/.local/share/synapse-lite/synapse_lite_gui.py
```

## Service management

```bash
systemctl --user status synapse-lite.service --no-pager
systemctl --user restart synapse-lite.service
systemctl --user stop synapse-lite.service
```

External switcher (optional):

```bash
systemctl --user status synapse-lite-profile-switcher.service --no-pager
systemctl --user enable --now synapse-lite-profile-switcher.service
systemctl --user disable --now synapse-lite-profile-switcher.service
```

## Config locations

Primary config (new path):
- `~/.config/synapse-lite/config.json`

Legacy configs may be migrated/seeded on first run if the new config is missing:
- `~/.config/razer-mouse-control-center/config.json`
- `~/.config/naga-synapse-lite/config.json`

## Troubleshooting

### Buttons don’t remap / you get “double actions”
- Make sure the mapper is running:
  ```bash
  systemctl --user status synapse-lite.service --no-pager
  ```
- Ensure the service uses `--grab` (exclusive grab), so the physical device doesn’t also act normally.

### Permission denied reading `/dev/input/event*`
- Your user typically needs access via the `input` group (varies by distro).
- After adding your user to the right group, log out/in.

### Macros stuck keys / weird state
- Restart the mapper:
  ```bash
  systemctl --user restart synapse-lite.service
  ```

### GUI can’t find images / hotspots
- Ensure `~/.local/share/synapse-lite/assets/` exists and contains:
  - `startpage.png`
  - `mouse_panel_overlays.json`
  - `kblayout_hotspots.json`

## Uninstall

From the project folder:

```bash
chmod +x uninstall.sh
./uninstall.sh
```

This removes:
- `~/.local/share/synapse-lite/`
- user systemd units
- desktop launcher entry

Your config at `~/.config/synapse-lite/` is **left in place** (you can choose to delete it manually).
