# macro_store.py
import copy
import uuid

DEFAULT_MACRO = {
    "id": "",
    "name": "New Macro",
    "folder": "",
    "steps": [],
    "stop_mode": "on_release",
    "timing": {"mode": "recorded", "fixed_ms": 50},
    "repeat": {"mode": "none", "count": 1, "gap_ms": 0},
    "no_overlap": True,
}


def normalize_macro(m: dict) -> dict:
    if not m:
        m = {}

    out = copy.deepcopy(DEFAULT_MACRO)
    out.update(m)

    if not out.get("id"):
        out["id"] = str(uuid.uuid4())

    # nested defaults
    out.setdefault("timing", {})
    out["timing"].setdefault("mode", "recorded")
    out["timing"].setdefault("fixed_ms", 50)

    out.setdefault("repeat", {})
    out["repeat"].setdefault("mode", "none")
    out["repeat"].setdefault("count", 1)
    out["repeat"].setdefault("gap_ms", 0)

    out.setdefault("steps", [])

    return out



def migrate_macros_to_global(cfg: dict, clear_profile_macros: bool = False) -> None:
    """Backward compatible: merge profiles[*].macros into cfg['macros']."""
    if not isinstance(cfg, dict):
        return
    cfg.setdefault("macros", {})
    if not isinstance(cfg.get("macros"), dict):
        cfg["macros"] = {}
    g = cfg["macros"]
    profiles = cfg.get("profiles") or {}
    if not isinstance(profiles, dict):
        return

    def unique_name(base: str) -> str:
        if base not in g:
            return base
        n = 2
        while f"{base}__{n}" in g:
            n += 1
        return f"{base}__{n}"

    for prof, pobj in profiles.items():
        if not isinstance(pobj, dict):
            continue
        pm = pobj.get("macros")
        if not isinstance(pm, dict) or not pm:
            continue
        for name, macro in pm.items():
            if not isinstance(macro, dict):
                continue
            new_name = name
            if new_name in g and g.get(new_name) != macro:
                new_name = unique_name(f"{prof}/{name}")
            else:
                new_name = unique_name(new_name)
            g[new_name] = macro
        if clear_profile_macros:
            pobj["macros"] = {}



class MacroStore:
    def __init__(self, cfg: dict):
        self.cfg = cfg

    # ---------- helpers ----------
    def _profile_obj(self, profile: str) -> dict:
        # profiles still exist for bindings/settings, but macros are global
        return self.cfg.setdefault("profiles", {}).setdefault(profile, {})

    def _macros_dict(self, profile: str) -> dict:
        migrate_macros_to_global(self.cfg)
        return self.cfg.setdefault("macros", {})

    # ---------- public ----------
    def list_macros(self, profile: str) -> list[dict]:
        macros = []
        for name, m in self._macros_dict(profile).items():
            m2 = normalize_macro(m)
            m2["name"] = name
            macros.append(m2)
        return macros

    def get_macro(self, profile: str, name: str) -> dict | None:
        m = self._macros_dict(profile).get(name)
        if not m:
            return None
        m2 = normalize_macro(m)
        m2["name"] = name
        return m2

    def upsert_macro(self, profile: str, macro: dict):
        macro = normalize_macro(macro)
        name = macro["name"]

        # rename handling
        macros = self._macros_dict(profile)
        for k, v in list(macros.items()):
            if v.get("id") == macro["id"] and k != name:
                del macros[k]

        macros[name] = macro

    def delete_macro(self, profile: str, name: str):
        self._macros_dict(profile).pop(name, None)

    def find_by_id(self, profile: str, mid: str) -> dict | None:
        for name, m in self._macros_dict(profile).items():
            if m.get("id") == mid:
                m2 = normalize_macro(m)
                m2["name"] = name
                return m2
        return None
