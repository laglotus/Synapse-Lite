#!/usr/bin/env python3
"""Synapse Lite profile switcher (optional)

Watches the currently active window classname via kdotool and switches the
active profile in config accordingly, then restarts the mapper.

This is generally optional because the mapper can do internal autoswitch,
but some users prefer running it as a separate service.
"""

import json
import os
import shutil
import time
import subprocess
from typing import Tuple, List

APP_ID = "synapse-lite"

# Legacy IDs/paths kept for migration/upgrade compatibility.
LEGACY_APP_IDS: List[str] = ["razer-mouse-control-center", "naga-synapse-lite"]

CONFIG_PATH = os.path.expanduser(f"~/.config/{APP_ID}/config.json")
LEGACY_CONFIG_PATHS = [os.path.expanduser(f"~/.config/{aid}/config.json") for aid in LEGACY_APP_IDS]

MAPPER_SERVICE = "synapse-lite.service"
LEGACY_MAPPER_SERVICES: List[str] = ["razer-mouse-control-center.service", "naga-synapse-lite.service"]

POLL_SEC = 0.25
RESTART_DEBOUNCE_SEC = 0.8


def run(cmd: List[str]) -> Tuple[int, str]:
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    return p.returncode, (p.stdout or "").strip()


def get_active_classname() -> str:
    code, out = run(["kdotool", "getactivewindow", "getwindowclassname"])
    if code != 0:
        return ""
    return out.strip().lower()


def _seed_from_legacy_if_missing() -> None:
    """If CONFIG_PATH doesn't exist, try to copy in the first legacy config found."""
    if os.path.exists(CONFIG_PATH):
        return

    for legacy in LEGACY_CONFIG_PATHS:
        if os.path.exists(legacy):
            try:
                os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
                shutil.copy2(legacy, CONFIG_PATH)
                return
            except Exception:
                # If copy fails, just keep searching; load_config() will fall back.
                pass


def load_config() -> dict:
    # Prefer new config path; if missing, seed from legacy (legacy remains as backup).
    _seed_from_legacy_if_missing()

    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)

    # Fall back directly to first legacy config that exists.
    for legacy in LEGACY_CONFIG_PATHS:
        if os.path.exists(legacy):
            with open(legacy, "r", encoding="utf-8") as f:
                return json.load(f)

    raise FileNotFoundError("No config found in synapse-lite or legacy locations")


def save_config(cfg: dict) -> None:
    os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
    tmp = CONFIG_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, sort_keys=True)
        f.write("\n")
    os.replace(tmp, CONFIG_PATH)


def restart_mapper() -> Tuple[bool, str]:
    # Prefer the new service name.
    code, out = run(["systemctl", "--user", "restart", MAPPER_SERVICE])
    if code == 0:
        return True, out

    # Try legacy service names for upgraded installs.
    for svc in LEGACY_MAPPER_SERVICES:
        code, out = run(["systemctl", "--user", "restart", svc])
        if code == 0:
            return True, out

    return False, out


def main() -> None:
    print("synapse_lite_profile_switcher: starting (kdotool)")
    last_app = ""
    last_restart = 0.0

    while True:
        app = get_active_classname()
        if app and app != last_app:
            last_app = app

            try:
                cfg = load_config()
            except Exception as e:
                print("synapse_lite_profile_switcher: config read error:", e)
                time.sleep(POLL_SEC)
                continue

            # Support both historical key names.
            amap = (cfg.get("app_profile_map") or cfg.get("app_profiles") or {})
            target = amap.get(app)

            cur = cfg.get("active_profile", "")
            if target and target != cur:
                profiles = cfg.get("profiles") or {}
                if target not in profiles:
                    print(f"synapse_lite_profile_switcher: app '{app}' -> '{target}' but profile not found")
                    time.sleep(POLL_SEC)
                    continue

                cfg["active_profile"] = target
                try:
                    save_config(cfg)
                except Exception as e:
                    print("synapse_lite_profile_switcher: config write error:", e)
                    time.sleep(POLL_SEC)
                    continue

                print(f"synapse_lite_profile_switcher: active app '{app}' -> profile '{target}'")

                now = time.time()
                if now - last_restart >= RESTART_DEBOUNCE_SEC:
                    last_restart = now
                    ok, out = restart_mapper()
                    if ok:
                        print("synapse_lite_profile_switcher: restarted mapper")
                    else:
                        print("synapse_lite_profile_switcher: restart failed:", out)

        time.sleep(POLL_SEC)


if __name__ == "__main__":
    main()
