#!/usr/bin/env python3
"""
naga_proxy_mapper_rgb_idle_v9.py

Adds a non-blocking, two-stage RGB idle manager:

Stage 1 (DIM): after dim_after_seconds, set devices to the current profile color but dim brightness to dim_brightness_percent (e.g. 1%).
Stage 2 (OFF / battery saver): after off_after_seconds, turn devices off (black + brightness 0 best-effort).

- Backward compatible with existing config:
  - If rgb_idle.timeout_seconds exists and rgb_idle.off_after_seconds is not set, timeout_seconds is treated as off_after_seconds.
  - If dim_* fields are missing, DIM stage is disabled.
- Uses OpenRGB CLI (native or Flatpak org.openrgb.OpenRGB). Honors:
    NAGA_OPENRGB_PREFER_FLATPAK=1|true|yes|on
- Runs OpenRGB apply/off in a background worker thread to avoid blocking mouse movement.
- Mapper still provides bindings + pointer_scale behavior like the current naga_proxy_mapper.py.

Config keys used:
  rgb.enabled: bool
  rgb.mouse_device: int (OpenRGB device index)
  rgb.keyboard_device: int (OpenRGB device index)
  rgb.brightness: int 0..100
  rgb.per_profile: { "<profile>": "#RRGGBB" }

  rgb_idle.enabled: bool
  rgb_idle.wake_on_activity: bool
  rgb_idle.dim_enabled: bool (optional, default False unless dim_after_seconds present)
  rgb_idle.dim_after_seconds: int (optional)
  rgb_idle.dim_brightness_percent: int 1..100 (optional, default 1)
  rgb_idle.off_enabled: bool (optional, default True when enabled)
  rgb_idle.off_after_seconds: int (optional)
  rgb_idle.timeout_seconds: int (legacy; treated as off_after_seconds if off_after_seconds missing)

Recommended:
  dim_after_seconds < off_after_seconds

v10 changes (mouse wake lag fix):
  - DIM stage now dims **keyboard only** by default (mouse is left untouched) to avoid
    wireless-mouse wake/input stalls caused by OpenRGB talking to the mouse.
  - On wake, keyboard is applied immediately; mouse apply is deferred by
    rgb_idle.mouse_wake_delay_seconds (default 4s).
  - You can opt back into dimming the mouse by setting:
      rgb_idle.dim_apply_to_mouse = true
    (not recommended for wireless if you see wake lag)

  - You can also disable the OFF stage affecting the mouse (keyboard-only off)
    if you want zero pointer wake lag even after long idle:
      rgb_idle.off_apply_to_mouse = false
"""

import argparse
import json
import os
import select
import signal
import subprocess
import sys
import threading
import shutil
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Set, Any


import unicodedata

def _synapse_name_sort_key(name: str) -> str:
    """Stable sort key (Finnish-ish): pushes Å/Ä/Ö after Z and strips accents."""
    if name is None:
        return ""
    s = str(name).strip().casefold()
    s = s.replace("å","zzza").replace("ä","zzzb").replace("ö","zzzc")
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    return s

from evdev import InputDevice, list_devices, ecodes, UInput

APP_ID = "synapse-lite"
LEGACY_APP_IDS = ['razer-mouse-control-center', 'synapse-lite']

CONFIG: Dict = {}
PROFILE_BINDINGS: Dict[str, dict] = {}

# Modifier tracking (for modifier layers)
MODIFIER_STATE = {"shift": False, "ctrl": False, "alt": False}
MODIFIER_KEYCODES = {
    "shift": {"KEY_LEFTSHIFT", "KEY_RIGHTSHIFT"},
    "ctrl": {"KEY_LEFTCTRL", "KEY_RIGHTCTRL"},
    "alt": {"KEY_LEFTALT", "KEY_RIGHTALT", "KEY_ALTGR"},
}

POINTER_SCALE: float = 1.0
SCROLL_SCALE: float = 1.0

RUNNING = True
DEBUG = False
CONFIG_PATH = ""


def write_pidfile(path: str) -> None:
    try:
        p = Path(os.path.expanduser(path)).resolve()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(str(os.getpid()) + "\n", encoding="utf-8")
    except Exception:
        pass


def remove_file(path: Optional[str]) -> None:
    if not path:
        return
    try:
        Path(os.path.expanduser(path)).resolve().unlink(missing_ok=True)
    except Exception:
        pass


def _clamp_scale(v: float) -> float:
    try:
        v = float(v)
    except Exception:
        v = 1.0
    return max(0.10, min(3.00, v))


def _atomic_write_json(path: str, data: dict) -> None:
    import tempfile

    directory = os.path.dirname(path) or "."
    fd, tmp = tempfile.mkstemp(prefix=".cfg.", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4, sort_keys=False)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        try:
            if os.path.exists(tmp):
                os.unlink(tmp)
        except Exception:
            pass


def load_config(path: str) -> None:
    # If new config path is missing but legacy exists, seed it (best effort).
    try:
        if (not os.path.exists(path)):
            for _old_id in LEGACY_APP_IDS:
                _old = os.path.expanduser(f"~/.config/{_old_id}/config.json")
                if os.path.exists(_old):
                    os.makedirs(os.path.dirname(path), exist_ok=True)
                    try:
                        import shutil
                        shutil.copy2(_old, path)
                    except Exception:
                        pass
                    break
    except Exception:
        pass
    """
    Load config.json robustly.

    Protects against:
      - empty file
      - partially-written JSON
      - valid JSON followed by extra garbage ("Extra data")
    """
    global CONFIG, PROFILE_BINDINGS, CONFIG_PATH, POINTER_SCALE, SCROLL_SCALE
    CONFIG_PATH = path

    if not os.path.exists(path):
        CONFIG = {}
        PROFILE_BINDINGS = {}
        POINTER_SCALE = 1.0
        return

    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = f.read()
    except Exception:
        raw = ""

    if not raw.strip():
        CONFIG = {}
    else:
        try:
            CONFIG = json.loads(raw)
        except json.JSONDecodeError:
            # salvage first JSON object
            try:
                dec = json.JSONDecoder()
                obj, idx = dec.raw_decode(raw.lstrip())
                CONFIG = obj if isinstance(obj, dict) else {}
                trailing = raw.lstrip()[idx:].strip()
                if trailing:
                    try:
                        import time

                        corrupt_path = f"{path}.corrupt.{int(time.time())}"
                        with open(corrupt_path, "w", encoding="utf-8") as b:
                            b.write(raw)
                        _atomic_write_json(path, CONFIG)
                        print(
                            f"[CFG] Salvaged config (had trailing garbage). Backup: {corrupt_path}"
                        )
                    except Exception as e:
                        print(
                            f"[CFG] Salvaged config but failed to rewrite cleanly: {e}"
                        )
            except Exception as e:
                print(f"[CFG] Failed to parse config; using empty config: {e}")
                CONFIG = {}

    if not isinstance(CONFIG, dict):
        CONFIG = {}

    prof = str(CONFIG.get("active_profile", "default") or "default")
    PROFILE_BINDINGS = (CONFIG.get("profiles") or {}).get(prof, {}).get(
        "bindings", {}
    ) or {}

    prof_scale = (
        (CONFIG.get("profiles") or {})
        .get(prof, {})
        .get("settings", {})
        .get("pointer_scale", None)
    )
    if prof_scale is None:
        POINTER_SCALE = _clamp_scale(CONFIG.get("pointer_scale", 1.0))
    else:
        POINTER_SCALE = _clamp_scale(prof_scale)

    prof_scroll = (
        (CONFIG.get("profiles") or {})
        .get(prof, {})
        .get("settings", {})
        .get("scroll_scale", None)
    )
    if prof_scroll is None:
        SCROLL_SCALE = _clamp_scale(CONFIG.get("scroll_scale", 1.0))
    else:
        SCROLL_SCALE = _clamp_scale(prof_scroll)

    prof_scroll = (
        (CONFIG.get("profiles") or {})
        .get(prof, {})
        .get("settings", {})
        .get("scroll_scale", None)
    )
    if prof_scroll is None:
        SCROLL_SCALE = _clamp_scale(CONFIG.get("scroll_scale", 1.0))
    else:
        SCROLL_SCALE = _clamp_scale(prof_scroll)


def reload_from_signal(signum, frame) -> None:
    try:
        if CONFIG_PATH:
            load_config(CONFIG_PATH)
            if DEBUG:
                print(
                    f"[DBG] reloaded config: active_profile={CONFIG.get('active_profile')} pointer_scale={POINTER_SCALE} scroll_scale={SCROLL_SCALE}",
                    file=sys.stderr,
                )
    except Exception as e:
        print(f"[WARN] config reload failed: {e}", file=sys.stderr)


def discover_naga_nodes() -> List[InputDevice]:
    devs: List[InputDevice] = []
    for p in list_devices():
        try:
            d = InputDevice(p)
            if "naga" in (d.name or "").lower():
                devs.append(d)
        except Exception:
            continue
    return devs


