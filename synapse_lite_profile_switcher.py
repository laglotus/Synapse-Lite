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
    payload = _prepare_config_for_save(cfg)
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, sort_keys=True)
        f.write("\n")
    os.replace(tmp, CONFIG_PATH)


def _resolve_autoswitch_block(cfg: dict) -> dict:
    auto = cfg.get("autoswitch")
    if not isinstance(auto, dict):
        auto = {}
    enabled = auto.get("enabled", cfg.get("auto_switch_enabled", True))
    app_profiles = auto.get("app_profiles", cfg.get("app_profile_map") or cfg.get("app_profiles") or {})
    fallback = auto.get("fallback_profile", cfg.get("fallback_profile", cfg.get("default_profile", "default")))
    if not isinstance(app_profiles, dict):
        app_profiles = {}
    return {
        "enabled": bool(enabled),
        "app_profiles": {str(k).strip().lower(): v for k, v in app_profiles.items()},
        "fallback_profile": str(fallback or "default"),
    }


def _resolve_target_profile(cfg: dict, target: str) -> str:
    profiles = cfg.get("profiles") or {}
    if not isinstance(profiles, dict):
        return str(target or "default")
    base = str(target or "default")
    if base not in profiles:
        return base
    subs = []
    for name, pdata in profiles.items():
        if not isinstance(pdata, dict):
            continue
        settings = pdata.get("settings") or {}
        if isinstance(settings, dict) and str(settings.get("subprofile_of") or "") == base:
            subs.append(str(name))
    if not subs:
        return base
    subs = sorted(subs, key=lambda s: s.casefold())
    last = cfg.get("last_subprofiles") or {}
    if isinstance(last, dict):
        cand = last.get(base)
        if isinstance(cand, str) and cand in subs:
            return cand
    cur = str(cfg.get("active_profile", "") or "")
    if cur in subs:
        return cur
    return subs[0]


def _prepare_config_for_save(cfg: dict) -> dict:
    out = dict(cfg if isinstance(cfg, dict) else {})
    out["autoswitch"] = _resolve_autoswitch_block(out)
    out.pop("auto_switch_enabled", None)
    out.pop("app_profiles", None)
    out.pop("app_profile_map", None)
    out.pop("fallback_profile", None)
    return out


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

            auto = _resolve_autoswitch_block(cfg)
            if not auto.get("enabled", True):
                time.sleep(POLL_SEC)
                continue

            target = auto.get("app_profiles", {}).get(app)
            if target is None:
                target = auto.get("fallback_profile", "default")
            target = _resolve_target_profile(cfg, str(target or "default"))

            cur = str(cfg.get("active_profile", "") or "")
            if target and target != cur:
                profiles = cfg.get("profiles") or {}
                if target not in profiles:
                    print(f"synapse_lite_profile_switcher: app '{app}' -> '{target}' but profile not found")
                    time.sleep(POLL_SEC)
                    continue

                cfg["active_profile"] = target
                try:
                    base = ((cfg.get("profiles") or {}).get(target, {}).get("settings") or {}).get("subprofile_of")
                    if base:
                        cfg.setdefault("last_subprofiles", {})[str(base)] = target
                except Exception:
                    pass
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