def discover_keyboard_nodes() -> List[InputDevice]:
    """Best-effort discovery of keyboard-like input devices for modifier tracking.

    We only *read* from these devices to track Shift/Ctrl/Alt state.
    We never grab them and we never pass their events through uinput.
    """
    devs: List[InputDevice] = []
    modifier_keycodes = {
        ecodes.KEY_LEFTSHIFT,
        ecodes.KEY_RIGHTSHIFT,
        ecodes.KEY_LEFTCTRL,
        ecodes.KEY_RIGHTCTRL,
        ecodes.KEY_LEFTALT,
        ecodes.KEY_RIGHTALT,
        getattr(ecodes, "KEY_ALTGR", 0),
    }

    for p in list_devices():
        try:
            d = InputDevice(p)
        except Exception:
            continue

        try:
            name = (d.name or "").lower()
        except Exception:
            name = ""

        if "uinput" in name or "synapse-lite" in name:
            continue
        if "naga" in name:
            continue

        try:
            caps = d.capabilities(verbose=False) or {}
        except Exception:
            continue
        if ecodes.EV_KEY not in caps:
            continue

        keys = set(caps.get(ecodes.EV_KEY, []) or [])

        looks_like_keyboard = (
            ecodes.KEY_A in keys or ecodes.KEY_Q in keys or "keyboard" in name
        )
        has_modifiers = any(k in keys for k in modifier_keycodes if k)

        if looks_like_keyboard or has_modifiers:
            devs.append(d)

    return devs


def _binding_keycodes(binding: dict) -> List[int]:
    if not isinstance(binding, dict):
        return []
    if binding.get("type") != "keyboard":
        return []
    out: List[int] = []
    for k in binding.get("keys") or []:
        if isinstance(k, str) and k.startswith("KEY_"):
            kc = ecodes.ecodes.get(k)
            if isinstance(kc, int):
                out.append(kc)
    return out


def _binding_button_code(binding: dict) -> Optional[int]:
    if not isinstance(binding, dict):
        return None
    if binding.get("type") != "mouse":
        return None
    btn = binding.get("button")
    if isinstance(btn, str) and btn.startswith("BTN_"):
        bc = ecodes.ecodes.get(btn)
        if isinstance(bc, int):
            return bc
    return None


def build_minimal_uinput_caps(bindings: Dict[str, dict]) -> Dict[int, List[int]]:
    """Build uinput capabilities.

    Historically this project tried to advertise only the keys that appear in the
    *current* profile. That breaks when you auto-switch profiles: keys that are
    not advertised by the virtual device are silently dropped by the kernel.

    The stable approach is to advertise a full keyboard range (plus mouse buttons
    + relative axes) once at startup.
    """
    all_keys: List[int] = list(range(ecodes.KEY_ESC, ecodes.KEY_MAX + 1))
    keyset: List[int] = [
        # mouse buttons
        ecodes.BTN_LEFT,
        ecodes.BTN_RIGHT,
        ecodes.BTN_MIDDLE,
        ecodes.BTN_SIDE,
        ecodes.BTN_EXTRA,
        *all_keys,
    ]
    return {
        ecodes.EV_REL: [
            ecodes.REL_X,
            ecodes.REL_Y,
            ecodes.REL_WHEEL,
            ecodes.REL_HWHEEL,
        ],
        ecodes.EV_KEY: keyset,
    }


# ---------- Auto profile switching (Wayland via kdotool) ----------

DEBUG_AUTO = os.environ.get("NAGA_DEBUG_AUTO", "").strip().lower() in (
    "1",
    "true",
    "yes",
    "on",
)


def _all_profile_bindings(cfg: Dict[str, Any]) -> Dict[str, dict]:
    """Union of bindings across all profiles (and modifier layers if present).

    This is used to build uinput capabilities so switching profiles doesn't
    silently drop keys that weren't present when uinput was created.
    """
    out: Dict[str, dict] = {}
    profiles = cfg.get("profiles") or {}
    for _pname, pobj in profiles.items():
        pobj = pobj or {}
        b = pobj.get("bindings") or {}
        for k, v in b.items():
            out.setdefault(k, v)
        ml = pobj.get("modifier_layers") or {}
        if isinstance(ml, dict):
            for _lname, lmap in ml.items():
                if not isinstance(lmap, dict):
                    continue
                for k, v in lmap.items():
                    out.setdefault(k, v)
    return out


_AUTOSWITCH_LAST_T = 0.0
_AUTOSWITCH_LAST_CLASS: Optional[str] = None
_AUTOSWITCH_LAST_TARGET: Optional[str] = None
_AUTOSWITCH_STABLE_COUNT: int = 0


def _which_kdotool() -> Optional[str]:
    p = shutil.which("kdotool")
    if p:
        return p
    # common fallback when installed via cargo
    cand = os.path.expanduser("~/.cargo/bin/kdotool")
    return cand if os.path.exists(cand) else None


def get_active_window_class() -> Optional[str]:
    kd = _which_kdotool()
    if not kd:
        return None
    try:
        out = subprocess.check_output(
            [kd, "getactivewindow", "getwindowclassname"],
            text=True,
            stderr=subprocess.DEVNULL,
            timeout=0.4,
        )
        cls = (out or "").strip()
        return cls or None
    except Exception:
        return None


def _apply_profile_in_memory(profile: str) -> None:
    """Switch the mapper's active profile without touching the config file on disk."""
    global PROFILE_BINDINGS, POINTER_SCALE, SCROLL_SCALE
    prof = str(profile or "default")

    CONFIG["active_profile"] = prof
    PROFILE_BINDINGS = (CONFIG.get("profiles") or {}).get(prof, {}).get(
        "bindings", {}
    ) or {}

    # pointer_scale: profile override > global
    prof_scale = (
        (CONFIG.get("profiles") or {})
        .get(prof, {})
        .get("settings", {})
        .get("pointer_scale", None)
    )
    if prof_scale is None:
        POINTER_SCALE = _clamp_scale(CONFIG.get("pointer_scale", 1.0))
    else:
        POINTER_SCALE = _clamp_scale(prof_scale)

    prof_scroll = (
        (CONFIG.get("profiles") or {})
        .get(prof, {})
        .get("settings", {})
        .get("scroll_scale", None)
    )
    if prof_scroll is None:
        SCROLL_SCALE = _clamp_scale(CONFIG.get("scroll_scale", 1.0))
    else:
        SCROLL_SCALE = _clamp_scale(prof_scroll)

    prof_scroll = (
        (CONFIG.get("profiles") or {})
        .get(prof, {})
        .get("settings", {})
        .get("scroll_scale", None)
    )
    if prof_scroll is None:
        SCROLL_SCALE = _clamp_scale(CONFIG.get("scroll_scale", 1.0))
    else:
        SCROLL_SCALE = _clamp_scale(prof_scroll)


def autoswitch_tick(rgb_worker=None) -> None:
    """Poll active window class and switch profiles on a timer.

    - Uses CONFIG['app_profiles'] mapping: {window_class: profile_name}
    - If class is unmapped, falls back to 'default' (or CONFIG['fallback_profile']).
    """
    global _AUTOSWITCH_LAST_T, _AUTOSWITCH_LAST_CLASS, _AUTOSWITCH_LAST_TARGET, _AUTOSWITCH_STABLE_COUNT

    if not bool(CONFIG.get("auto_switch_enabled", False)):
        return
    # Manual lock: when user has Set Active to a non-default profile, suppress autoswitch
    # until they Set Active back to default.
    try:
        if bool(CONFIG.get("manual_profile_lock", False)):
            return
    except Exception:
        pass


    now = time.monotonic()
    if now - _AUTOSWITCH_LAST_T < 0.35:
        return
    _AUTOSWITCH_LAST_T = now

    cls = get_active_window_class()
    mapping = CONFIG.get("app_profiles") or {}
    fallback = str(CONFIG.get("fallback_profile", "default") or "default")

    # If we couldn't read the active window class, still allow fallback switching.
    if cls is None:
        cls = "__unknown__"

    target = mapping.get(cls, fallback)

    if cls == _AUTOSWITCH_LAST_CLASS:
        _AUTOSWITCH_STABLE_COUNT += 1
    else:
        _AUTOSWITCH_STABLE_COUNT = 1

    # Require the same class twice in a row before switching to avoid flicker during Alt-Tab.
    if cls != "__unknown__" and _AUTOSWITCH_STABLE_COUNT < 2:
        _AUTOSWITCH_LAST_CLASS = cls
        return

    if target == _AUTOSWITCH_LAST_TARGET and cls == _AUTOSWITCH_LAST_CLASS:
        return

    _AUTOSWITCH_LAST_CLASS = cls
    _AUTOSWITCH_LAST_TARGET = target

    cur = str(CONFIG.get("active_profile", "default") or "default")
    if target != cur:
        if DEBUG_AUTO:
            print(f"[AUTO] class={cls} switch {cur} -> {target}", file=sys.stderr)
        _apply_profile_in_memory(target)
        # Ask RGB worker to apply new profile color ASAP (non-blocking)
        try:
            if rgb_worker:
                rgb_worker.request("apply_active")
        except Exception:
            pass


# ---------- OpenRGB helpers (non-blocking via worker) ----------


def _openrgb_candidate_cmds() -> List[List[str]]:
    import shutil

    prefer_flatpak = os.environ.get(
        "NAGA_OPENRGB_PREFER_FLATPAK", ""
    ).strip().lower() in ("1", "true", "yes", "on")
    native = ["openrgb"] if shutil.which("openrgb") else None
    flatpak = (
        ["flatpak", "run", "--command=openrgb", "org.openrgb.OpenRGB"]
        if shutil.which("flatpak")
        else None
    )

    if prefer_flatpak:
        return [flatpak] if flatpak else ([] if native is None else [native])

    cmds: List[List[str]] = []
    if native:
        cmds.append(native)
    if flatpak:
        cmds.append(flatpak)
    return cmds


def _run_openrgb(args: List[str], timeout: int = 4) -> Tuple[bool, str]:
    cmds = _openrgb_candidate_cmds()
    if not cmds:
        return False, "OpenRGB CLI not found"

    def is_apply_like(a: List[str]) -> bool:
        return ("-c" in a) or ("--mode" in a) or ("-b" in a)

    def run_once(base: List[str], t: int) -> Tuple[int, str]:
        p = subprocess.run(
            base + args,
            capture_output=True,
            text=True,
            timeout=t,
            check=False,
        )
        out = (p.stdout or "").strip()
        err = (p.stderr or "").strip()
        msg = out if out else err
        return p.returncode, (msg or f"OpenRGB rc={p.returncode}")

    last_msg = ""
    last_base = ""
    for base in cmds:
        last_base = " ".join(base)
        try:
            rc, msg = run_once(base, timeout)
            last_msg = msg
            if rc == 0:
                return True, msg

            # some devices need longer on first contact
            if "mouse connection attempt failed" in (msg or "").lower():
                rc2, msg2 = run_once(base, max(timeout, 10))
                last_msg = msg2
                if rc2 == 0:
                    return True, msg2

        except subprocess.TimeoutExpired:
            if is_apply_like(args):
                return True, f"[{last_base}] applied (timed out after {timeout}s)"
            last_msg = "OpenRGB command timed out."
            continue
        except Exception as e:
            last_msg = f"OpenRGB error: {e}"
            continue

    return False, f"[{last_base}] {last_msg or 'OpenRGB failed'}"


def _hex_to_rgb(hexcol: str) -> Optional[Tuple[int, int, int]]:
    s = (hexcol or "").strip()
    if not s.startswith("#") or len(s) != 7:
        return None
    try:
        r = int(s[1:3], 16)
        g = int(s[3:5], 16)
        b = int(s[5:7], 16)
        return (r, g, b)
    except Exception:
        return None


def _rgb_color_for_active_profile(cfg: Dict) -> Tuple[int, int, int]:
    prof = str(cfg.get("active_profile", "default") or "default")
    rgb_cfg = (cfg.get("rgb") or {}) if isinstance(cfg.get("rgb"), dict) else {}
    per = (
        (rgb_cfg.get("per_profile") or {})
        if isinstance(rgb_cfg.get("per_profile"), dict)
        else {}
    )

    # Prefer exact match; if missing and this profile is a subprofile, inherit from its base.
    hexcol = per.get(prof)
    if not hexcol:
        try:
            profs = (cfg.get("profiles") or {}) if isinstance(cfg.get("profiles"), dict) else {}
            base = (profs.get(prof, {}).get("settings") or {}).get("subprofile_of")
            if base and base in per:
                hexcol = per.get(base)
        except Exception:
            hexcol = None

    hexcol = str(hexcol or "#000000")
    rgbv = _hex_to_rgb(hexcol)
    return rgbv if rgbv else (0, 0, 0)


def _clamp_int(v, lo, hi, default):
    try:
        iv = int(v)
    except Exception:
        iv = int(default)
    return max(int(lo), min(int(hi), iv))


def _apply_openrgb_device(
    dev_idx: int, *, r: int, g: int, b: int, brightness: int
) -> Tuple[bool, str]:
    # Use direct mode to set color, plus brightness.
    color_arg = f"{r:02X}{g:02X}{b:02X}"
    args = [
        "-d",
        str(int(dev_idx)),
        "--mode",
        "direct",
        "-c",
        color_arg,
        "-b",
        str(int(brightness)),
    ]
    ok, msg = _run_openrgb(args, timeout=4)
    if ok:
        return True, msg

    # Fallback: some builds/device combos don't like -b 0; try color-only.
    if int(brightness) == 0:
        ok2, msg2 = _run_openrgb(
            ["-d", str(int(dev_idx)), "--mode", "direct", "-c", color_arg], timeout=4
        )
        if ok2:
            return True, msg2
        return False, msg2
    return False, msg


class _RGBWorker:
    """
    Single background worker that coalesces requests:
    - You can request 'apply_active', 'dim', or 'off'.
    - Only the latest request is executed (older pending ones are skipped).
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._cv = threading.Condition(self._lock)
        self._running = True
        self._want: Optional[str] = None
        self._want_version = 0
        self._thread = threading.Thread(
            target=self._run, name="rgb-worker", daemon=True
        )
        self._thread.start()

    def stop(self):
        with self._cv:
            self._running = False
            self._cv.notify_all()

    def request(self, mode: str):
        with self._cv:
            self._want = mode
            self._want_version += 1
            self._cv.notify_all()

    def _run(self):
        last_done_version = 0
        while True:
            with self._cv:
                while self._running and (
                    self._want is None or self._want_version == last_done_version
                ):
                    self._cv.wait(timeout=1.0)
                if not self._running:
                    return
                mode = self._want
                version = self._want_version

            # Execute outside lock
            if mode:
                try:
                    rgb_apply_mode(mode)
                except Exception as e:
                    if DEBUG:
                        print(f"[DBG] rgb worker error: {e}", file=sys.stderr)

            last_done_version = version


def rgb_apply_mode(mode: str) -> None:
    """
    Apply one of: 'apply_active', 'dim', 'off'
    Reads CONFIG each time, so it stays current across SIGHUP reload.
    """
    rgb_cfg = (CONFIG.get("rgb") or {}) if isinstance(CONFIG.get("rgb"), dict) else {}
    idle_cfg = (
        (CONFIG.get("rgb_idle") or {})
        if isinstance(CONFIG.get("rgb_idle"), dict)
        else {}
    )

    if not bool(rgb_cfg.get("enabled", False)):
        return

    mouse_idx = rgb_cfg.get("mouse_device")
    kb_idx = rgb_cfg.get("keyboard_device")
    if mouse_idx is None or kb_idx is None:
        return

    base_brightness = _clamp_int(rgb_cfg.get("brightness", 100), 0, 100, 100)
    r, g, b = _rgb_color_for_active_profile(CONFIG)

    if mode in ("apply_active", "apply_active_kb", "apply_active_mouse"):
        bval = base_brightness
        okm = okk = True

        # Always apply keyboard first (it doesn't affect pointer latency)
        if mode in ("apply_active", "apply_active_kb"):
            okk, _ = _apply_openrgb_device(int(kb_idx), r=r, g=g, b=b, brightness=bval)

        # Mouse apply can cause a few-second wake stall on some wireless devices.
        # We keep it separate so we can defer it after activity.
        if mode in ("apply_active", "apply_active_mouse"):
            okm, _ = _apply_openrgb_device(
                int(mouse_idx), r=r, g=g, b=b, brightness=bval
            )

        if DEBUG:
            print(
                f"[RGB:APPLY_ACTIVE] mode={mode} mouse={okm} kb={okk} bright={bval}",
                file=sys.stderr,
            )
        return

    if mode == "dim":
        dim_pct = _clamp_int(idle_cfg.get("dim_brightness_percent", 1), 1, 100, 1)
        dim_mouse = bool(idle_cfg.get("dim_apply_to_mouse", False))
        okk, _ = _apply_openrgb_device(int(kb_idx), r=r, g=g, b=b, brightness=dim_pct)
        okm = True
        if dim_mouse:
            okm, _ = _apply_openrgb_device(
                int(mouse_idx), r=r, g=g, b=b, brightness=dim_pct
            )
        if DEBUG:
            print(
                f"[RGB:DIM] mouse={okm} kb={okk} bright={dim_pct} dim_mouse={dim_mouse}",
                file=sys.stderr,
            )
        return

    if mode == "off":
        # best-effort off: black + brightness 0
        off_mouse = bool(idle_cfg.get("off_apply_to_mouse", True))
        okk, _ = _apply_openrgb_device(int(kb_idx), r=0, g=0, b=0, brightness=0)
        okm = True
        if off_mouse:
            okm, _ = _apply_openrgb_device(int(mouse_idx), r=0, g=0, b=0, brightness=0)
        if DEBUG:
            print(
                f"[RGB:OFF] mouse={okm} kb={okk} off_mouse={off_mouse}", file=sys.stderr
            )
        return


class RGBIdleManager:
    """
    Two-stage idle manager: ACTIVE -> DIM -> OFF (battery saver)
    """

    ACTIVE = "active"
    DIM = "dim"
    OFF = "off"

    def __init__(self, worker: _RGBWorker):
        self.worker = worker
        self.state = self.ACTIVE
        self.last_activity = time.time()
        self._last_tick = 0.0
        self._mouse_wake_timer: Optional[threading.Timer] = None

    def _schedule_mouse_wake_apply(self):
        """Defer mouse RGB apply after activity to avoid pointer wake stalls."""
        idle_cfg = (
            (CONFIG.get("rgb_idle") or {})
            if isinstance(CONFIG.get("rgb_idle"), dict)
            else {}
        )
        delay = _clamp_int(idle_cfg.get("mouse_wake_delay_seconds", 4), 0, 30, 4)

        # Cancel any previous scheduled apply
        try:
            if self._mouse_wake_timer:
                self._mouse_wake_timer.cancel()
        except Exception:
            pass

        if delay <= 0:
            self.worker.request("apply_active_mouse")
            return

        def _fire():
            # Only apply mouse if we're still active.
            idle_cfg2 = (
                (CONFIG.get("rgb_idle") or {})
                if isinstance(CONFIG.get("rgb_idle"), dict)
                else {}
            )
            if not bool(idle_cfg2.get("enabled", False)):
                return
            if self.state != self.ACTIVE:
                return
            self.worker.request("apply_active_mouse")
            if DEBUG:
                print(
                    f"[RGB:WAKE] deferred mouse apply fired after {delay}s",
                    file=sys.stderr,
                )

        self._mouse_wake_timer = threading.Timer(float(delay), _fire)
        self._mouse_wake_timer.daemon = True
        self._mouse_wake_timer.start()
        if DEBUG:
            print(f"[RGB:WAKE] scheduled mouse apply in {delay}s", file=sys.stderr)

    def notify_activity(self):
        self.last_activity = time.time()
        idle_cfg = (
            (CONFIG.get("rgb_idle") or {})
            if isinstance(CONFIG.get("rgb_idle"), dict)
            else {}
        )
        if not bool(idle_cfg.get("enabled", False)):
            return
        if bool(idle_cfg.get("wake_on_activity", True)):
            if self.state != self.ACTIVE:
                self.state = self.ACTIVE
                # Apply keyboard immediately; defer mouse to avoid wake stalls.
                self.worker.request("apply_active_kb")
                self._schedule_mouse_wake_apply()
                if DEBUG:
                    print(
                        "[RGB:WAKE] requested apply_active_kb (+ deferred mouse)",
                        file=sys.stderr,
                    )

    def _read_cfg(self) -> Tuple[bool, bool, int, bool, int]:
        idle_cfg = (
            (CONFIG.get("rgb_idle") or {})
            if isinstance(CONFIG.get("rgb_idle"), dict)
            else {}
        )
        enabled = bool(idle_cfg.get("enabled", False))
        if not enabled:
            return (False, False, 0, False, 0)

        # DIM stage
        dim_after = idle_cfg.get("dim_after_seconds", None)
        dim_enabled = bool(idle_cfg.get("dim_enabled", False)) or (
            dim_after is not None
        )
        dim_after_s = (
            _clamp_int(dim_after if dim_after is not None else 0, 1, 86400, 120)
            if dim_enabled
            else 0
        )

        # OFF stage
        off_after = idle_cfg.get("off_after_seconds", None)
        if off_after is None:
            # legacy
            off_after = idle_cfg.get("timeout_seconds", None)
        off_enabled = bool(idle_cfg.get("off_enabled", True))
        off_after_s = (
            _clamp_int(off_after if off_after is not None else 0, 1, 7 * 86400, 600)
            if off_enabled
            else 0
        )

        # ensure ordering if both enabled
        if dim_enabled and off_enabled and off_after_s <= dim_after_s:
            # push OFF to at least dim+60s
            off_after_s = dim_after_s + 60

        return (
            dim_enabled,
            True if dim_enabled else False,
            dim_after_s,
            off_enabled,
            off_after_s,
        )

    def tick(self):
        # limit tick rate
        now = time.time()
        if now - self._last_tick < 0.2:
            return
        self._last_tick = now

        rgb_cfg = (
            (CONFIG.get("rgb") or {}) if isinstance(CONFIG.get("rgb"), dict) else {}
        )
        if not bool(rgb_cfg.get("enabled", False)):
            return

        idle_cfg = (
            (CONFIG.get("rgb_idle") or {})
            if isinstance(CONFIG.get("rgb_idle"), dict)
            else {}
        )
        if not bool(idle_cfg.get("enabled", False)):
            return

        dim_enabled, _, dim_after_s, off_enabled, off_after_s = self._read_cfg()
        idle_for = now - self.last_activity

        # Transition logic
        if off_enabled and off_after_s > 0 and idle_for >= off_after_s:
            if self.state != self.OFF:
                self.state = self.OFF
                self.worker.request("off")
                if DEBUG:
                    print(
                        f"[RGB:IDLE->OFF] idle_for={idle_for:.1f}s off_after={off_after_s}s",
                        file=sys.stderr,
                    )
            return

        if dim_enabled and dim_after_s > 0 and idle_for >= dim_after_s:
            if self.state == self.ACTIVE:
                self.state = self.DIM
                self.worker.request("dim")
                if DEBUG:
                    print(
                        f"[RGB:IDLE->DIM] idle_for={idle_for:.1f}s dim_after={dim_after_s}s",
                        file=sys.stderr,
                    )
            return

        # If timers changed (config reload) and we're in DIM/OFF but should be ACTIVE, bring it back on next activity only.
        # (We intentionally don't auto-wake without activity.)


def resolve_binding(logical: str) -> dict:
    """Resolve binding with modifier layer priority: Shift > Ctrl > Alt > Normal.

    If a modifier layer has no binding for this logical action, it is ignored.
    """
    # Active profile object
    prof = str(CONFIG.get("active_profile", "default") or "default")
    pobj = (CONFIG.get("profiles") or {}).get(prof, {}) or {}

    # Check modifier layers first
    layers = pobj.get("modifier_layers") or {}
    if isinstance(layers, dict):
        for mod in ("shift", "ctrl", "alt"):
            if MODIFIER_STATE.get(mod):
                b = (layers.get(mod) or {}).get(logical)
                if isinstance(b, dict) and b:
                    return b

    # Fallback normal layer (already cached in PROFILE_BINDINGS)
    b = (PROFILE_BINDINGS or {}).get(logical) or {}
    return b if isinstance(b, dict) else {}


# -------------------------
# Macro support (Synapse-like)
# -------------------------

_MACRO_THREADS: Dict[str, Tuple[threading.Thread, threading.Event, dict]] = {}


def _profile_obj() -> dict:
    prof = CONFIG.get("active_profile", "default")
    return (CONFIG.get("profiles") or {}).get(prof, {}) or {}



def resolve_keyboard_binding(key_name: str) -> dict:
    """Resolve a keyboard binding with modifier layer priority: Shift > Ctrl > Alt > Normal.

    Looks under active profile:
      profile["keyboard_bindings"]["shift"|"ctrl"|"alt"|"normal"][KEY_*]
    """
    if not isinstance(key_name, str) or not key_name.startswith("KEY_"):
        return {}

    prof = str(CONFIG.get("active_profile", "default") or "default")
    pobj = (CONFIG.get("profiles") or {}).get(prof) or {}
    if not isinstance(pobj, dict):
        pobj = {}

    kb = pobj.get("keyboard_bindings") or {}
    if not isinstance(kb, dict):
        kb = {}

    normal = kb.get("normal") or kb.get("NORMAL") or {}
    shift  = kb.get("shift")  or kb.get("SHIFT")  or {}
    ctrl   = kb.get("ctrl")   or kb.get("CTRL")   or {}
    alt    = kb.get("alt")    or kb.get("ALT")    or {}

    # Check modifier layers only if that modifier is currently held
    if MODIFIER_STATE.get("shift") and isinstance(shift, dict) and isinstance(shift.get(key_name), dict):
        return shift.get(key_name) or {}
    if MODIFIER_STATE.get("ctrl") and isinstance(ctrl, dict) and isinstance(ctrl.get(key_name), dict):
        return ctrl.get(key_name) or {}
    if MODIFIER_STATE.get("alt") and isinstance(alt, dict) and isinstance(alt.get(key_name), dict):
        return alt.get(key_name) or {}

    # Fallback to normal
    if isinstance(normal, dict) and isinstance(normal.get(key_name), dict):
        return normal.get(key_name) or {}

    # If we're on a subprofile and the key isn't overridden here, inherit from base profile.
    try:
        profs = (CONFIG.get("profiles") or {}) if isinstance(CONFIG.get("profiles"), dict) else {}
        base = (profs.get(prof, {}).get("settings") or {}).get("subprofile_of")
        if base and base in profs:
            kb2 = (profs.get(base, {}) or {}).get("keyboard_bindings") or {}
            if isinstance(kb2, dict):
                normal2 = kb2.get("normal") or kb2.get("NORMAL") or {}
                shift2  = kb2.get("shift")  or kb2.get("SHIFT")  or {}
                ctrl2   = kb2.get("ctrl")   or kb2.get("CTRL")   or {}
                alt2    = kb2.get("alt")    or kb2.get("ALT")    or {}

                if MODIFIER_STATE.get("shift") and isinstance(shift2, dict) and isinstance(shift2.get(key_name), dict):
                    return shift2.get(key_name) or {}
                if MODIFIER_STATE.get("ctrl") and isinstance(ctrl2, dict) and isinstance(ctrl2.get(key_name), dict):
                    return ctrl2.get(key_name) or {}
                if MODIFIER_STATE.get("alt") and isinstance(alt2, dict) and isinstance(alt2.get(key_name), dict):
                    return alt2.get(key_name) or {}
                if isinstance(normal2, dict) and isinstance(normal2.get(key_name), dict):
                    return normal2.get(key_name) or {}
    except Exception:
        pass
    return {}



def _get_macro(name: str) -> Optional[dict]:
    # Prefer global macro library (shared across profiles), with legacy fallback.
    gmacros = CONFIG.get("macros")
    if isinstance(gmacros, dict):
        mv = gmacros.get(name)
        if isinstance(mv, dict):
            return mv

    pobj = _profile_obj()
    macros = pobj.get("macros") or {}
    mv = macros.get(name)
    return mv if isinstance(mv, dict) else None


def _text_to_key_events(s: str) -> List[Tuple[List[int], bool]]:
    """
    Convert text to a sequence of (keys, pressed) events using a US-like mapping.
    This is best-effort; for game macros prefer explicit key steps.
    """
    out: List[Tuple[List[int], bool]] = []

    def combo(keys: List[int]):
        out.append((keys, True))
        out.append((keys, False))

    # minimal map for common macro text
    base_map = {
        " ": ("KEY_SPACE", False),
        "\n": ("KEY_ENTER", False),
        "\t": ("KEY_TAB", False),
        "-": ("KEY_MINUS", False),
        "_": ("KEY_MINUS", True),
        "=": ("KEY_EQUAL", False),
        "+": ("KEY_EQUAL", True),
        "[": ("KEY_LEFTBRACE", False),
        "{": ("KEY_LEFTBRACE", True),
        "]": ("KEY_RIGHTBRACE", False),
        "}": ("KEY_RIGHTBRACE", True),
        ";": ("KEY_SEMICOLON", False),
        ":": ("KEY_SEMICOLON", True),
        "'": ("KEY_APOSTROPHE", False),
        '"': ("KEY_APOSTROPHE", True),
        ",": ("KEY_COMMA", False),
        "<": ("KEY_COMMA", True),
        ".": ("KEY_DOT", False),
        ">": ("KEY_DOT", True),
        "/": ("KEY_SLASH", False),
        "?": ("KEY_SLASH", True),
        "\\": ("KEY_BACKSLASH", False),
        "|": ("KEY_BACKSLASH", True),
        "`": ("KEY_GRAVE", False),
        "~": ("KEY_GRAVE", True),
        "!": ("KEY_1", True),
        "@": ("KEY_2", True),
        "#": ("KEY_3", True),
        "$": ("KEY_4", True),
        "%": ("KEY_5", True),
        "^": ("KEY_6", True),
        "&": ("KEY_7", True),
        "*": ("KEY_8", True),
        "(": ("KEY_9", True),
        ")": ("KEY_0", True),
    }

    for ch in s:
        if "a" <= ch <= "z":
            kc = ecodes.ecodes.get(f"KEY_{ch.upper()}")
            if kc is not None:
                combo([kc])
            continue
        if "A" <= ch <= "Z":
            kc = ecodes.ecodes.get(f"KEY_{ch}")
            sh = ecodes.ecodes.get("KEY_LEFTSHIFT")
            if kc is not None and sh is not None:
                combo([sh, kc])
            continue
        if "0" <= ch <= "9":
            kc = ecodes.ecodes.get(f"KEY_{ch}")
            if kc is not None:
                combo([kc])
            continue
        if ch in base_map:
            key_name, need_shift = base_map[ch]
            kc = ecodes.ecodes.get(key_name)
            if kc is None:
                continue
            if need_shift:
                sh = ecodes.ecodes.get("KEY_LEFTSHIFT")
                if sh is None:
                    continue
                combo([sh, kc])
            else:
                combo([kc])
            continue
        # unsupported char: skip
    return out


def _macro_keycode(code: str) -> Optional[int]:
    if not isinstance(code, str) or not code.startswith(("KEY_", "BTN_")):
        return None
    return ecodes.ecodes.get(code)


def _macro_should_block_reentry(m: dict) -> bool:
    # New schema: m['no_overlap']; legacy: options.dont_repeat_if_running
    if "no_overlap" in m:
        return bool(m.get("no_overlap"))
    opt = m.get("options") or {}
    return bool(opt.get("dont_repeat_if_running", True))


def _macro_stop_on_release(m: dict) -> bool:
    # New schema: m['stop_mode'] in {'on_release','finish'}; legacy: options.stop_on_release
    sm = str(m.get("stop_mode") or "")
    if sm:
        smn = sm.strip().lower().replace(" ", "_")
        return smn == "on_release"
    opt = m.get("options") or {}
    return bool(opt.get("stop_on_release", False))


def _macro_repeat_mode(m: dict) -> Tuple[str, int]:
    """
    Returns (mode, n).

    GUI/current schema:
      m['repeat'] = {'mode': 'none'|'n'|'while_held'|'toggle', 'count': int, 'delay_ms': int}

    Legacy schema (fallback):
      m['options'] = {'repeat_mode': 'once'|'n'|'while_held'|'toggle', 'repeat_count': int}

    Normalized modes returned by this function:
      - 'once', 'n', 'while_held', 'toggle'
    And n meaning:
      - once: ignored
      - n: number of times (min 1)
      - while_held: max loops while held (0 = infinite)
      - toggle: max loops until toggled off (0 = infinite)
    """
    rep = m.get("repeat") or {}
    mode_raw = rep.get("mode")
    # Normalize 'none' -> once
    if mode_raw is None:
        # legacy
        opt = m.get("options") or {}
        mode_raw = opt.get("repeat_mode", "once")
        n_raw = opt.get("repeat_count", 1)
    else:
        if str(mode_raw).lower() in ("none", "", "once"):
            mode_raw = "once"
        n_raw = rep.get("count", 1)

    mode = str(mode_raw).strip().lower().replace(" ", "_")
    if mode in ("none", ""):
        mode = "once"
    if mode == "n_times":
        mode = "n"
    if mode not in ("once", "n", "while_held", "toggle"):
        mode = "once"

    try:
        n = int(n_raw) if n_raw is not None else 1
    except Exception:
        n = 1

    if mode == "n":
        if n < 1:
            n = 1
    else:
        # allow 0 meaning infinite for while_held/toggle
        if n < 0:
            n = 0
    return mode, n


def _run_macro_thread(
    mapper: "Mapper", logical: str, m: dict, cancel: threading.Event
) -> None:
    steps = m.get("steps") or []
    if not isinstance(steps, list):
        return

    # Track keys/buttons we pressed down so we can safely release them on cancel.
    pressed_codes: set[int] = set()

    # Timing options: prefer GUI schema (m['timing']), fallback to legacy m['options'].
    timing = m.get("timing") or {}
    tmode = str(timing.get("mode") or "")
    if tmode:
        use_recorded = tmode == "recorded"
        fixed_delay_ms = int(timing.get("fixed_ms") or 30)
    else:
        use_recorded = bool((m.get("options") or {}).get("use_recorded_delays", True))
        fixed_delay_ms = int((m.get("options") or {}).get("fixed_delay_ms", 30) or 30)

    mode, n = _macro_repeat_mode(m)

    rep = m.get("repeat") or {}
    try:
        repeat_gap_ms = int(rep.get("delay_ms") or 0)
    except Exception:
        repeat_gap_ms = 0
    repeat_gap_sec = max(0.0, repeat_gap_ms / 1000.0)

    def _sleep_cancelable(seconds: float) -> None:
        end = time.time() + max(0.0, seconds)
        while time.time() < end and not cancel.is_set():
            time.sleep(min(0.02, max(0.0, end - time.time())))

    def _sleep_gap() -> None:
        # Avoid hot-loop even when gap is 0.
        _sleep_cancelable(repeat_gap_sec if repeat_gap_sec > 0 else 0.005)

    def _emit_key(code: int, down: bool) -> None:
        mapper.ui.write(ecodes.EV_KEY, code, 1 if down else 0)
        mapper.ui.syn()
        if down:
            pressed_codes.add(code)
        else:
            pressed_codes.discard(code)

    def play_once() -> None:
        for st in steps:
            if cancel.is_set():
                return
            if not isinstance(st, dict):
                continue
            t = st.get("type")

            if t == "sleep":
                ms = st.get("ms")
                try:
                    ms = float(ms)
                except Exception:
                    ms = 0.0
                if not use_recorded:
                    ms = float(fixed_delay_ms)
                if ms > 0:
                    _sleep_cancelable(ms / 1000.0)
                continue

            if t in ("key", "mouse"):
                code = st.get("code") or st.get("button")
                down = bool(st.get("down", True))
                kc = _macro_keycode(code)
                if kc is None:
                    continue
                _emit_key(kc, down)
                continue

            if t == "text":
                s = st.get("text") or ""
                if not isinstance(s, str):
                    continue
                for keys, pressed in _text_to_key_events(s):
                    if cancel.is_set():
                        return
                    # This helper emits both press and release, so no tracking needed here.
                    mapper._emit_key_combo(keys, pressed)
                continue

    try:
        if mode == "toggle":
            loops = 0
            while not cancel.is_set() and (n == 0 or loops < n):
                play_once()
                loops += 1
                _sleep_gap()
            return

        if mode == "while_held":
            loops = 0
            while not cancel.is_set() and (n == 0 or loops < n):
                play_once()
                loops += 1
                _sleep_gap()
            return

        if mode == "n":
            for i in range(n):
                if cancel.is_set():
                    return
                play_once()
                if i != n - 1:
                    _sleep_gap()
            return

        # once
        play_once()
        return
    finally:
        # Release anything we left pressed to avoid "stuck key" bugs on cancel.
        for kc in list(pressed_codes):
            try:
                mapper.ui.write(ecodes.EV_KEY, kc, 0)
            except Exception:
                pass
        try:
            mapper.ui.syn()
        except Exception:
            pass

        cur = _MACRO_THREADS.get(logical)
        if cur and cur[0] is threading.current_thread():
            _MACRO_THREADS.pop(logical, None)


class Mapper:
    def __init__(self, *, grab: bool, debug: bool, grab_keyboard: bool=False, passthrough_modifiers: bool=False, panic_combo: str='RCTRL+RALT+BACKSPACE'):
        self.nodes = discover_naga_nodes()
        # Extra read-only keyboard nodes for modifier tracking
        self.kb_nodes = discover_keyboard_nodes()
        if self.kb_nodes:
            print("Detected keyboard nodes for modifier tracking:")
            for d in self.kb_nodes:
                try:
                    print(f"  - {d.path}  '{d.name}'")
                except Exception:
                    print(f"  - {getattr(d,'path','?')}")
        else:
            print(
                "Detected keyboard nodes for modifier tracking: NONE (Ctrl/Alt layers may not work)"
            )

        self.grab_keyboard = bool(grab_keyboard)
        self.passthrough_modifiers = bool(passthrough_modifiers)
        self.panic_combo = str(panic_combo or "RCTRL+RALT+BACKSPACE")
        # Track panic combo key states when keyboard grab is enabled (default: KEY_RIGHTCTRL + KEY_RIGHTALT + KEY_BACKSPACE)
        self._panic_state = {"KEY_RIGHTCTRL": False, "KEY_RIGHTALT": False, "KEY_BACKSPACE": False}

        if self.grab_keyboard and self.kb_nodes:
            for d in self.kb_nodes:
                try:
                    d.grab()
                except Exception as _e:
                    print(f"[WARN] Failed to grab keyboard node {getattr(d,'path','?')}: {_e}", file=sys.stderr)

        if not self.nodes:
            raise RuntimeError("No Naga nodes found (name contains 'Naga').")

        self.grab = grab
        self.debug = debug

        print("Detected Naga nodes:")
        for n in self.nodes:
            tags = []
            try:
                caps = n.capabilities()
                if ecodes.EV_REL in caps:
                    tags.append("REL")
                if ecodes.EV_KEY in caps:
                    tags.append("KEY")
            except Exception:
                pass
            print(f"  - {n.path}  '{n.name}'  [{', '.join(tags) if tags else 'misc'}]")

        self.physical_to_logical: Dict[Tuple[int, int], str] = {
                        (ecodes.EV_KEY, ecodes.BTN_MIDDLE): "middle_click",
            (ecodes.EV_KEY, ecodes.KEY_F7): "middle_click",
            (ecodes.EV_KEY, ecodes.KEY_F3): "bottom_row_right",
            (ecodes.EV_KEY, ecodes.KEY_F4): "top_row_left",
            (ecodes.EV_KEY, ecodes.KEY_F5): "top_row_middle",
            (ecodes.EV_KEY, ecodes.KEY_F6): "top_row_right",
            (ecodes.EV_KEY, ecodes.KEY_F8): "wheel_tilt_left",
            (ecodes.EV_KEY, ecodes.KEY_F9): "wheel_tilt_right",
            (ecodes.EV_KEY, ecodes.KEY_F10): "dpi_up",
            (ecodes.EV_KEY, ecodes.KEY_F11): "dpi_down",
        }

        # Side buttons are layout-dependent remappable keys
        self._side_btn_codes = {ecodes.BTN_SIDE, ecodes.BTN_EXTRA}


        caps = build_minimal_uinput_caps(_all_profile_bindings(CONFIG))
        self.ui = UInput(caps, name="synapse-lite (uinput)")

        # remainders for smooth scaling
        self._rem_x = 0.0
        self._rem_y = 0.0
        self._rem_wheel = 0.0
        self._rem_hwheel = 0.0
        self._rem_wheel_hi = 0.0
        self._rem_hwheel_hi = 0.0

        # RGB idle manager (non-blocking)
        self.rgb_worker = _RGBWorker()
        self.rgb_idle = RGBIdleManager(self.rgb_worker)

        # Apply active RGB once on start (if enabled)
        self.rgb_worker.request("apply_active")

        # Repeat support (for "repeat": true bindings)
        # Map logical action -> stop Event + thread
        self._repeat_stop: Dict[str, threading.Event] = {}
        self._repeat_thread: Dict[str, threading.Thread] = {}


    def _active_panel_layout(self) -> str:
        try:
            prof = str(CONFIG.get("active_profile", "default") or "default")
            pobj = (CONFIG.get("profiles") or {}).get(prof) or {}
            settings = (pobj.get("settings") or {}) if isinstance(pobj, dict) else {}
            layout = str(settings.get("panel_layout", "6") or "6")
            return layout if layout in ("2", "6", "12") else "6"
        except Exception:
            return "6"

    def _map_side_button(self, code: int) -> str:
        """Map physical BTN_SIDE/BTN_EXTRA to a logical action key for the active panel layout."""
        layout = self._active_panel_layout()

        # BTN_SIDE is typically MB4 (back), BTN_EXTRA is MB5 (forward).
        if layout == "2":
            # Our 2-button UI model swaps MB4/MB5
            return "mb5" if code == ecodes.BTN_SIDE else "mb4"

        if layout == "6":
            # Expose as bottom-left/bottom-middle in 6-button layout.
            # Swap to match your physical plate (bottom-left <-> bottom-middle).
            return "thumb6_bottom_middle" if code == ecodes.BTN_SIDE else "thumb6_bottom_left"

        # Fallback legacy logicals (still remappable in bindings list if you expose them)
        return "browse_forward" if code == ecodes.BTN_SIDE else "browse_backward"


    def _cycle_subprofile(self) -> None:
        """Cycle to the next subprofile under the current base profile (wrap-around).

        - If active profile is a subprofile, base = subprofile_of.
        - If active profile is a base profile, base = itself.
        - If there are no subprofiles under base, do nothing.
        Persists CONFIG to disk and reapplies RGB.
        """
        try:
            profs = (CONFIG.get("profiles") or {}) if isinstance(CONFIG.get("profiles"), dict) else {}
            cur = str(CONFIG.get("active_profile", "default") or "default")
            cur_settings = (profs.get(cur, {}).get("settings") or {}) if isinstance(profs.get(cur), dict) else {}
            base = str(cur_settings.get("subprofile_of") or cur)

            subs = []
            for name, pdata in profs.items():
                if not isinstance(pdata, dict):
                    continue
                settings = pdata.get("settings") or {}
                if isinstance(settings, dict) and str(settings.get("subprofile_of") or "") == base:
                    subs.append(str(name))
            subs = sorted(subs, key=_synapse_name_sort_key)

            if not subs:
                return

            if cur in subs:
                i = subs.index(cur)
                nxt = subs[(i + 1) % len(subs)]
            else:
                nxt = subs[0]

            # Apply in-memory (updates bindings + scales)
            _apply_profile_in_memory(nxt)

            # Persist last-used subprofile for this base
            try:
                CONFIG.setdefault("last_subprofiles", {})[base] = nxt
            except Exception:
                pass

            # Best-effort persist config to disk
            try:
                if CONFIG_PATH:
                    _atomic_write_json(CONFIG_PATH, CONFIG)
            except Exception:
                pass

            # Re-apply RGB for the new active profile (non-blocking)
            try:
                self.rgb_worker.request("apply_active")
            except Exception:
                pass
        except Exception as e:
            # Never crash mapper from a special action
            if DEBUG:
                print(f"[WARN] cycle_subprofile failed: {e}", file=sys.stderr)
            return


    def _repeat_enabled(self, binding: dict) -> bool:
        return bool(isinstance(binding, dict) and binding.get("repeat", False))

    def _repeat_interval_sec(self, binding: dict) -> float:
        # interval is in milliseconds in config; default 100ms
        try:
            ms = int(binding.get("interval", 100))
        except Exception:
            ms = 100
        ms = max(10, min(5000, ms))
        return ms / 1000.0

    def _stop_repeat(self, logical: str) -> None:
        ev = self._repeat_stop.pop(logical, None)
        if ev is not None:
            ev.set()
        th = self._repeat_thread.pop(logical, None)
        if th is not None and th.is_alive():
            # Don't join forever; keep the mapper responsive
            th.join(timeout=0.2)

    def _start_repeat(self, logical: str, binding: dict) -> None:
        # If already repeating, keep it running
        if logical in self._repeat_thread and self._repeat_thread[logical].is_alive():
            return

        interval = self._repeat_interval_sec(binding)

        stop_ev = threading.Event()
        self._repeat_stop[logical] = stop_ev

        # Snapshot the output now (so changes mid-repeat take effect on next press)
        btype = binding.get("type")
        keycodes = _binding_keycodes(binding) if btype == "keyboard" else []
        btn_code = _binding_button_code(binding) if btype == "mouse" else None

        def _worker() -> None:
            # small delay so the initial press doesn't immediately double-trigger
            time.sleep(interval)
            while not stop_ev.is_set():
                try:
                    if btype == "keyboard" and keycodes:
                        # Emit a full press+release each cycle
                        self._emit_key_combo(keycodes, True)
                        self._emit_key_combo(keycodes, False)
                    elif btype == "mouse" and btn_code is not None:
                        self._emit_button(int(btn_code), True)
                        self._emit_button(int(btn_code), False)
                except Exception:
                    # Never crash the mapper because repeat failed
                    pass

                # Sleep in small chunks so stop is responsive
                end = time.time() + interval
                while time.time() < end and not stop_ev.is_set():
                    time.sleep(min(0.02, max(0.0, end - time.time())))

        th = threading.Thread(target=_worker, name=f"repeat:{logical}", daemon=True)
        self._repeat_thread[logical] = th
        th.start()

    def _syn(self):
        try:
            self.ui.syn()
        except Exception:
            pass

    def _emit_key_combo(self, keycodes: List[int], pressed: bool) -> None:
        if pressed:
            for kc in keycodes:
                self.ui.write(ecodes.EV_KEY, kc, 1)
        else:
            for kc in reversed(keycodes):
                self.ui.write(ecodes.EV_KEY, kc, 0)
        self._syn()

    def _emit_button(self, btn_code: int, pressed: bool) -> None:
        self.ui.write(ecodes.EV_KEY, btn_code, 1 if pressed else 0)
        self._syn()

    def _emit_middle_passthrough(self, pressed: bool) -> None:
        self._emit_button(ecodes.BTN_MIDDLE, pressed)

    def handle_logical(self, logical: str, pressed: bool) -> bool:
        # If a macro is currently running on this logical button, handle toggle/stop on release
        # even if the binding/layer has changed since the press.
        if logical in _MACRO_THREADS:
            th, cancel, meta = _MACRO_THREADS.get(logical)
            m_mode = (meta or {}).get("mode")
            m_stop = bool((meta or {}).get("stop_on_release"))
            # Normalize in case older config/UI stored labels like "While held"
            m_mode_n = str(m_mode or "").strip().lower().replace(" ", "_")
            if m_mode_n == "whileheld":
                m_mode_n = "while_held"
            if pressed:
                if m_mode_n == "toggle":
                    cancel.set()
                    _MACRO_THREADS.pop(logical, None)
                    return True
            else:
                if m_mode_n == "while_held" or m_stop:
                    cancel.set()
                    _MACRO_THREADS.pop(logical, None)
                    return True

        binding = resolve_binding(logical)

        if not binding:
            if logical == "middle_click":
                self._emit_middle_passthrough(pressed)
                return True
            return False

        t = binding.get("type")

        if t == "special":
            act = str(binding.get("action") or "")
            if act == "cycle_subprofile":
                if pressed:
                    self._cycle_subprofile()
                return True
            return True

        if t in (None, "", "passthrough"):
            if logical == "middle_click":
                self._emit_middle_passthrough(pressed)
                return True
            return False

        if t == "keyboard":
            kcs = _binding_keycodes(binding)
            if kcs:
                # Repeat mode: emit press+release pulses while held
                if self._repeat_enabled(binding):
                    if pressed:
                        self._emit_key_combo(kcs, True)
                        self._emit_key_combo(kcs, False)
                        self._start_repeat(logical, binding)
                    else:
                        self._stop_repeat(logical)
                    return True

                self._emit_key_combo(kcs, pressed)
                return True
            if logical == "middle_click":
                self._emit_middle_passthrough(pressed)
                return True
            return False

        if t == "special":
            act = str(binding.get("action") or "")
            if act == "cycle_subprofile":
                if pressed:
                    self._cycle_subprofile()
                return True
            return True


        if t == "macro":
            name = binding.get("macro") or binding.get("name")
            if not isinstance(name, str) or not name:
                return False
            mobj = _get_macro(name)
            if not mobj:
                return False

            mode, _n = _macro_repeat_mode(mobj)
            stop_on_release = _macro_stop_on_release(mobj)

            if pressed:
                # Normal re-entry guard (except toggle handled at top of handle_logical)
                if _macro_should_block_reentry(mobj) and logical in _MACRO_THREADS:
                    return True

                cancel = threading.Event()
                th = threading.Thread(
                    target=_run_macro_thread,
                    args=(self, logical, mobj, cancel),
                    daemon=True,
                )
                _MACRO_THREADS[logical] = (
                    th,
                    cancel,
                    {"mode": mode, "stop_on_release": stop_on_release},
                )
                th.start()
                return True

            # released: handled at top of handle_logical
            return True

        if t == "mouse":
            bc = _binding_button_code(binding)
            if bc is not None:
                if self._repeat_enabled(binding):
                    if pressed:
                        self._emit_button(bc, True)
                        self._emit_button(bc, False)
                        self._start_repeat(logical, binding)
                    else:
                        self._stop_repeat(logical)
                    return True

                self._emit_button(bc, pressed)
                return True
            if logical == "middle_click":
                self._emit_middle_passthrough(pressed)
                return True
            return False

        if logical == "middle_click":
            self._emit_middle_passthrough(pressed)
            return True
        return False


    def handle_keyboard_key(self, key_name: str, pressed: bool) -> bool:
        """Handle a physical keyboard key event when --grab-keyboard is enabled.

        Returns True if the original event should be swallowed.
        """
        if not isinstance(key_name, str) or not key_name.startswith("KEY_"):
            return False

        logical = f"kbd:{key_name}"

        # Honor macro stop/toggle semantics if already running on this key
        if logical in _MACRO_THREADS:
            th, cancel, meta = _MACRO_THREADS.get(logical)
            m_mode = (meta or {}).get("mode")
            m_stop = bool((meta or {}).get("stop_on_release"))
            m_mode_n = str(m_mode or "").strip().lower().replace(" ", "_")
            if m_mode_n == "whileheld":
                m_mode_n = "while_held"
            if pressed:
                if m_mode_n == "toggle":
                    cancel.set()
                    _MACRO_THREADS.pop(logical, None)
                    return True
            else:
                if m_mode_n == "while_held" or m_stop:
                    cancel.set()
                    _MACRO_THREADS.pop(logical, None)
                    return True

        binding = resolve_keyboard_binding(key_name)

        if not binding:
            # Global hotkeys (apply to all profiles unless overridden)
            try:
                gh = (self.cfg.get("global_hotkeys") or {})
                gh_kb = (gh.get("keyboard_bindings") or {})
                gh_layer = (gh_kb.get(layer_name) or {})
                binding = gh_layer.get(key_name)
            except Exception:
                binding = None
        if not binding:
            return False

        t = binding.get("type")

        if t in (None, "", "passthrough"):
            return False

        if t == "keyboard":
            kcs = _binding_keycodes(binding)
            if not kcs:
                return False

            if self._repeat_enabled(binding):
                if pressed:
                    # immediate pulse + start repeat thread
                    self._emit_key_combo(kcs, True)
                    self._emit_key_combo(kcs, False)
                    self._start_repeat(logical, binding)
                else:
                    self._stop_repeat(logical)
                return True

            self._emit_key_combo(kcs, pressed)
            return True

        if t == "special":
            act = str(binding.get("action") or "")
            if act == "cycle_subprofile":
                if pressed:
                    self._cycle_subprofile()
                return True
            return True

        if t == "macro":
            name = binding.get("macro") or binding.get("name")
            if not isinstance(name, str) or not name:
                return False
            mobj = _get_macro(name)
            if not mobj:
                return False

            mode, _n = _macro_repeat_mode(mobj)
            stop_on_release = _macro_stop_on_release(mobj)

            if pressed:
                if _macro_should_block_reentry(mobj) and logical in _MACRO_THREADS:
                    return True

                cancel = threading.Event()
                th = threading.Thread(
                    target=_run_macro_thread,
                    args=(self, logical, mobj, cancel),
                    daemon=True,
                )
                _MACRO_THREADS[logical] = (
                    th,
                    cancel,
                    {"mode": mode, "stop_on_release": stop_on_release},
                )
                th.start()
                return True
            return True

        return False

    def passthrough_rel(self, ev) -> None:
        global POINTER_SCALE, SCROLL_SCALE
        # Any movement counts as activity
        self.rgb_idle.notify_activity()

        # Scale X/Y and wheel speeds (including hi-res wheel events when available)
        if ev.code in (ecodes.REL_X, ecodes.REL_Y):
            s = POINTER_SCALE
            if s != 1.0:
                if ev.code == ecodes.REL_X:
                    v = ev.value * s + self._rem_x
                    iv = int(round(v))
                    self._rem_x = v - iv
                    val = iv
                else:
                    v = ev.value * s + self._rem_y
                    iv = int(round(v))
                    self._rem_y = v - iv
                    val = iv
                self.ui.write(ecodes.EV_REL, ev.code, val)
                self._syn()
                return

        # Scale wheel (normal + hi-res)
        wheel_hi = getattr(ecodes, "REL_WHEEL_HI_RES", None)
        hwheel_hi = getattr(ecodes, "REL_HWHEEL_HI_RES", None)

        if ev.code in (ecodes.REL_WHEEL, ecodes.REL_HWHEEL, wheel_hi, hwheel_hi):
            s = SCROLL_SCALE
            if s != 1.0:
                if ev.code == ecodes.REL_WHEEL:
                    v = ev.value * s + self._rem_wheel
                    iv = int(round(v))
                    self._rem_wheel = v - iv
                    val = iv
                elif ev.code == ecodes.REL_HWHEEL:
                    v = ev.value * s + self._rem_hwheel
                    iv = int(round(v))
                    self._rem_hwheel = v - iv
                    val = iv
                elif wheel_hi is not None and ev.code == wheel_hi:
                    v = ev.value * s + self._rem_wheel_hi
                    iv = int(round(v))
                    self._rem_wheel_hi = v - iv
                    val = iv
                else:  # hwheel_hi
                    v = ev.value * s + self._rem_hwheel_hi
                    iv = int(round(v))
                    self._rem_hwheel_hi = v - iv
                    val = iv

                # Don't emit zero (avoid pointless SYN storms)
                if val != 0:
                    self.ui.write(ecodes.EV_REL, ev.code, val)
                    self._syn()
                return

        self.ui.write(ecodes.EV_REL, ev.code, ev.value)
        self._syn()

    def passthrough_key(self, ev) -> None:
        # Any key counts as activity
        if ev.value in (0, 1):
            self.rgb_idle.notify_activity()
        # Modifier tracking (if a modifier key somehow comes through these devices)
        try:
            name = ecodes.KEY.get(ev.code)
            if name:
                for m, names in MODIFIER_KEYCODES.items():
                    if name in names:
                        old = MODIFIER_STATE.get(m)
                        MODIFIER_STATE[m] = bool(ev.value)
                        if DEBUG and old != MODIFIER_STATE[m]:
                            print(f"[MOD] {m}={MODIFIER_STATE[m]} via {name}")
                        break
        except Exception:
            pass
        self.ui.write(ecodes.EV_KEY, ev.code, ev.value)
        self._syn()

    def run(self) -> None:
        global RUNNING

        if self.grab:
            for n in self.nodes:
                try:
                    n.grab()
                except Exception as e:
                    print(f"[WARN] failed to grab {n.path}: {e}", file=sys.stderr)
            print("Grab: enabled")
        else:
            print("Grab: disabled (raw F-keys will still reach apps)")

        print(
            f"[LOAD] active_profile={CONFIG.get('active_profile','default')} pointer_scale={POINTER_SCALE} scroll_scale={SCROLL_SCALE}"
        )
        devs_by_fd = {d.fd: d for d in (self.nodes + getattr(self, "kb_nodes", []))}
        print("RUNNING. Ctrl+C to quit.")

        try:
            while RUNNING:
                # Auto-switch on a timer (independent of mouse events)
                try:
                    autoswitch_tick(self.rgb_worker)
                except Exception as _e:
                    if DEBUG_AUTO:
                        print(f"[AUTO] tick failed: {_e}", file=sys.stderr)

                r, _, _ = select.select(list(devs_by_fd.keys()), [], [], 0.25)
                for fd in r:
                    dev = devs_by_fd[fd]
                    is_kb = hasattr(self, "kb_nodes") and dev in self.kb_nodes
                    try:
                        _events = dev.read()
                    except OSError as e:
                        if getattr(e, 'errno', None) == 19:
                            print("[WARN] input device vanished: %s (ENODEV). Exiting so it can be restarted." % getattr(dev, 'path', '?'), file=sys.stderr)
                            raise SystemExit(1)
                        raise
                    for ev in _events:
                        if ev.type == ecodes.EV_REL:
                            self.passthrough_rel(ev)
                            continue

                        if ev.type != ecodes.EV_KEY:
                            continue

                        if ev.value not in (0, 1):
                            continue

                        pressed = ev.value == 1

                        if is_kb:
                            # Keyboard node: update modifier state always
                            name = ecodes.KEY.get(ev.code) or ecodes.BTN.get(ev.code) or ""
                            up = str(name).upper() if name else ""
                            if up:
                                pressed_mod = pressed
                                if "SHIFT" in up:
                                    old = MODIFIER_STATE.get("shift")
                                    MODIFIER_STATE["shift"] = pressed_mod
                                    if DEBUG and old != MODIFIER_STATE["shift"]:
                                        print(f"[MOD] shift={MODIFIER_STATE['shift']} via {name}")
                                elif "CTRL" in up:
                                    old = MODIFIER_STATE.get("ctrl")
                                    MODIFIER_STATE["ctrl"] = pressed_mod
                                    if DEBUG and old != MODIFIER_STATE["ctrl"]:
                                        print(f"[MOD] ctrl={MODIFIER_STATE['ctrl']} via {name}")
                                elif "ALT" in up:
                                    old = MODIFIER_STATE.get("alt")
                                    MODIFIER_STATE["alt"] = pressed_mod
                                    if DEBUG and old != MODIFIER_STATE["alt"]:
                                        print(f"[MOD] alt={MODIFIER_STATE['alt']} via {name}")

                            if getattr(self, "grab_keyboard", False):
                                # Panic combo (default: KEY_RIGHTCTRL + KEY_RIGHTALT + KEY_BACKSPACE)
                                if up in self._panic_state:
                                    self._panic_state[up] = pressed
                                if self._panic_state.get("KEY_RIGHTCTRL") and self._panic_state.get("KEY_RIGHTALT") and self._panic_state.get("KEY_BACKSPACE"):
                                    print("[PANIC] RCTRL+RALT+BACKSPACE pressed; exiting mapper.")
                                    RUNNING = False
                                    break

                                # Swallow modifiers by default so they act as layer selectors only
                                if (("SHIFT" in up) or ("CTRL" in up) or ("ALT" in up)) and not getattr(self, "passthrough_modifiers", False):
                                    continue

                                if name and str(name).startswith("KEY_"):
                                    handled = self.handle_keyboard_key(str(name), pressed)
                                    if handled:
                                        continue

                                # Not handled -> passthrough so keyboard still works while grabbed
                                if self.debug:
                                    nm = ecodes.KEY.get(ev.code) or str(ev.code)
                                    print(f"[DBG] kb passthrough {nm} value={ev.value}")
                                self.passthrough_key(ev)
                                continue

                            # Default mode: only track modifiers; don't re-emit keyboard events
                            continue

                        # Naga can emit extended function keys (F13-F24) on its KEY node.
                        # Route those through keyboard bindings (e.g. KEY_F24 -> cycle_subprofile).
                        if ev.code in (
                            ecodes.KEY_F13, ecodes.KEY_F14, ecodes.KEY_F15, ecodes.KEY_F16,
                            ecodes.KEY_F17, ecodes.KEY_F18, ecodes.KEY_F19, ecodes.KEY_F20,
                            ecodes.KEY_F21, ecodes.KEY_F22, ecodes.KEY_F23, ecodes.KEY_F24,
                        ):
                            key_name = ecodes.KEY.get(ev.code)
                            if key_name and str(key_name).startswith("KEY_"):
                                if self.handle_keyboard_key(str(key_name), pressed):
                                    continue

                        logical = None
                        if ev.type == ecodes.EV_KEY and ev.code in self._side_btn_codes:
                            logical = self._map_side_button(ev.code)
                        if logical is None:
                            logical = self.physical_to_logical.get((ev.type, ev.code))
                        if logical:
                            swallowed = self.handle_logical(logical, pressed)
                            if swallowed:
                                continue
                        else:
                            # Unmapped KEY_* events coming from the Naga's keyboard-like interface (e.g. KEY_F24)
                            # should still be eligible for keyboard_bindings (special/macro/keyboard).
                            name = ecodes.KEY.get(ev.code) or ""
                            if name and str(name).startswith("KEY_"):
                                if self.handle_keyboard_key(str(name), pressed):
                                    continue

                        if self.debug:
                            nm = (
                                ecodes.KEY.get(ev.code)
                                or ecodes.BTN.get(ev.code)
                                or str(ev.code)
                            )
                            print(f"[DBG] passthrough key {nm} value={ev.value}")

                        self.passthrough_key(ev)

                # idle checks (no blocking)
                self.rgb_idle.tick()

        except KeyboardInterrupt:
            RUNNING = False
        finally:
            try:
                self.rgb_worker.stop()
            except Exception:
                pass
            try:
                self.ui.close()
            except Exception:
                pass


def shutdown_from_signal(signum, frame) -> None:
    global RUNNING
    RUNNING = False


def main() -> None:
    global DEBUG

    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    ap.add_argument("--grab", action="store_true")
    ap.add_argument("--grab-keyboard", action="store_true", help="Grab keyboard devices and apply keyboard_bindings remaps/macros.")
    ap.add_argument("--passthrough-modifiers", action="store_true", help="When grabbing keyboard, also re-emit modifier keys (Ctrl/Alt/Shift). Default keeps them internal for layer selection.")
    ap.add_argument("--panic-combo", default="RCTRL+RALT+BACKSPACE", help="Panic combo to immediately exit when --grab-keyboard is enabled. Default: RCTRL+RALT+BACKSPACE")
    ap.add_argument("--pidfile", default="")
    ap.add_argument("--debug", action="store_true")
    args = ap.parse_args()

    DEBUG = bool(args.debug)

    cfg_path = os.path.abspath(os.path.expanduser(args.config))

    # Migration/fallback: if the new config path does not exist yet, fall back to the legacy
    # synapse-lite config and (best-effort) seed the new location.
    if not os.path.exists(cfg_path):
        legacy_path = os.path.expanduser(f"~/.config/{LEGACY_APP_ID}/config.json")
        if os.path.exists(legacy_path):
            try:
                os.makedirs(os.path.dirname(cfg_path), exist_ok=True)
                # Keep legacy as backup; seed the new location.
                shutil.copy2(legacy_path, cfg_path)
            except Exception:
                # If we can't seed, still run from legacy so input still works.
                cfg_path = legacy_path
        # else: leave as-is; load_config will create defaults as needed.

    load_config(cfg_path)


    signal.signal(signal.SIGHUP, reload_from_signal)
    signal.signal(signal.SIGTERM, shutdown_from_signal)
    signal.signal(signal.SIGINT, shutdown_from_signal)

    if args.pidfile:
        write_pidfile(args.pidfile)

    m = Mapper(grab=bool(args.grab), debug=DEBUG, grab_keyboard=bool(args.grab_keyboard), passthrough_modifiers=bool(args.passthrough_modifiers), panic_combo=args.panic_combo)
    m.run()

    if args.pidfile:
        remove_file(args.pidfile)


if __name__ == "__main__":
    main()