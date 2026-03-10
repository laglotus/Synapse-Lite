#!/usr/bin/env python3
import argparse
import json
import copy
import os
import signal
import sys
import subprocess
import threading
import select
import tarfile
import time
import shlex
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import unicodedata

def _synapse_name_sort_key(name: str) -> str:
    """Sort key that is stable and reasonably Finnish-friendly.
    Pushes Å/Ä/Ö after Z (approx Finnish collation) and normalizes accents."""
    if name is None:
        return ""
    s = str(name).strip().casefold()
    # Finnish ordering places Å/Ä/Ö after Z. Approximate by pushing them to the end.
    s = s.replace("å", "zzza").replace("ä", "zzzb").replace("ö", "zzzc")
    # Remove other diacritics for predictable ordering
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    return s


def _apply_premium_shadow(w) -> None:
    """Adds a subtle drop shadow (premium look) without a visible frame."""
    try:
        eff = QtWidgets.QGraphicsDropShadowEffect(w)
        eff.setBlurRadius(22)
        eff.setOffset(0, 6)
        eff.setColor(QtGui.QColor(0, 0, 0, 110))
        w.setGraphicsEffect(eff)
    except Exception:
        pass



from PySide6 import QtCore, QtGui, QtWidgets



class _OverlayToolTip(QtWidgets.QFrame):
    """Custom tooltip for overlay clickzones (avoids platform/native tooltip theming quirks)."""
    def __init__(self):
        super().__init__(None, QtCore.Qt.ToolTip | QtCore.Qt.FramelessWindowHint)
        self.setAttribute(QtCore.Qt.WA_ShowWithoutActivating, True)
        self.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents, True)
        self._label = QtWidgets.QLabel(self)
        self._label.setWordWrap(False)
        lay = QtWidgets.QHBoxLayout(self)
        lay.setContentsMargins(8, 5, 8, 5)
        lay.addWidget(self._label)

        # Match the bindings-row highlight vibe: teal tint, slightly transparent, white text.
        self.setStyleSheet(
            "QFrame{"
            "background: rgba(33,169,194,100);"
            "border: 1px solid rgba(33,169,194,230);"
            "border-radius: 4px;"
            "}"
            "QLabel{color: #fff;}"
        )

    def setText(self, text: str) -> None:
        self._label.setText(text or "")
        self.adjustSize()

    def showAtCursor(self, offset: QtCore.QPoint = QtCore.QPoint(14, 18)) -> None:
        pos = QtGui.QCursor.pos() + offset
        self.move(pos)
        self.show()


# -------------------------
# Clickable panel preview overlay
# -------------------------

class ClickablePanelPreview(QtWidgets.QWidget):
    """Shows a panel image with transparent clickable hotspots on top."""
    actionClicked = QtCore.Signal(str)

    actionHovered = QtCore.Signal(str)
    actionUnhovered = QtCore.Signal(str)
    def __init__(self, parent=None):
        super().__init__(parent)
        self._pix = QtGui.QPixmap()
        self._layout = "6"  # "2" | "6" | "12" | "top"
        self._buttons: Dict[str, QtWidgets.QPushButton] = {}
        self._action_label_map: Dict[str, str] = {}

        self.label = QtWidgets.QLabel(self)
        self.label.setAlignment(QtCore.Qt.AlignCenter)
        self.label.setStyleSheet("background: transparent;")
        self.label.setScaledContents(False)

        # If you want visual debugging (see rectangles), set True:
        self.debug_boxes = False

        self._overlay_tip = _OverlayToolTip()

        self.debug_boxes = False

        self.setMinimumHeight(int(220 * 1.7))

    def set_layout(self, layout: str) -> None:
        self._layout = str(layout or "6")
        self._rebuild_buttons()

    def set_action_label_map(self, m: Dict[str, str]) -> None:
        """Provide action_key -> user-facing label for overlay tooltips."""
        try:
            self._action_label_map = dict(m or {})
        except Exception:
            self._action_label_map = {}


    def set_pixmap(self, pm: QtGui.QPixmap) -> None:
        self._pix = pm if pm else QtGui.QPixmap()
        self._update_scaled_pixmap()
        self._reposition()

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        self.label.setGeometry(self.rect())
        self._update_scaled_pixmap()
        self._reposition()

    def _update_scaled_pixmap(self):
        if self._pix.isNull():
            self.label.setPixmap(QtGui.QPixmap())
            return
        target = self.label.size()
        scaled = self._pix.scaled(target, QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation)
        self.label.setPixmap(scaled)

    def _pixmap_draw_rect(self) -> QtCore.QRect:
        """Return the rect (widget coords) where the scaled pixmap is drawn."""
        pm = self.label.pixmap()
        if pm is None or pm.isNull():
            return self.rect()
        w = self.label.width()
        h = self.label.height()
        pw = pm.width()
        ph = pm.height()
        x = (w - pw) // 2
        y = (h - ph) // 2
        return QtCore.QRect(x, y, pw, ph)

    def _rebuild_buttons(self):
        for b in self._buttons.values():
            b.deleteLater()
        self._buttons.clear()

        overlays = PANEL_OVERLAYS.get(self._layout, {})
        for action_key in overlays.keys():
            btn = QtWidgets.QPushButton(self)
            btn.setFlat(True)
            btn.setFocusPolicy(QtCore.Qt.NoFocus)
            btn.setCursor(QtGui.QCursor(QtCore.Qt.PointingHandCursor))

            if self.debug_boxes:
                btn.setStyleSheet("background: rgba(255,0,0,40); border: 1px solid rgba(255,0,0,120);")
            else:
                btn.setStyleSheet("QPushButton{background: transparent; border: 2px solid rgba(0,0,0,0); border-radius: 999px;}""QPushButton:hover{background: rgba(0,255,0,35); border-color: rgba(0,255,0,120);}""QPushButton[hl=\"true\"]{background: rgba(0,255,0,55); border-color: rgba(0,255,0,200);}")

            btn.clicked.connect(lambda _=None, ak=action_key: self.actionClicked.emit(ak))
            btn.setProperty('action_key', action_key)
            btn.installEventFilter(self)
            btn.setMouseTracking(True)
            btn.setProperty('hl','false')
            btn.setToolTip("")  # use custom tooltip widget
            self._buttons[action_key] = btn

        self._reposition()


    def eventFilter(self, obj, ev):
        try:
            if isinstance(obj, QtWidgets.QPushButton):
                ak = obj.property("action_key")
                if ak:
                    et = ev.type()
                    if et == QtCore.QEvent.Enter:
                        self.actionHovered.emit(str(ak))
                        # Custom tooltip (instant + themed)
                        try:
                            self._overlay_tip.setText(self._action_label_map.get(str(ak), str(ak)))
                            self._overlay_tip.showAtCursor()
                        except Exception:
                            pass
                    elif et == QtCore.QEvent.Leave:
                        self.actionUnhovered.emit(str(ak))
                        try:
                            self._overlay_tip.hide()
                        except Exception:
                            pass
                    elif et == QtCore.QEvent.MouseMove:
                        # Keep tooltip tracking cursor while hovering within the clickzone
                        try:
                            if self._overlay_tip.isVisible():
                                self._overlay_tip.showAtCursor()
                        except Exception:
                            pass
        except Exception:
            pass
        return super().eventFilter(obj, ev)



    def set_highlight(self, action_key: str | None) -> None:
        """Persistently highlight a hotspot (in addition to :hover)."""
        for ak, btn in self._buttons.items():
            btn.setProperty("hl", "true" if (action_key and str(ak) == str(action_key)) else "false")
            try:
                btn.setStyleSheet(btn.styleSheet())
                btn.update()
            except Exception:
                pass

    def _reposition(self):
        draw = self._pixmap_draw_rect()
        overlays = PANEL_OVERLAYS.get(self._layout, {})

        for action_key, btn in self._buttons.items():
            rn = overlays.get(action_key)
            if not rn:
                btn.hide()
                continue

            x, y, w, h = rn
            px = int(draw.x() + x * draw.width())
            py = int(draw.y() + y * draw.height())
            pw = int(w * draw.width())
            ph = int(h * draw.height())

            btn.setGeometry(px, py, pw, ph)

            # Make hover/highlight shape match the physical button:
            # - Thumb buttons and wheel tilt: ellipse mask
            # - DPI rockers: keep rectangular (no mask)
            try:
                if self._layout in ("2","6","12"):
                    if action_key not in ("dpi_up","dpi_down"):
                        # Use an ellipse mask slightly inset so the border isn't clipped.
                        r = QtCore.QRect(0, 0, pw, ph).adjusted(1, 1, -1, -1)
                        btn.setMask(QtGui.QRegion(r, QtGui.QRegion.Ellipse))
                    else:
                        btn.clearMask()
                elif self._layout == "top":
                    if action_key in ("wheel_tilt_left","wheel_tilt_right","middle_click"):
                        r = QtCore.QRect(0, 0, pw, ph).adjusted(1, 1, -1, -1)
                        btn.setMask(QtGui.QRegion(r, QtGui.QRegion.Ellipse))
                    elif action_key in ("dpi_up", "dpi_down"):
                        # Rounded pill so the hover/outline matches the rocker shape
                        r = QtCore.QRect(0, 0, pw, ph).adjusted(1, 1, -1, -1)
                        rad = max(2, min(r.width(), r.height()) // 2)
                        path = QtGui.QPainterPath()
                        path.addRoundedRect(QtCore.QRectF(r), rad, rad)
                        poly = path.toFillPolygon().toPolygon()
                        btn.setMask(QtGui.QRegion(poly))
                    else:
                        btn.clearMask()
                else:
                    btn.clearMask()
            except Exception:
                # Never let masking kill the UI
                try:
                    btn.clearMask()
                except Exception:
                    pass

            btn.show()



# -------------------------
# Clickable keyboard preview overlay (for Keyboard tab)
# -------------------------

class ClickableKeyboardPreview(QtWidgets.QWidget):
    """Shows a keyboard layout image with transparent clickable key hotspots."""
    keyClicked = QtCore.Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pix = QtGui.QPixmap()
        self._buttons: Dict[str, QtWidgets.QPushButton] = {}
        self._hotspots: Dict[str, Tuple[float, float, float, float]] = {}

        self.label = QtWidgets.QLabel(self)
        self.label.setAlignment(QtCore.Qt.AlignCenter)
        self.label.setStyleSheet("background: transparent;")
        self.label.setScaledContents(False)

        self.setMouseTracking(True)
        self.label.setMouseTracking(True)

    def sizeHint(self) -> QtCore.QSize:
        # Keep this widget reasonably sized so it doesn't force the whole window wider.
        # The pixmap itself is scaled to fit whatever space we get.
        return QtCore.QSize(900, 320)

    def set_pixmap(self, pm: QtGui.QPixmap) -> None:
        self._pix = pm if pm else QtGui.QPixmap()
        self._update_scaled_pixmap()
        self._reposition()

    def set_hotspots(self, hotspots: Dict[str, Tuple[float, float, float, float]]) -> None:
        self._hotspots = dict(hotspots or {})
        self._rebuild_buttons()

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        self.label.setGeometry(self.rect())
        self._update_scaled_pixmap()
        self._reposition()

    def _update_scaled_pixmap(self):
        if self._pix.isNull():
            self.label.setPixmap(QtGui.QPixmap())
            return
        target = self.label.size()
        scaled = self._pix.scaled(target, QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation)
        self.label.setPixmap(scaled)

    def _pixmap_draw_rect(self) -> QtCore.QRect:
        pm = self.label.pixmap()
        if pm is None or pm.isNull():
            return self.rect()
        w = self.label.width()
        h = self.label.height()
        pw = pm.width()
        ph = pm.height()
        x = (w - pw) // 2
        y = (h - ph) // 2
        return QtCore.QRect(x, y, pw, ph)

    def _rebuild_buttons(self):
        for b in self._buttons.values():
            b.deleteLater()
        self._buttons.clear()

        for key_name in self._hotspots.keys():
            btn = QtWidgets.QPushButton(self)
            btn.setFlat(True)
            btn.setFocusPolicy(QtCore.Qt.NoFocus)
            btn.setCursor(QtGui.QCursor(QtCore.Qt.PointingHandCursor))
            btn.setStyleSheet(
                "QPushButton{background: transparent; border: 2px solid rgba(0,0,0,0); border-radius: 999px;}"
                "QPushButton:hover{background: rgba(0,255,0,35); border-color: rgba(0,255,0,120);}"
                "QPushButton[hl=\"true\"]{background: rgba(0,255,0,55); border-color: rgba(0,255,0,200);}"
            )
            btn.setProperty("key_name", key_name)
            btn.setProperty("hl", "false")
            btn.clicked.connect(lambda _=None, kn=key_name: self.keyClicked.emit(str(kn)))
            self._buttons[key_name] = btn

        self._reposition()

    def set_highlight(self, key_name: Optional[str]) -> None:
        for kn, btn in self._buttons.items():
            btn.setProperty("hl", "true" if (key_name and str(kn) == str(key_name)) else "false")
            try:
                btn.setStyleSheet(btn.styleSheet())
            except Exception:
                pass

    def _reposition(self):
        draw = self._pixmap_draw_rect()
        if draw.width() <= 0 or draw.height() <= 0:
            return
        for kn, btn in self._buttons.items():
            rn = self._hotspots.get(kn)
            if not rn:
                btn.hide()
                continue
            x, y, w, h = rn
            px = int(draw.x() + x * draw.width())
            py = int(draw.y() + y * draw.height())
            pw = int(w * draw.width())
            ph = int(h * draw.height())
            btn.setGeometry(px, py, pw, ph)
            btn.show()

# Normalized overlay rectangles (x, y, w, h) relative to the image area (0..1)
DEFAULT_PANEL_OVERLAYS: Dict[str, Dict[str, tuple]] = {
    # Side panel overlays (tuned to your numbered-circle PNGs)
    "2": {
        "mb5": (0.5766109627485275, 0.24547499552456667, 0.07900468468666078, 0.19013113306496035),
        "mb4": (0.42153087094426156, 0.28401318165726447, 0.08799296945333479, 0.21176216385479396),
    },
    "6": {
        "top_row_left":   (0.39064729437232015, 0.28301482508801684, 0.08882265836000447, 0.21375887699328913),
        "top_row_middle": (0.48075745552778243, 0.2615642865872411,  0.07969609797000882, 0.19179507478563823),
        "top_row_right":  (0.5676836158335209,  0.2507826116362414,  0.07928125351667403, 0.19079671821639063),

        "thumb6_bottom_left":   (0.41796017430722715, 0.48693587647201597, 0.07872812464833256, 0.18946556907142784),
        "thumb6_bottom_middle": (0.5055051268637181,  0.4720028586113075,  0.0794195291399955,  0.19112948963420795),
        "bottom_row_right":     (0.5934648896753788,  0.44537721340300473, 0.07928125351667403, 0.19079671821639063),
    },
    "12": {
        "thumb12_top_left":         (0.41251563709229233, 0.2594970913807458,  0.07672656215727325, 0.18464864782384927),
        "thumb12_top_middle_left":  (0.48913872092962263, 0.2295605346823131,  0.07699609100818638, 0.185297290698902),
        "thumb12_top_middle_right": (0.564475622214377,   0.213450081508112,   0.07632226459681979, 0.18367567320127748),
        "thumb12_top_right":        (0.6422705333679914,  0.21876616875797544, 0.07659179344773293, 0.18432431607633024),

        "thumb12_middle_left":         (0.4216211099177599,  0.42383080470155743, 0.07726562842726703, 0.18594595419394),
        "thumb12_middle_middle_left":  (0.4981973122805357,  0.40506231305742096, 0.07645703330636028, 0.1840000049487964),
        "thumb12_middle_middle_right": (0.5736484495922923,  0.3892268139383067,  0.07672656215727325, 0.18464864782384927),
        "thumb12_middle_right":        (0.6498877089470625,  0.3893889798120662,  0.07659179344773293, 0.18432431607633024),

        "thumb12_bottom_left":         (0.4284707259386778,  0.5926063860345251,  0.07645703330636028, 0.1840000049487964),
        "thumb12_bottom_middle_left":  (0.503854538500309,   0.5780188411439208,  0.07686133086681357, 0.1849729795713683),
        "thumb12_bottom_middle_right": (0.5773164168000221,  0.5503455013001708,  0.0869687527418137,  0.20929730389569262),
        "thumb12_bottom_right":        (0.6505586043000221,  0.5503455013001708,  0.0869687527418137,  0.20929730389569262),
    },

    # Top view (panel_top.png) hotspots:
    "top": {
        "wheel_tilt_left":  (0.29424157303370785, 0.1923828125, 0.11938202247191011, 0.0830078125),
        "wheel_tilt_right": (0.5182584269662921,  0.1923828125, 0.11938202247191011, 0.0830078125),
        "middle_click":     (0.40870786516853935, 0.16845703125, 0.11938202247191011, 0.0830078125),
        "dpi_up":           (0.41151685393258425, 0.31396484375, 0.10533707865168539, 0.0732421875),
        "dpi_down":         (0.4160814606741573,  0.38525390625, 0.10533707865168539, 0.0732421875),
    },
}


def _load_panel_overlays(default: Dict[str, Dict[str, tuple]]) -> Dict[str, Dict[str, tuple]]:
    """Load mouse panel overlays from assets/mouse_panel_overlays.json.

    Expected format:
      {
        "2": { "mb4": [x,y,w,h], ... },
        "6": { ... },
        "12": { ... },
        "top": { ... }
      }
    All coordinates are normalized (0..1).
    """
    from pathlib import Path
    path = str(Path(__file__).resolve().parent / "assets" / "mouse_panel_overlays.json")
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        if not isinstance(raw, dict):
            return default

        out: Dict[str, Dict[str, tuple]] = {}
        for layout, mapping in raw.items():
            if not isinstance(layout, str) or not isinstance(mapping, dict):
                continue
            lay: Dict[str, tuple] = {}
            for action_key, rect in mapping.items():
                if not isinstance(action_key, str):
                    continue
                if (isinstance(rect, (list, tuple)) and len(rect) == 4 and all(isinstance(v, (int, float)) for v in rect)):
                    x, y, w, h = [float(v) for v in rect]
                    # basic sanity
                    if 0 <= x <= 1 and 0 <= y <= 1 and 0 <= w <= 1 and 0 <= h <= 1 and w > 0 and h > 0:
                        lay[action_key] = (x, y, w, h)
            if lay:
                out[layout] = lay

        # Only accept if it contains at least one known layout
        if any(k in out for k in ("2", "6", "12", "top")):
            return out
    except Exception:
        return default
    return default


# Runtime overlays (prefer JSON, fall back to defaults)
PANEL_OVERLAYS: Dict[str, Dict[str, tuple]] = _load_panel_overlays(DEFAULT_PANEL_OVERLAYS)


from PySide6.QtNetwork import QLocalServer, QLocalSocket

from evdev import InputDevice, list_devices, ecodes
from macro_editor import MacroEditor
from macro_store import migrate_macros_to_global

# ---------------- Assets + side button layout helpers ----------------
def _asset_path(filename: str) -> str:
    """Return absolute path to an asset in ./assets (or ./Assets) next to this script."""
    base = os.path.dirname(os.path.abspath(__file__))
    for folder in ("assets", "Assets"):
        p = os.path.join(base, folder, filename)
        if os.path.exists(p):
            return p
    return os.path.join(base, "assets", filename)

def _get_profile_obj(cfg: dict, profile_name: str) -> dict:
    profiles = cfg.setdefault("profiles", {})
    return profiles.setdefault(profile_name, {})

def _get_panel_layout(cfg: dict, profile_name: str) -> str:
    pobj = _get_profile_obj(cfg, profile_name)
    settings = pobj.setdefault("settings", {})
    v = str(settings.get("panel_layout", "6") or "6")
    return v if v in ("2", "6", "12") else "6"

def _set_panel_layout(cfg: dict, profile_name: str, layout: str) -> None:
    layout = str(layout or "6")
    if layout not in ("2", "6", "12"):
        layout = "6"
    pobj = _get_profile_obj(cfg, profile_name)
    settings = pobj.setdefault("settings", {})
    settings["panel_layout"] = layout


def _set_preview_mode(w, mode: str, layout: str | None = None) -> None:
    """Switch the bindings preview between side panel layouts and top buttons.

    - mode: 'side' or 'top'
    - layout: '2' | '6' | '12' when mode == 'side'
    """
    w.preview_mode = mode

    if mode == "top":
        w.preview_layout_override = None
    else:
        if layout is not None:
            w.preview_layout_override = str(layout)

    _sync_layout_buttons(w)
    _update_panel_preview(w)

    # Rebuild bindings list for the new view/layout
    if hasattr(w, "refresh_table"):
        w.refresh_table()

    if mode == "side" and layout:
        prof = w.current_profile()
        _set_panel_layout(w.cfg, prof, layout)

    _sync_layout_buttons(w)
    _update_panel_preview(w)


def _actions_order_for_layout(layout: str):
    """Return list of (action_key, label) for the bindings table.

    2-button and 6-button panels share the same two physical buttons.
    We expose distinct logical keys so bindings don't collide:
      - layout 2:  mb4 / mb5
      - layout 6:  thumb6_bottom_left / thumb6_bottom_middle

    12-button uses thumb12_* keys.
    """
    common = [
        ("middle_click", "Middle Click (Wheel Press)"),
        ("wheel_tilt_left", "Wheel Tilt Left"),
        ("wheel_tilt_right", "Wheel Tilt Right"),
        ("dpi_up", "DPI Up"),
        ("dpi_down", "DPI Down"),
    ]

    if layout == "2":
        side = [("mb4", "MB5"), ("mb5", "MB4")]
        return side + common

    if layout == "6":
        side = [
            ("thumb6_bottom_left", "Thumb Bottom-Right"),
            ("thumb6_bottom_middle", "Thumb Bottom-Middle"),
            ("bottom_row_right", "Thumb Bottom-Left"),
            ("top_row_left", "Thumb Top-Left"),
            ("top_row_middle", "Thumb Top-Middle"),
            ("top_row_right", "Thumb Top-Right"),
        ]
        return side + common

    if layout == "12":
        side = [
            ("thumb12_top_left", "Thumb Top-Left"),
            ("thumb12_top_middle_left", "Thumb Top-Middle-Left"),
            ("thumb12_top_middle_right", "Thumb Top-Middle-Right"),
            ("thumb12_top_right", "Thumb Top-Right"),
            ("thumb12_middle_left", "Thumb Middle-Left"),
            ("thumb12_middle_middle_left", "Thumb Middle-Middle-Left"),
            ("thumb12_middle_middle_right", "Thumb Middle-Middle-Right"),
            ("thumb12_middle_right", "Thumb Middle-Right"),
            ("thumb12_bottom_left", "Thumb Bottom-Left"),
            ("thumb12_bottom_middle_left", "Thumb Bottom-Middle-Left"),
            ("thumb12_bottom_middle_right", "Thumb Bottom-Middle-Right"),
            ("thumb12_bottom_right", "Thumb Bottom-Right"),
        ]
        return side + common

    return list(ACTIONS_ORDER)

def _sync_layout_buttons(w) -> None:
    """Sync panel preview mode buttons (Top/2/6/12)."""
    if not hasattr(w, "cfg") or not hasattr(w, "panel_btn_2"):
        return

    mode = getattr(w, "preview_mode", "side") or "side"

    # Top mode selected
    if hasattr(w, "panel_btn_top"):
        w.panel_btn_top.blockSignals(True)
        w.panel_btn_top.setChecked(mode == "top")
        w.panel_btn_top.blockSignals(False)

    # Side layout buttons selected when not in top mode
    prof = w.current_profile()
    layout = _get_panel_layout(w.cfg, prof)
    mapping = {"2": w.panel_btn_2, "6": w.panel_btn_6, "12": w.panel_btn_12}
    for k, btn in mapping.items():
        btn.blockSignals(True)
        btn.setChecked((mode != "top") and (k == layout))
        btn.blockSignals(False)


def _update_panel_preview(w) -> None:
    if not hasattr(w, "panel_preview") or not hasattr(w, "cfg"):
        return

    prof = w.current_profile()
    mode = getattr(w, "preview_mode", "side")

    if mode == "top":
        layout_key = "top"
        fname = "panel_top.png"
    else:
        layout = getattr(w, "preview_layout_override", None) or _get_panel_layout(w.cfg, prof)
        layout = str(layout)
        layout_key = layout
        fname = {"2": "panel_2.png", "6": "panel_6.png", "12": "panel_12.png"}.get(layout, "panel_6.png")

    pm = QtGui.QPixmap(_asset_path(fname))

    if pm.isNull():
        if hasattr(w.panel_preview, "set_pixmap"):
            w.panel_preview.set_layout(layout_key)
            w.panel_preview.set_pixmap(QtGui.QPixmap())
        else:
            w.panel_preview.setText(f"(Missing {fname})")
            w.panel_preview.setPixmap(QtGui.QPixmap())
        return

    if hasattr(w.panel_preview, "set_pixmap"):
        w.panel_preview.set_layout(layout_key)
        w.panel_preview.set_pixmap(pm)
    else:
        w.panel_preview.setText("")
        w.panel_preview.setPixmap(pm.scaledToHeight(272, QtCore.Qt.SmoothTransformation))


def _apply_layout_change(w, layout: str) -> None:
    prof = w.current_profile()
    _set_panel_layout(w.cfg, prof, layout)
    try:
        w.save_config()
    except Exception:
        pass
    try:
        w.refresh_table()
    except Exception:
        pass
    _sync_layout_buttons(w)
    _update_panel_preview(w)
APP_ID = "synapse-lite"
LEGACY_APP_IDS = ['razer-mouse-control-center', 'naga-synapse-lite']
SERVICE_NAME = "synapse-lite.service"
LEGACY_SERVICE_NAMES = ["razer-mouse-control-center.service", "naga-synapse-lite.service"]

ACTIONS_ORDER = [
    ("browse_backward", "Browse Back"),
    ("browse_forward", "Browse Forward"),
    ("middle_click", "Middle Click (Wheel Press)"),
    ("bottom_row_right", "Thumb Bottom-Right (F3)"),
    ("top_row_left", "Thumb Top-Left (F4)"),
    ("top_row_middle", "Thumb Top-Middle (F5)"),
    ("top_row_right", "Thumb Top-Right (F6)"),
    ("wheel_tilt_left", "Wheel Tilt Left (F8)"),
    ("wheel_tilt_right", "Wheel Tilt Right (F9)"),
    ("dpi_up", "DPI Up (F10)"),
    ("dpi_down", "DPI Down (F11)"),
]

MOUSE_OUTPUT_CHOICES = [
    "BTN_MIDDLE",
    "BTN_LEFT",
    "BTN_RIGHT",
    "BTN_SIDE",
    "BTN_EXTRA",
]

DEFAULT_CONFIG_DIR = os.path.join(os.path.expanduser("~"), ".config", APP_ID)
DEFAULT_CONFIG_PATH = os.path.join(DEFAULT_CONFIG_DIR, "config.json")




def _sync_layout_ui(w) -> None:
    """Sync checked layout button + preview image to the currently selected profile."""
    try:
        _sync_layout_buttons(w)
    except Exception:
        pass
    try:
        _update_panel_preview(w)
    except Exception:
        pass
def _socket_name(app_id: str) -> str:
    """Per-user socket name for QLocalServer/QLocalSocket."""
    user = os.environ.get("USERNAME") or os.environ.get("USER") or "user"
    return f"{app_id}-{user}"


class _NagaSingleInstance:
    """Single-instance IPC using QtNetwork local sockets."""

    def __init__(self, app_id: str):
        self.app_id = app_id
        self.socket_name = _socket_name(app_id)
        self.server: Optional[QLocalServer] = None

    def send(self, message: str, timeout_ms: int = 200) -> bool:
        """Return True if a running instance was contacted and message delivered."""
        sock = QLocalSocket()
        sock.connectToServer(self.socket_name)
        if not sock.waitForConnected(timeout_ms):
            return False
        sock.write(message.encode("utf-8", errors="replace"))
        sock.flush()
        sock.waitForBytesWritten(timeout_ms)
        sock.disconnectFromServer()
        return True

    def listen(self, on_message, *, remove_stale: bool = True) -> None:
        """Start listening as the primary instance."""
        self.server = QLocalServer()
        if remove_stale:
            QLocalServer.removeServer(self.socket_name)
        # If listen fails, we still let the app run (worst case: multi-instance)
        if not self.server.listen(self.socket_name):
            return

        def _on_new_connection():
            while self.server and self.server.hasPendingConnections():
                c = self.server.nextPendingConnection()
                if c is None:
                    continue
                c.waitForReadyRead(200)
                msg = bytes(c.readAll()).decode("utf-8", errors="replace").strip()
                try:
                    on_message(msg)
                finally:
                    c.disconnectFromServer()

        self.server.newConnection.connect(_on_new_connection)


def default_pidfile() -> str:
    xdg = os.environ.get("XDG_RUNTIME_DIR")
    if not xdg:
        xdg = f"/run/user/{os.getuid()}"
    return os.path.join(xdg, f"{APP_ID}.pid")


def ensure_dir(p: str) -> None:
    Path(p).mkdir(parents=True, exist_ok=True)


def load_json(path: str) -> Dict[str, Any]:
    # Prefer new config path; if missing, seed from legacy (legacy remains as backup).
    try:
        if (not os.path.exists(path)):
            for _old_id in LEGACY_APP_IDS:
                _old = os.path.expanduser(f"~/.config/{_old_id}/config.json")
                if os.path.exists(_old):
                    ensure_dir(os.path.dirname(path))
                    try:
                        import shutil
                        shutil.copy2(_old, path)
                    except Exception:
                        pass
                    break
    except Exception:
        pass
    if not os.path.exists(path):
        return {
            "active_profile": "default",
            "auto_switch_enabled": True,
            "pointer_scale": 1.0,
            "scroll_scale": 1.0,
            "profiles": {
                "default": {
                    "bindings": {},
                    "modifier_layers": {"shift": {}, "ctrl": {}, "alt": {}},
                }
            },
            "app_profiles": {},
            "app_names": {},
            "rgb": {
                "enabled": False,
                "mouse_device": None,
                "keyboard_device": None,
                "brightness": 100,
                "per_profile": {},
            },
            "rgb_idle": {
                "enabled": False,
                "timeout_seconds": 600,
                "wake_on_activity": True,
            },
        }
    with open(path, "r", encoding="utf-8") as f:
        cfg = json.load(f)

    cfg.setdefault("active_profile", "default")
    cfg.setdefault("auto_switch_enabled", True)
    cfg.setdefault("pointer_scale", 1.0)
    cfg.setdefault("profiles", {"default": {"bindings": {}}})
    cfg.setdefault("app_profiles", {})
    cfg.setdefault("app_names", {})
    cfg.setdefault(
        "rgb",
        {
            "enabled": False,
            "mouse_device": None,
            "keyboard_device": None,
            "brightness": 100,
            "per_profile": {},
        },
    )
    cfg.setdefault(
        "rgb_idle", {"enabled": False, "timeout_seconds": 600, "wake_on_activity": True}
    )
    return cfg


def atomic_write_json(path: str, data: Dict[str, Any]) -> None:
    ensure_dir(os.path.dirname(path))
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, sort_keys=False)
        f.write("\n")
    os.replace(tmp, path)


def pid_is_mapper(pid: int) -> bool:
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as f:
            cmd = f.read().decode("utf-8", errors="ignore").replace("\x00", " ").strip()
        return ("synapse_lite_mapper.py" in cmd) or ("synapse_lite_mapper" in cmd) or ("razer_mouse_control_center_mapper.py" in cmd) or ("razer_mouse_control_center_mapper" in cmd)
    except Exception:
        return False


def read_pid(pidfile: str) -> Optional[int]:
    try:
        s = Path(pidfile).read_text(encoding="utf-8").strip()
        return int(s) if s else None
    except Exception:
        return None


def signal_mapper_reload(pidfile: str) -> Tuple[bool, str]:
    pid = read_pid(pidfile)
    if pid is None:
        return False, f"mapper not running / no pidfile at {pidfile}"

    try:
        os.kill(pid, 0)
    except Exception:
        return False, f"mapper pid not running (pid={pid})"

    if not pid_is_mapper(pid):
        return False, f"pid {pid} is not razer_mouse_control_center_mapper.py"

    try:
        os.kill(pid, signal.SIGHUP)
        return True, f"signaled mapper reload (pid={pid})"
    except Exception as e:
        return False, f"could not signal mapper: {e}"


# ---------- systemd helpers ----------


def run_systemctl_user(args: List[str]) -> Tuple[bool, str]:
    try:
        p = subprocess.run(
            ["systemctl", "--user"] + args,
            capture_output=True,
            text=True,
            check=False,
        )
        out = (p.stdout or "").strip()
        err = (p.stderr or "").strip()
        ok = p.returncode == 0
        msg = out if out else err
        return ok, msg or f"systemctl --user {' '.join(args)} (rc={p.returncode})"
    except FileNotFoundError:
        return False, "systemctl not found"
    except Exception as e:
        return False, f"systemctl error: {e}"


def systemd_is_active(service: str) -> Optional[bool]:
    ok, msg = run_systemctl_user(["is-active", service])
    m = (msg or "").strip()
    if ok:
        return m == "active"
    if m in ("inactive", "failed", "deactivating", "activating"):
        return m == "active"
    return None


# ---------- Backup/Restore helpers ----------


def _home() -> Path:
    return Path.home()


def default_backup_dir() -> Path:
    return _home() / ".config" / APP_ID / "backups"


def service_unit_path() -> Path:
    return _home() / ".config" / "systemd" / "user" / SERVICE_NAME


def _execstart_script_paths_from_service() -> List[Path]:
    """
    Reads ~/.config/systemd/user/razer-mouse-control-center.service and tries to discover
    referenced .py paths from ExecStart= line. Only returns paths under $HOME.
    """
    sp = service_unit_path()
    if not sp.exists():
        return []
    try:
        lines = sp.read_text(encoding="utf-8", errors="ignore").splitlines()
    except Exception:
        return []

    home = _home().resolve()
    found: List[Path] = []

    for line in lines:
        line = line.strip()
        if not line.startswith("ExecStart="):
            continue
        cmd = line.split("=", 1)[1].strip()
        try:
            parts = shlex.split(cmd)
        except Exception:
            parts = cmd.split()

        for tok in parts:
            if not tok.endswith(".py"):
                continue
            p = Path(os.path.expanduser(tok))
            try:
                p = p.resolve()
            except Exception:
                pass
            if str(p).startswith(str(home)) and p.exists():
                found.append(p)

    # unique
    out = []
    seen = set()
    for p in found:
        if p not in seen:
            seen.add(p)
            out.append(p)
    return out


def create_backup_tar_gz(out_path: Path) -> Tuple[bool, str]:
    """
    Create a tar.gz containing:
      - ~/.config/razer-mouse-control-center/ (excluding backups dir itself to avoid recursion)
      - ~/.config/systemd/user/razer-mouse-control-center.service
      - any .py script paths referenced by ExecStart= in the service (under $HOME)
    Stored with paths relative-to-HOME.
    """
    home = _home().resolve()
    cfg_dir = (home / ".config" / APP_ID).resolve()
    backups_dir = default_backup_dir().resolve()
    svc = service_unit_path().resolve()

    include: List[Path] = []
    if cfg_dir.exists():
        include.append(cfg_dir)
    if svc.exists():
        include.append(svc)
    for p in _execstart_script_paths_from_service():
        include.append(p)

    def arcname_for(p: Path) -> str:
        return str(p.resolve().relative_to(home))

    try:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with tarfile.open(out_path, "w:gz") as tf:
            for p in include:
                p = p.resolve()
                if p.is_dir():
                    # Avoid including the backups dir inside itself
                    for sub in p.rglob("*"):
                        try:
                            sub = sub.resolve()
                        except Exception:
                            continue
                        if not sub.exists():
                            continue
                        if backups_dir in sub.parents or sub == backups_dir:
                            continue
                        # store dirs too
                        tf.add(sub, arcname=arcname_for(sub), recursive=False)
                else:
                    tf.add(p, arcname=arcname_for(p), recursive=False)
        return True, f"Backup created: {out_path}"
    except Exception as e:
        return False, f"Backup failed: {e}"


def _safe_members_for_home(tf: tarfile.TarFile) -> List[tarfile.TarInfo]:
    """
    Only allow members that are relative paths (no absolute, no .. traversal).
    We also only extract within $HOME by joining later.
    """
    safe: List[tarfile.TarInfo] = []
    for m in tf.getmembers():
        name = m.name.replace("\\", "/")
        if name.startswith("/") or name.startswith("~"):
            continue
        if ".." in Path(name).parts:
            continue
        safe.append(m)
    return safe


def restore_backup_tar_gz(backup_path: Path) -> Tuple[bool, str]:
    """
    Restores files into $HOME (overwriting).
    """
    home = _home().resolve()
    if not backup_path.exists():
        return False, "Backup file does not exist."

    try:
        with tarfile.open(backup_path, "r:gz") as tf:
            members = _safe_members_for_home(tf)
            for m in members:
                target = home / m.name
                # Ensure target stays in home
                try:
                    target.resolve().relative_to(home)
                except Exception:
                    continue
                # Create parent dirs
                target.parent.mkdir(parents=True, exist_ok=True)
            tf.extractall(path=home, members=members)
        return True, "Restore completed."
    except Exception as e:
        return False, f"Restore failed: {e}"


# ---------- Key capture helpers ----------


def qtkey_to_evdev_names(mods: QtCore.Qt.KeyboardModifiers, key: int) -> List[str]:
    keys: List[str] = []
    if mods & QtCore.Qt.ControlModifier:
        keys.append("KEY_LEFTCTRL")
    if mods & QtCore.Qt.ShiftModifier:
        keys.append("KEY_LEFTSHIFT")
    if mods & QtCore.Qt.AltModifier:
        keys.append("KEY_LEFTALT")
    if mods & QtCore.Qt.MetaModifier:
        keys.append("KEY_LEFTMETA")

    if QtCore.Qt.Key_A <= key <= QtCore.Qt.Key_Z:
        keys.append(f"KEY_{chr(ord('A') + (key - QtCore.Qt.Key_A))}")
        return keys
    if QtCore.Qt.Key_0 <= key <= QtCore.Qt.Key_9:
        keys.append(f"KEY_{key - QtCore.Qt.Key_0}")
        return keys
    if QtCore.Qt.Key_F1 <= key <= QtCore.Qt.Key_F24:
        keys.append(f"KEY_F{1 + (key - QtCore.Qt.Key_F1)}")
        return keys

    # Common non-alphanumeric keys
    if key == QtCore.Qt.Key_Space:
        keys.append("KEY_SPACE")
        return keys
    if key in (QtCore.Qt.Key_Return, QtCore.Qt.Key_Enter):
        keys.append("KEY_ENTER")
        return keys
    if key == QtCore.Qt.Key_Backspace:
        keys.append("KEY_BACKSPACE")
        return keys
    if key == QtCore.Qt.Key_Tab:
        keys.append("KEY_TAB")
        return keys
    if key == QtCore.Qt.Key_Escape:
        keys.append("KEY_ESC")
        return keys

    # If we only recognized modifiers (or an unknown key), return what we have.
    return keys


class KeyCaptureDialog(QtWidgets.QDialog):
    """Modal dialog that captures a single key press and returns an evdev-style KEY_* name."""
    def __init__(self, parent=None, title: str = "Press a key"):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        self.resize(420, 140)
        self.captured_key: Optional[str] = None

        lay = QtWidgets.QVBoxLayout(self)
        msg = QtWidgets.QLabel("Press the key you want to bind now.\n(Esc cancels.)")
        msg.setAlignment(QtCore.Qt.AlignCenter)
        msg.setWordWrap(True)
        lay.addWidget(msg)

        self.preview = QtWidgets.QLineEdit()
        self.preview.setReadOnly(True)
        self.preview.setFocusPolicy(QtCore.Qt.NoFocus)
        lay.addWidget(self.preview)

        self.setFocusPolicy(QtCore.Qt.StrongFocus)

    def showEvent(self, event: QtGui.QShowEvent) -> None:
        super().showEvent(event)
        # Ensure this dialog receives key events even on Wayland/various focus quirks
        try:
            self.raise_()
            self.activateWindow()
        except Exception:
            pass
        self.setFocus(QtCore.Qt.ActiveWindowFocusReason)
        # Grab keyboard so key presses are delivered here
        self.grabKeyboard()

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:
        try:
            self.releaseKeyboard()
        except Exception:
            pass
        super().closeEvent(event)

    def keyPressEvent(self, event: QtGui.QKeyEvent) -> None:
        if event.key() == QtCore.Qt.Key_Escape:
            self.reject()
            return
        names = qtkey_to_evdev_names(event.modifiers(), event.key()) or []
        # We want the primary key (ignore modifier-only captures)
        non_mod = [k for k in names if k not in ("KEY_LEFTCTRL","KEY_RIGHTCTRL","KEY_LEFTALT","KEY_RIGHTALT","KEY_LEFTSHIFT","KEY_RIGHTSHIFT","KEY_LEFTMETA","KEY_RIGHTMETA")]
        if not non_mod:
            # If only modifiers are pressed, ignore
            event.accept()
            return
        self.captured_key = non_mod[-1]
        self.preview.setText(self.captured_key)
        self.accept()


def human_from_binding(b: Dict[str, Any]) -> str:
    if not b:
        return ""
    t = b.get("type")
    if t == "keyboard":
        ks = b.get("keys", [])
        rep = b.get("repeat", False)
        ms = b.get("interval", 100)
        s = "+".join(ks)
        if rep:
            s += f" (repeat {ms}ms)"
        return s
    if t == "mouse":
        btn = str(b.get("button", "") or "")
        # Friendly labels for common mouse buttons (handle legacy lowercase/variants)
        norm = btn.upper()
        if norm == "BTN_SIDE" or btn in ("btn_side", "button_side"):
            return "MB4_Browse_Back"
        if norm == "BTN_EXTRA" or btn in ("btn_extra", "button_extra"):
            return "MB5_Browse_Forward"
        # Deprecated / unused in our app, but keep readable if present in older configs
        if norm == "BTN_BACK":
            return "MB4_Browse_Back"
        if norm == "BTN_FORWARD":
            return "MB5_Browse_Forward"
        return btn
    if t == "passthrough":
        return "(passthrough)"
    if t == "macro":
        name = b.get("macro") or b.get("name") or ""
        return f"MACRO: {name}" if name else "MACRO"
    if t == "special":
        act = str(b.get("action") or "")
        if act == "cycle_subprofile":
            return "SPECIAL: Cycle Subprofile"
        return f"SPECIAL: {act}" if act else "SPECIAL"
    return ""


@dataclass
class BindingResult:
    binding: Optional[Dict[str, Any]]  # None means "clear"


class BindDialog(QtWidgets.QDialog):
    def __init__(
        self,
        parent=None,
        current: Optional[Dict[str, Any]] = None,
        action_key: str = "",
    ):
        super().__init__(parent)
        self.setWindowTitle("Synapse Lite")
        self.setModal(True)
        self.resize(520, 260)

        self._captured_keys: List[str] = []
        self._binding_type = "keyboard"
        self._action_key = (action_key or "").strip()

        # Long-press support (only used for middle_click)

        root = QtWidgets.QVBoxLayout(self)

        root.addWidget(
            QtWidgets.QLabel(
                "Press the key or combo now (e.g. Ctrl+1).\n"
                "Or choose Mouse Output below.\n"
                "Tip: Click inside this window first."
            )
        )

        self.preview = QtWidgets.QLineEdit()
        self.preview.setReadOnly(True)
        self.preview.setFocusPolicy(QtCore.Qt.NoFocus)
        root.addWidget(self.preview)

        tabs = QtWidgets.QTabWidget()
        root.addWidget(tabs)

        kb = QtWidgets.QWidget()
        kb_l = QtWidgets.QVBoxLayout(kb)

        self.repeat_cb = QtWidgets.QCheckBox("Auto repeat")
        kb_l.addWidget(self.repeat_cb)

        rep_row = QtWidgets.QHBoxLayout()
        rep_row.addWidget(QtWidgets.QLabel("Repeat interval (ms):"))
        self.repeat_ms = QtWidgets.QSpinBox()
        self.repeat_ms.setRange(10, 5000)
        self.repeat_ms.setValue(100)
        rep_row.addWidget(self.repeat_ms)
        rep_row.addStretch(1)
        kb_l.addLayout(rep_row)

        kb_l.addStretch(1)
        tabs.addTab(kb, "Keyboard")

        ms = QtWidgets.QWidget()
        ms_l = QtWidgets.QVBoxLayout(ms)

        self.mouse_combo = QtWidgets.QComboBox()
        # Mouse output choices (show friendly labels, keep linux button code as item data)
        self.mouse_combo.clear()
        _mouse_labels = {
            "BTN_SIDE": "MB4_Browse_Back",
            "BTN_EXTRA": "MB5_Browse_Forward",
        }
        for _btn in MOUSE_OUTPUT_CHOICES:
            self.mouse_combo.addItem(_mouse_labels.get(_btn, _btn), _btn)
        ms_l.addWidget(QtWidgets.QLabel("Mouse output:"))
        ms_l.addWidget(self.mouse_combo)
        ms_l.addStretch(1)
        tabs.addTab(ms, "Mouse")

        mac = QtWidgets.QWidget()
        mac_l = QtWidgets.QVBoxLayout(mac)

        mac_l.addWidget(QtWidgets.QLabel("Macro:"))
        self.macro_combo = QtWidgets.QComboBox()
        # populate from parent MainWindow if available
        try:
            if parent is not None and hasattr(parent, "list_macro_names"):
                self.macro_combo.addItems(
                    parent.list_macro_names(parent.current_profile())
                )
        except Exception:
            pass
        mac_l.addWidget(self.macro_combo)
        mac_l.addWidget(QtWidgets.QLabel("Tip: create/edit macros in the Macros tab."))
        mac_l.addStretch(1)
        tabs.addTab(mac, "Macro")

        spec = QtWidgets.QWidget()
        spec_l = QtWidgets.QVBoxLayout(spec)
        spec_l.addWidget(QtWidgets.QLabel("Special action:"))
        self.special_combo = QtWidgets.QComboBox()
        self.special_combo.addItem("Cycle Subprofile", "cycle_subprofile")
        spec_l.addWidget(self.special_combo)
        spec_l.addStretch(1)
        tabs.addTab(spec, "Special")

        btns = QtWidgets.QHBoxLayout()
        self.clear_btn = QtWidgets.QPushButton("Clear")
        self.ok_btn = QtWidgets.QPushButton("OK")
        self.cancel_btn = QtWidgets.QPushButton("Cancel")
        self.ok_btn = QtWidgets.QPushButton("OK")
        self.cancel_btn = QtWidgets.QPushButton("Cancel")
        btns.addWidget(self.clear_btn)
        btns.addStretch(1)
        btns.addWidget(self.cancel_btn)
        btns.addWidget(self.ok_btn)
        root.addLayout(btns)

        self.clear_btn.clicked.connect(self._on_clear)
        self.cancel_btn.clicked.connect(self.reject)
        self.ok_btn.clicked.connect(self.accept)

        # Prevent Space/Enter from activating buttons (allows binding Space, etc.)
        for _b in (self.ok_btn, self.cancel_btn, self.clear_btn):
            try:
                _b.setAutoDefault(False)
                _b.setDefault(False)
                _b.setFocusPolicy(QtCore.Qt.NoFocus)
            except Exception:
                pass
        try:
            self.setFocusPolicy(QtCore.Qt.StrongFocus)
            QtCore.QTimer.singleShot(0, lambda: self.setFocus(QtCore.Qt.OtherFocusReason))
        except Exception:
            pass

        tabs.currentChanged.connect(self._tab_changed)

        if current and current.get("type") == "keyboard":
            self._binding_type = "keyboard"
            self._captured_keys = list(current.get("keys", []))
            self.repeat_cb.setChecked(bool(current.get("repeat", False)))
            self.repeat_ms.setValue(int(current.get("interval", 100)))
            tabs.setCurrentIndex(0)
        elif current and current.get("type") == "mouse":
            self._binding_type = "mouse"
            btn = current.get("button", "BTN_SIDE")
            idx = self.mouse_combo.findData(btn)
            if idx >= 0:
                self.mouse_combo.setCurrentIndex(idx)
            tabs.setCurrentIndex(1)
        elif current and current.get("type") == "macro":
            self._binding_type = "macro"
            name = current.get("macro") or current.get("name") or ""
            idx = self.macro_combo.findText(name)
            if idx >= 0:
                self.macro_combo.setCurrentIndex(idx)
            tabs.setCurrentIndex(2)
        elif current and current.get("type") == "special":
            self._binding_type = "special"
            act = current.get("action") or "cycle_subprofile"
            idx = self.special_combo.findData(act)
            if idx >= 0:
                self.special_combo.setCurrentIndex(idx)
            tabs.setCurrentIndex(3)
        self._update_preview()

    def _tab_changed(self, idx: int):
        if idx == 0:
            self._binding_type = "keyboard"
        elif idx == 1:
            self._binding_type = "mouse"
        elif idx == 2:
            self._binding_type = "macro"
        else:
            self._binding_type = "special"
        self._update_preview()

    def _on_clear(self):
        self._captured_keys = []
        self._update_preview()
        self.done(2)

    def _update_preview(self):
        if self._binding_type == "keyboard":
            s = (
                "+".join(self._captured_keys)
                if self._captured_keys
                else "(press keys...)"
            )
            if self.repeat_cb.isChecked() and self._captured_keys:
                s += f"  (repeat {self.repeat_ms.value()}ms)"
            self.preview.setText(s)
        elif self._binding_type == "special":
            self.preview.setText(self.special_combo.currentText())
        else:
            self.preview.setText(self.mouse_combo.currentData() or self.mouse_combo.currentText())

    def keyPressEvent(self, e: QtGui.QKeyEvent) -> None:
        if self._binding_type != "keyboard":
            super().keyPressEvent(e)
            return

        if e.key() in (
            QtCore.Qt.Key_Control,
            QtCore.Qt.Key_Shift,
            QtCore.Qt.Key_Alt,
            QtCore.Qt.Key_Meta,
        ):
            return

        self._captured_keys = qtkey_to_evdev_names(e.modifiers(), e.key())
        self._update_preview()

    def result_binding(self) -> BindingResult:

        if self.result() == 2:
            return BindingResult(binding=None)

        base: Optional[Dict[str, Any]] = None

        if self._binding_type == "mouse":
            base = {"type": "mouse", "button": self.mouse_combo.currentData() or self.mouse_combo.currentText()}
        elif self._binding_type == "macro":
            base = {"type": "macro", "macro": self.macro_combo.currentText()}
        elif self._binding_type == "special":
            base = {"type": "special", "action": self.special_combo.currentData() or "cycle_subprofile"}
        else:
            if self._captured_keys:
                base = {
                    "type": "keyboard",
                    "keys": self._captured_keys,
                    "repeat": bool(self.repeat_cb.isChecked()),
                    "interval": int(self.repeat_ms.value()),
                }

        if base is None:
            return BindingResult(binding=None)

        return BindingResult(binding=base)

        if self._binding_type == "mouse":
            return BindingResult(
                binding={"type": "mouse", "button": self.mouse_combo.currentData() or self.mouse_combo.currentText()}
            )

        if not self._captured_keys:
            return BindingResult(binding=None)

        return BindingResult(
            binding={
                "type": "keyboard",
                "keys": self._captured_keys,
                "repeat": bool(self.repeat_cb.isChecked()),
                "interval": int(self.repeat_ms.value()),
            }
        )


def setup_tray(
    app: QtWidgets.QApplication, window: "MainWindow"
) -> Optional[QtWidgets.QSystemTrayIcon]:
    if not QtWidgets.QSystemTrayIcon.isSystemTrayAvailable():
        return None

    icon = QtGui.QIcon.fromTheme("input-mouse")
    if icon.isNull():
        icon = app.windowIcon()

    tray = QtWidgets.QSystemTrayIcon(icon, window)
    tray.setToolTip("Synapse Lite")

    menu = QtWidgets.QMenu()
    act_open = menu.addAction("Open")
    act_quit = menu.addAction("Quit")

    def do_open():
        window.show()
        window.raise_()
        window.activateWindow()

    def do_quit():
        tray.hide()
        app.quit()

    act_open.triggered.connect(do_open)
    act_quit.triggered.connect(do_quit)

    tray.setContextMenu(menu)
    tray.activated.connect(
        lambda reason: (
            do_open() if reason == QtWidgets.QSystemTrayIcon.Trigger else None
        )
    )
    tray.show()
    return tray


class _RGBApplyWorker(QtCore.QThread):
    done = QtCore.Signal(bool, str)

    def __init__(self, parent, fn):
        super().__init__(parent)
        self._fn = fn

    def run(self):
        try:
            ok, msg = self._fn()
            self.done.emit(ok, msg)
        except Exception as e:
            self.done.emit(False, str(e))


class MacroRecordDialog(QtWidgets.QDialog):
    """Record key events + timing from keyboard input devices (evdev)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Record Macro")
        self.setModal(True)
        self.resize(520, 220)

        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._stop_ev = threading.Event()
        self._steps: List[Dict[str, Any]] = []

        root = QtWidgets.QVBoxLayout(self)
        self.info = QtWidgets.QLabel(
            "Click Start, then type your macro.\n"
            "Only keyboard press/release events are recorded.\n"
            "Click Stop when done."
        )
        root.addWidget(self.info)

        self.status = QtWidgets.QLabel("Idle.")
        root.addWidget(self.status)

        btns = QtWidgets.QHBoxLayout()
        root.addLayout(btns)
        self.start_btn = QtWidgets.QPushButton("Start")
        self.stop_btn = QtWidgets.QPushButton("Stop")
        self.stop_btn.setEnabled(False)
        self.ok_btn = QtWidgets.QPushButton("Use Recording")
        self.ok_btn.setEnabled(False)
        self.cancel_btn = QtWidgets.QPushButton("Cancel")
        btns.addWidget(self.start_btn)
        btns.addWidget(self.stop_btn)
        btns.addStretch(1)
        btns.addWidget(self.ok_btn)
        btns.addWidget(self.cancel_btn)

        # Prevent Space/Enter from "clicking" focused buttons while recording (Qt default behavior).
        for _b in (self.start_btn, self.stop_btn, self.ok_btn, self.cancel_btn):
            _b.setFocusPolicy(QtCore.Qt.NoFocus)
            _b.setAutoDefault(False)
            _b.setDefault(False)

        self.start_btn.clicked.connect(self._start)
        self.stop_btn.clicked.connect(self._stop)
        self.ok_btn.clicked.connect(self.accept)
        self.cancel_btn.clicked.connect(self.reject)

    def keyPressEvent(self, e: QtGui.QKeyEvent) -> None:
        # Swallow Space/Enter so they don't trigger dialog buttons; recording thread uses evdev anyway.
        if e.key() in (QtCore.Qt.Key_Space, QtCore.Qt.Key_Return, QtCore.Qt.Key_Enter):
            e.accept()
            return
        super().keyPressEvent(e)

    def steps(self) -> List[Dict[str, Any]]:
        return list(self._steps)

    def _discover_keyboards(self) -> List[InputDevice]:
        devs: List[InputDevice] = []
        for p in list_devices():
            try:
                d = InputDevice(p)
                caps = d.capabilities().get(ecodes.EV_KEY, [])
                keys = set(caps)
                # include if looks like a keyboard OR has modifiers
                has_letters = any(k in keys for k in (ecodes.KEY_A, ecodes.KEY_Q))
                has_mods = any(
                    k in keys
                    for k in (
                        ecodes.KEY_LEFTSHIFT,
                        ecodes.KEY_RIGHTSHIFT,
                        ecodes.KEY_LEFTCTRL,
                        ecodes.KEY_RIGHTCTRL,
                        ecodes.KEY_LEFTALT,
                        ecodes.KEY_RIGHTALT,
                        getattr(ecodes, "KEY_ALTGR", ecodes.KEY_RIGHTALT),
                    )
                )
                if has_letters or has_mods or "keyboard" in (d.name or "").lower():
                    devs.append(d)
            except Exception:
                continue
        return devs

    def _start(self):
        if self._running:
            return
        self._steps = []
        self._stop_ev.clear()
        self._running = True
        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.ok_btn.setEnabled(False)
        self.status.setText("Recording…")

        self._thread = threading.Thread(target=self._worker, daemon=True)
        self._thread.start()

    def _stop(self):
        if not self._running:
            return
        self._stop_ev.set()
        self._running = False
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.ok_btn.setEnabled(True)
        self.status.setText(f"Recorded {len(self._steps)} steps.")

    def _worker(self):
        kbs = self._discover_keyboards()
        if not kbs:
            QtCore.QMetaObject.invokeMethod(
                self.status,
                "setText",
                QtCore.Qt.QueuedConnection,
                QtCore.Q_ARG(str, "No keyboard devices found for recording."),
            )
            return

        fds = [d.fd for d in kbs]
        last_t = time.monotonic()

        while not self._stop_ev.is_set():
            try:
                r, _, _ = select.select(fds, [], [], 0.2)
            except Exception:
                continue
            for fd in r:
                d = next((x for x in kbs if x.fd == fd), None)
                if d is None:
                    continue
                try:
                    for ev in d.read():
                        if ev.type != ecodes.EV_KEY:
                            continue
                        if ev.value not in (0, 1):
                            continue  # ignore repeats (2)
                        now = time.monotonic()
                        dt = now - last_t
                        last_t = now
                        ms = int(round(dt * 1000.0))
                        if ms > 0:
                            self._steps.append({"type": "sleep", "ms": ms})
                        name = ecodes.KEY.get(ev.code, None)
                        if not name:
                            continue
                        self._steps.append(
                            {"type": "key", "code": name, "down": bool(ev.value)}
                        )
                except Exception:
                    continue



class _InstantToolTipStyle(QtWidgets.QProxyStyle):
    """Proxy style to make tooltips appear immediately."""

    def styleHint(self, hint, option=None, widget=None, returnData=None):
        if hint == QtWidgets.QStyle.SH_ToolTip_WakeUpDelay:
            return 0
        return super().styleHint(hint, option, widget, returnData)


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self, config_path: str, pidfile: str, start_minimized: bool):
        super().__init__()
        self.preview_mode = "side"
        self.setWindowTitle("Synapse Lite")
        self.resize(1020, 720)

        self.config_path = os.path.abspath(os.path.expanduser(config_path))
        self.pidfile = os.path.abspath(os.path.expanduser(pidfile))

        self.cfg: Dict[str, Any] = load_json(self.config_path)
        # Ensure global macros are available across profiles
        migrate_macros_to_global(self.cfg)
        self._recent_classes: List[str] = []
        self._last_active_class: Optional[str] = None

        root = QtWidgets.QWidget()
        self.setCentralWidget(root)
        layout = QtWidgets.QVBoxLayout(root)

        # --- Mapper control + Backup/Restore row ---
        row = QtWidgets.QHBoxLayout()
        layout.addLayout(row)

        row.addWidget(QtWidgets.QLabel("Mapper:"))
        self.mapper_status_lbl = QtWidgets.QLabel("(unknown)")
        row.addWidget(self.mapper_status_lbl)

        row.addSpacing(16)
        self.btn_start_mapper = QtWidgets.QPushButton("Start")
        self.btn_stop_mapper = QtWidgets.QPushButton("Stop")
        self.btn_restart_mapper = QtWidgets.QPushButton("Restart")
        row.addWidget(self.btn_start_mapper)
        row.addWidget(self.btn_stop_mapper)
        row.addWidget(self.btn_restart_mapper)

        row.addSpacing(24)
        self.btn_backup = QtWidgets.QPushButton("Backup")
        self.btn_restore = QtWidgets.QPushButton("Restore…")
        row.addWidget(self.btn_backup)
        row.addWidget(self.btn_restore)

        row.addStretch(1)

        self.tabs = QtWidgets.QTabWidget()
        try:
            self.tabs.tabBar().setExpanding(False)
        except Exception:
            pass
        self.tabs.setStyleSheet("")
        self.tabs.setStyleSheet("QTabBar::tab { width: 240px; }")
        layout.addWidget(self.tabs)

        # --- Tab bar sizing only (no layout changes) ---
        # Make tabs ~3x wider and visually centered by expanding across the bar.
        try:
            self.tabs.setUsesScrollButtons(False)
            tb = self.tabs.tabBar()
            tb.setExpanding(True)
            tb.setElideMode(QtCore.Qt.ElideNone)
            tb.setStyleSheet("")
        except Exception:
            pass

        # (Tabs omitted here for brevity in comments — this file keeps your existing tabs.
        #  To keep the message readable, I’ve left the tabs as-is from the version you already have.)

        # --------- Tabs: Bindings / Auto-switch / Performance ----------
        # For simplicity: we load the UI from the existing file in your project.
        # BUT: you asked for "no manual edits", so we include full code anyway below:
        # (Same as your current multi-tab GUI, unchanged in behavior.)

        # --- Bindings tab ---
        tab_bind = QtWidgets.QWidget()

        # ---- Welcome tab ----
        self.welcome_tab = QtWidgets.QWidget()
        wlay = QtWidgets.QVBoxLayout(self.welcome_tab)
        wlay.addStretch(1)

        self.welcome_image = QtWidgets.QLabel()
        self.welcome_image.setAlignment(QtCore.Qt.AlignCenter)
        try:
            base_dir = os.path.dirname(os.path.abspath(__file__))
            cand = [
                _asset_path("startpage.png"),
                _asset_path("startpage.png"),
                _asset_path("startpage.png"),
            ]
            pix = QtGui.QPixmap()
            for p in cand:
                if os.path.exists(p):
                    pix = QtGui.QPixmap(p)
                    if not pix.isNull():
                        break
            if not pix.isNull():
                self.welcome_image.setPixmap(
                    pix.scaled(
                        600,
                        400,
                        QtCore.Qt.KeepAspectRatio,
                        QtCore.Qt.SmoothTransformation,
                    )
                )
            else:
                self.welcome_image.setText("Welcome image not found")
        except Exception:
            pass
        wlay.addWidget(self.welcome_image, alignment=QtCore.Qt.AlignCenter)

        self.welcome_version = QtWidgets.QLabel("")
        self.welcome_version.setAlignment(QtCore.Qt.AlignCenter)
        font = self.welcome_version.font()
        font.setPointSize(font.pointSize() + 2)
        self.welcome_version.setFont(font)
        self.welcome_version.hide()
        wlay.addWidget(self.welcome_version)

        wlay.addStretch(2)

        self.tabs.addTab(self.welcome_tab, "Welcome")

        # Shrink left tab bar width (~20%) after layout settles
        QtCore.QTimer.singleShot(
            0, lambda: self._shrink_tabbar_width(0.8)
        )  # only applies for West/East

        self.tabs.addTab(tab_bind, "Mouse")
        # Keyboard tab (v1): image + "modified keys only" list (per profile & layer)
        tab_kb = QtWidgets.QWidget()
        kb_layout = QtWidgets.QVBoxLayout(tab_kb)
        kb_layout.setContentsMargins(9, 6, 9, 9)

        # Top row: layer selector + add/edit/remove
        kb_top = QtWidgets.QHBoxLayout()
        kb_layout.addLayout(kb_top)

        kb_top.addWidget(QtWidgets.QLabel("Active profile:"))
        self.kb_profile_combo = QtWidgets.QComboBox()
        kb_top.addWidget(self.kb_profile_combo)
        # Profile management (base profiles)
        self.kb_set_active_btn = QtWidgets.QPushButton("Set Active")
        kb_top.addWidget(self.kb_set_active_btn)

        self.kb_add_profile_btn = QtWidgets.QPushButton("Add Profile")
        kb_top.addWidget(self.kb_add_profile_btn)

        self.kb_del_profile_btn = QtWidgets.QPushButton("Delete Profile")
        kb_top.addWidget(self.kb_del_profile_btn)

        kb_top.addSpacing(6)
        kb_top.addWidget(QtWidgets.QLabel("Subprofiles:"))
        self.kb_subprofile_combo = QtWidgets.QComboBox()
        self.kb_subprofile_combo.setMinimumWidth(180)
        kb_top.addWidget(self.kb_subprofile_combo)

        self.kb_add_subprofile_btn = QtWidgets.QPushButton("Add Subprofile")
        kb_top.addWidget(self.kb_add_subprofile_btn)

        self.kb_del_subprofile_btn = QtWidgets.QPushButton("Delete Subprofile")
        kb_top.addWidget(self.kb_del_subprofile_btn)

        kb_top.addSpacing(0)

        kb_top.addWidget(QtWidgets.QLabel("Layer:"))
        self.kb_layer_combo = QtWidgets.QComboBox()
        self.kb_layer_combo.addItems(["Normal", "Shift", "Ctrl", "Alt"])
        kb_top.addWidget(self.kb_layer_combo)

        kb_top.addStretch(1)

        self.kb_add_btn = QtWidgets.QPushButton("Add Key…")
        kb_top.addWidget(self.kb_add_btn)
        self.kb_save_apply_btn = QtWidgets.QPushButton("Save + Apply")
        kb_top.addWidget(self.kb_save_apply_btn)
        # Reference image + clickable hotspots (WASD + Q/E/R/F)
        kb_img_path = _asset_path("kblayout.png")
        if os.path.exists(kb_img_path):
            kb_img = QtGui.QPixmap(kb_img_path)

            # Base hotspots are normalized for the 2048x587 Finnish layout image
                        # Hotspots are normalized (x,y,w,h) relative to the base keyboard image size.
            # Preferred source: assets/kblayout_hotspots.json (so tweaking does not require code edits).
            self.kb_hotspots = {}
            hs_path = _asset_path("kblayout_hotspots.json")
            if os.path.exists(hs_path):
                try:
                    with open(hs_path, "r", encoding="utf-8") as f:
                        raw = json.load(f)
                    if isinstance(raw, dict) and "hotspots" in raw:
                        raw = raw["hotspots"]
                    if isinstance(raw, dict):
                        for k, v in raw.items():
                            if isinstance(v, (list, tuple)) and len(v) == 4:
                                self.kb_hotspots[str(k)] = (float(v[0]), float(v[1]), float(v[2]), float(v[3]))
                except Exception:
                    self.kb_hotspots = {}

            fallback_hotspots = {
                "KEY_Q": (0.081543, 0.362862, 0.035156, 0.122658),
                "KEY_W": (0.126465, 0.362862, 0.034668, 0.122658),
                "KEY_E": (0.169922, 0.362862, 0.034668, 0.122658),
                "KEY_R": (0.214355, 0.362862, 0.035156, 0.122658),
                "KEY_A": (0.092773, 0.516184, 0.035156, 0.122658),
                "KEY_S": (0.136719, 0.516184, 0.035156, 0.122658),
                "KEY_D": (0.181152, 0.516184, 0.034668, 0.122658),
                "KEY_F": (0.225098, 0.516184, 0.035156, 0.122658),
                "KEY_1": (0.058000, 0.209540, 0.035156, 0.122658),
                "KEY_2": (0.102922, 0.209540, 0.035156, 0.122658),
                "KEY_3": (0.147844, 0.209540, 0.035156, 0.122658),
                "KEY_4": (0.192766, 0.209540, 0.035156, 0.122658),
                "KEY_5": (0.237688, 0.209540, 0.035156, 0.122658),
                "KEY_F1": (0.060000, 0.060000, 0.035156, 0.115000),
                "KEY_F2": (0.104922, 0.060000, 0.035156, 0.115000),
                "KEY_F3": (0.149844, 0.060000, 0.035156, 0.115000),
                "KEY_F4": (0.194766, 0.060000, 0.035156, 0.115000),
                "KEY_F5": (0.283000, 0.060000, 0.035156, 0.115000),
                "KEY_F6": (0.327922, 0.060000, 0.035156, 0.115000),
                "KEY_F7": (0.372844, 0.060000, 0.035156, 0.115000),
                "KEY_F8": (0.417766, 0.060000, 0.035156, 0.115000),
                "KEY_V": (0.247000, 0.669506, 0.035156, 0.122658),
                "KEY_X": (0.159000, 0.669506, 0.035156, 0.122658),
                "KEY_C": (0.203000, 0.669506, 0.035156, 0.122658),
                "KEY_SPACE": (0.185000, 0.814143, 0.256000, 0.125714),
                "KEY_102ND": (0.071000, 0.669506, 0.035156, 0.122658),
                "KEY_Z": (0.115000, 0.669506, 0.035156, 0.122658),
                "KEY_LEFT": (0.742000, 0.822828, 0.035156, 0.122658),
                "KEY_DOWN": (0.787000, 0.822828, 0.035156, 0.122658),
            }
            if not self.kb_hotspots:
                self.kb_hotspots = fallback_hotspots


            self.kb_preview = ClickableKeyboardPreview()
            self.kb_preview.set_hotspots(self.kb_hotspots)
            if not kb_img.isNull():
                # 40% smaller (60% scale)
                _w = int(kb_img.width() * 0.60)
                _h = int(kb_img.height() * 0.60)
                kb_img = kb_img.scaled(_w, _h, QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation)
            self.kb_preview.set_pixmap(kb_img)

            # Click to bind specific key
            self.kb_preview.keyClicked.connect(self.on_kb_bind_specific)

            kb_layout.addWidget(self.kb_preview)
        # Active bindings row (Edit/Remove live here)
        kb_active_row = QtWidgets.QHBoxLayout()
        kb_active_row.addWidget(QtWidgets.QLabel("Active bindings:"))
        kb_active_row.addStretch(1)

        self.kb_edit_btn = QtWidgets.QPushButton("Edit")
        self.kb_remove_btn = QtWidgets.QPushButton("Remove")
        self.kb_edit_btn.setEnabled(False)
        self.kb_remove_btn.setEnabled(False)
        kb_active_row.addWidget(self.kb_edit_btn)
        kb_active_row.addWidget(self.kb_remove_btn)

        kb_layout.addLayout(kb_active_row)


        # Modified-keys table
        self.kb_table = QtWidgets.QTableWidget(0, 2)
        self.kb_table.setHorizontalHeaderLabels(["Key", "Binding"])
        self.kb_table.horizontalHeader().setStretchLastSection(True)
        self.kb_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.kb_table.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.kb_table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        kb_layout.addWidget(self.kb_table, stretch=1)

        kb_hint = QtWidgets.QLabel(
            "Only keys that you have modified are listed here.\n\n"
            "Tip: Click “Add Key…” then press a key (Finnish layout supported for letters/numbers/F-keys/numpad basics)."
        )
        kb_layout.addWidget(kb_hint)

        self.tabs.addTab(tab_kb, "Keyboard")

        # Keyboard tab wiring
        self.kb_profile_combo.currentTextChanged.connect(self._kb_on_profile_changed)
        if hasattr(self, 'kb_subprofile_combo'):
            self.kb_subprofile_combo.currentTextChanged.connect(self._kb_on_subprofile_changed)
        if hasattr(self, 'kb_set_active_btn'):
            self.kb_set_active_btn.clicked.connect(self.on_set_active)
        if hasattr(self, 'kb_add_profile_btn'):
            self.kb_add_profile_btn.clicked.connect(self.on_add_profile)
        if hasattr(self, 'kb_del_profile_btn'):
            self.kb_del_profile_btn.clicked.connect(self.on_delete_profile)
        if hasattr(self, 'kb_add_subprofile_btn'):
            self.kb_add_subprofile_btn.clicked.connect(self.add_subprofile)
        if hasattr(self, 'kb_del_subprofile_btn'):
            self.kb_del_subprofile_btn.clicked.connect(self.delete_subprofile)
        self.kb_layer_combo.currentIndexChanged.connect(self.refresh_keyboard_table)
        self.kb_add_btn.clicked.connect(self.on_kb_add)
        self.kb_edit_btn.clicked.connect(self.on_kb_edit)
        self.kb_remove_btn.clicked.connect(self.on_kb_remove)
        self.kb_save_apply_btn.clicked.connect(self.on_save_apply)
        self.kb_table.itemSelectionChanged.connect(self._kb_update_buttons)
        bind_layout = QtWidgets.QVBoxLayout(tab_bind)

        bind_layout.setContentsMargins(9, 6, 9, 9)
        top = QtWidgets.QHBoxLayout()
        bind_layout.addLayout(top)

        top.addWidget(QtWidgets.QLabel("Active profile:"))
        self.profile_combo = QtWidgets.QComboBox()
        top.addWidget(self.profile_combo)
        # Profile management (base profiles)

        self.set_active_btn = QtWidgets.QPushButton("Set Active")
        top.addWidget(self.set_active_btn)

        self.add_profile_btn = QtWidgets.QPushButton("Add Profile")
        top.addWidget(self.add_profile_btn)

        self.del_profile_btn = QtWidgets.QPushButton("Delete Profile")
        top.addWidget(self.del_profile_btn)


        top.addSpacing(6)
        top.addWidget(QtWidgets.QLabel("Subprofiles:"))
        self.subprofile_combo = QtWidgets.QComboBox()
        self.subprofile_combo.setMinimumWidth(180)
        top.addWidget(self.subprofile_combo)

        self.add_subprofile_btn = QtWidgets.QPushButton("Add Subprofile")
        top.addWidget(self.add_subprofile_btn)

        self.del_subprofile_btn = QtWidgets.QPushButton("Delete Subprofile")
        top.addWidget(self.del_subprofile_btn)

        # Keep panel layout UI in sync when switching profiles
        self.profile_combo.currentIndexChanged.connect(lambda _=None: _sync_layout_ui(self))
        self.profile_combo.currentTextChanged.connect(lambda _=None: _sync_layout_ui(self))
        self.profile_combo.currentTextChanged.connect(self._on_base_profile_changed)


        top.addWidget(QtWidgets.QLabel("Layer:"))
        self.layer_combo = QtWidgets.QComboBox()
        self.layer_combo.addItems(["Normal", "Shift", "Ctrl", "Alt"])
        top.addWidget(self.layer_combo)


        # Subprofile controls
        if hasattr(self, "add_subprofile_btn"):
            self.add_subprofile_btn.clicked.connect(self.add_subprofile)
        if hasattr(self, "del_subprofile_btn"):
            self.del_subprofile_btn.clicked.connect(self.delete_subprofile)
        if hasattr(self, "subprofile_combo"):
            self.subprofile_combo.currentTextChanged.connect(self._on_subprofile_changed)


        # Pointer scale (per-profile)
        scale_row = QtWidgets.QHBoxLayout()
        bind_layout.addLayout(scale_row)
        scale_row.addWidget(QtWidgets.QLabel("Pointer speed scale:"))

        self.scale_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.scale_slider.setMinimum(10)  # 0.10
        self.scale_slider.setMaximum(300)  # 3.00
        self.scale_slider.setSingleStep(1)
        self.scale_slider.setPageStep(10)
        scale_row.addWidget(self.scale_slider, 1)

        self.scale_spin = QtWidgets.QDoubleSpinBox()
        self.scale_spin.setDecimals(2)
        self.scale_spin.setRange(0.10, 3.00)
        self.scale_spin.setSingleStep(0.01)
        scale_row.addWidget(self.scale_spin)


        # Scroll scale (per-profile)
        scroll_row = QtWidgets.QHBoxLayout()
        bind_layout.addLayout(scroll_row)
        scroll_row.addWidget(QtWidgets.QLabel("Scroll speed scale:"))

        self.scroll_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.scroll_slider.setMinimum(10)   # 0.10
        self.scroll_slider.setMaximum(300)  # 3.00
        self.scroll_slider.setSingleStep(1)
        self.scroll_slider.setPageStep(10)
        scroll_row.addWidget(self.scroll_slider, 1)

        self.scroll_spin = QtWidgets.QDoubleSpinBox()
        self.scroll_spin.setDecimals(2)
        self.scroll_spin.setRange(0.10, 3.00)
        self.scroll_spin.setSingleStep(0.01)
        scroll_row.addWidget(self.scroll_spin)


        top.addStretch(1)

        self.save_apply_btn = QtWidgets.QPushButton("Save + Apply")
        top.addWidget(self.save_apply_btn)

        self.table = QtWidgets.QTableWidget()



        # Hide legacy table (kept for fallback) and use a grid-style bindings view.


        self.table.hide()



        self.bindings_grid = QtWidgets.QWidget()


        self.bindings_grid_layout = QtWidgets.QVBoxLayout(self.bindings_grid)


        self.bindings_grid_layout.setContentsMargins(0, 0, 0, 0)


        self.bindings_grid_layout.setSpacing(8)
        self._hover_row = None

        # Periodic hover sync (robust against missed enter/leave between cell widgets)
        self.table.setColumnCount(3)
        self.table.setHorizontalHeaderLabels(["Action", "Binding", ""])
        self.table.horizontalHeader().setStretchLastSection(False)
        self.table.horizontalHeader().setSectionResizeMode(
            1, QtWidgets.QHeaderView.Stretch
        )
        self.table.horizontalHeader().setSectionResizeMode(
            2, QtWidgets.QHeaderView.ResizeToContents
        )
        self._apply_binding_table_layout()
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        # Use native Qt hover styling instead of manual row painting (prevents 'stuck' highlights)
        self.table.setSelectionMode(QtWidgets.QAbstractItemView.NoSelection)
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        # Hover highlight (blue) for cells
        c = self._hover_qcolor(80)
        self.table.setStyleSheet(
            f"QTableWidget::item:hover {{ background: rgba({c.red()}, {c.green()}, {c.blue()}, {c.alpha()}); }}"
        )
        self.table.setMouseTracking(True)
        self.table.viewport().setMouseTracking(True)
        # --- Bindings grid + preview side-by-side ---
        bind_row = QtWidgets.QHBoxLayout()
        bind_layout.addLayout(bind_row)

        # Left: bindings grid
        left_col = QtWidgets.QWidget()
        left_l = QtWidgets.QVBoxLayout(left_col)
        left_l.setContentsMargins(0, 0, 0, 0)
        left_l.setSpacing(8)
        left_l.addWidget(self.bindings_grid)
        bind_row.addWidget(left_col, 1)

        # Right: panel preview + layout buttons
        right_col = QtWidgets.QWidget()
        right_l = QtWidgets.QVBoxLayout(right_col)
        right_l.setContentsMargins(0, 0, 0, 0)
        right_l.setSpacing(8)

        # Side panel layout buttons (top, aligned with bindings frame top edge)
        btn_row = QtWidgets.QHBoxLayout()
        btn_row.setContentsMargins(0, 0, 0, 0)
        btn_row.setSpacing(8)

        self.panel_btn_top = QtWidgets.QPushButton("Top Buttons")
        self.panel_btn_2 = QtWidgets.QPushButton("2 Button")
        self.panel_btn_6 = QtWidgets.QPushButton("6 Button")
        self.panel_btn_12 = QtWidgets.QPushButton("12 Button")
        for _b in (self.panel_btn_top, self.panel_btn_2, self.panel_btn_6, self.panel_btn_12):
            _b.setCheckable(True)
            btn_row.addWidget(_b)
        btn_row.addStretch(1)
        right_l.addLayout(btn_row)

        # Wire panel preview mode buttons
        self.panel_btn_top.clicked.connect(lambda _=None: _set_preview_mode(self, "top"))
        self.panel_btn_2.clicked.connect(lambda _=None: _set_preview_mode(self, "side", layout="2"))
        self.panel_btn_6.clicked.connect(lambda _=None: _set_preview_mode(self, "side", layout="6"))
        self.panel_btn_12.clicked.connect(lambda _=None: _set_preview_mode(self, "side", layout="12"))

        # Panel preview image (clickable hotspots)
        if not hasattr(self, "panel_preview"):
            self.panel_preview = ClickablePanelPreview()
            self.panel_preview.actionClicked.connect(self._edit_binding_action_key)
            self.panel_preview.actionHovered.connect(lambda ak: QtCore.QTimer.singleShot(0, lambda: (self._highlight_binding_action(ak), self.panel_preview.set_highlight(ak))))
            self.panel_preview.actionUnhovered.connect(lambda _ak=None: QtCore.QTimer.singleShot(0, lambda: (self._highlight_binding_action(None), self.panel_preview.set_highlight(None))))
            self.panel_preview.setStyleSheet("background: transparent;")
            _apply_premium_shadow(self.panel_preview)

            # Keep preview reasonably large
            self.panel_preview.setMinimumSize(380, 380)
            self.panel_preview.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)

        # Give preview stretch so it doesn't shrink
        right_l.addWidget(self.panel_preview, 1)

        bind_row.addWidget(right_col, 0, QtCore.Qt.AlignTop)

# --- Macros tab ---
        tab_macros = QtWidgets.QWidget()
        self.tabs.addTab(tab_macros, "Macros")
        mac_layout = QtWidgets.QVBoxLayout(tab_macros)

        # Top action bar (all macro buttons live here)
        mac_top = QtWidgets.QHBoxLayout()
        mac_layout.addLayout(mac_top)

        # Left-side actions
        self.macro_new_folder_btn = QtWidgets.QPushButton("New Folder")
        self.macro_new_btn = QtWidgets.QPushButton("New Macro")
        self.macro_del_btn = QtWidgets.QPushButton("Delete")
        mac_top.addWidget(self.macro_new_folder_btn)
        mac_top.addWidget(self.macro_new_btn)
        mac_top.addWidget(self.macro_del_btn)

        mac_top.addStretch(1)

        # Right-side actions (steps + record/save)
        self.macro_add_key_btn = QtWidgets.QPushButton("Add Key")
        self.macro_add_mouse_btn = QtWidgets.QPushButton("Add Mouse")
        self.macro_add_text_btn = QtWidgets.QPushButton("Add Text")
        self.macro_add_delay_btn = QtWidgets.QPushButton("Add Delay")
        self.macro_up_btn = QtWidgets.QPushButton("Up")
        self.macro_down_btn = QtWidgets.QPushButton("Down")
        self.macro_step_del_btn = QtWidgets.QPushButton("Delete Step")
        self.macro_record_btn = QtWidgets.QPushButton("Record…")
        self.macro_save_btn = QtWidgets.QPushButton("Save Macro")

        for b in [
            self.macro_add_key_btn,
            self.macro_add_mouse_btn,
            self.macro_add_text_btn,
            self.macro_add_delay_btn,
            self.macro_up_btn,
            self.macro_down_btn,
            self.macro_step_del_btn,
            self.macro_record_btn,
            self.macro_save_btn,
        ]:
            mac_top.addWidget(b)

        mac_split = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        mac_layout.addWidget(mac_split, 1)

        # Left: folder/macro tree
        left = QtWidgets.QWidget()
        left_l = QtWidgets.QVBoxLayout(left)
        mac_split.addWidget(left)

        self.macro_tree = QtWidgets.QTreeWidget()
        self.macro_tree.setHeaderLabels(["Macros"])
        left_l.addWidget(self.macro_tree, 1)

        # Right: macro details
        right = QtWidgets.QWidget()
        right_l = QtWidgets.QVBoxLayout(right)
        mac_split.addWidget(right)

        form = QtWidgets.QFormLayout()
        right_l.addLayout(form)

        self.macro_name_edit = QtWidgets.QLineEdit()
        self.macro_folder_edit = QtWidgets.QLineEdit()
        form.addRow("Name:", self.macro_name_edit)
        form.addRow("Folder:", self.macro_folder_edit)

        opts_row = QtWidgets.QHBoxLayout()
        right_l.addLayout(opts_row)

        self.macro_stop_mode = QtWidgets.QComboBox()
        self.macro_stop_mode.addItems(["on_release", "finish"])
        opts_row.addWidget(QtWidgets.QLabel("Stop:"))
        opts_row.addWidget(self.macro_stop_mode)

        self.macro_timing_mode = QtWidgets.QComboBox()
        self.macro_timing_mode.addItems(["recorded", "fixed"])
        self.macro_fixed_ms = QtWidgets.QSpinBox()
        self.macro_fixed_ms.setRange(1, 5000)
        self.macro_fixed_ms.setValue(50)
        opts_row.addWidget(QtWidgets.QLabel("Timing:"))
        opts_row.addWidget(self.macro_timing_mode)
        opts_row.addWidget(QtWidgets.QLabel("Fixed ms:"))
        opts_row.addWidget(self.macro_fixed_ms)

        rep_row = QtWidgets.QHBoxLayout()
        right_l.addLayout(rep_row)
        self.macro_repeat_mode = QtWidgets.QComboBox()
        self.macro_repeat_mode.addItems(["none", "n", "while_held", "toggle"])
        self.macro_repeat_count = QtWidgets.QSpinBox()
        self.macro_repeat_count.setRange(0, 9999)
        self.macro_repeat_count.setSpecialValueText("∞")  # 0 means infinite
        self.macro_repeat_count.setToolTip(
            "Repeat count (∞ for infinite). For N-times mode, minimum is 1."
        )
        self.macro_repeat_count.setValue(1)
        self.macro_repeat_gap_ms = QtWidgets.QSpinBox()
        self.macro_repeat_gap_ms.setRange(0, 60000)
        self.macro_repeat_gap_ms.setValue(0)
        self.macro_repeat_gap_ms.setToolTip(
            "Delay between repeats in milliseconds (0 = minimal gap)"
        )
        self.macro_no_overlap = QtWidgets.QCheckBox("Don't overlap")
        self.macro_no_overlap.setChecked(True)
        rep_row.addWidget(QtWidgets.QLabel("Repeat:"))
        rep_row.addWidget(self.macro_repeat_mode)
        rep_row.addWidget(QtWidgets.QLabel("Count:"))
        rep_row.addWidget(self.macro_repeat_count)
        rep_row.addWidget(QtWidgets.QLabel("Gap ms:"))
        rep_row.addWidget(self.macro_repeat_gap_ms)
        rep_row.addWidget(self.macro_no_overlap)
        rep_row.addStretch(1)

        self.macro_steps = QtWidgets.QTableWidget(0, 4)
        self.macro_steps.setHorizontalHeaderLabels(
            ["Type", "Code/Text", "Down", "Delay ms"]
        )
        self.macro_steps.horizontalHeader().setStretchLastSection(True)
        right_l.addWidget(self.macro_steps, 1)


        self._selected_macro_key: Optional[str] = None
        self._macro_loading: bool = False
        self._macro_autosave_timer = QtCore.QTimer()
        self._macro_autosave_timer.setSingleShot(True)
        # Macro autosave timer is wired by MacroEditor

        # Wiring (MacroEditor isolation)
        # Disconnect old handlers (safe if not connected)
        for _sig_disconnect in (
            lambda: self.macro_tree.itemSelectionChanged.disconnect(),
            lambda: self.macro_tree.currentItemChanged.disconnect(),
            lambda: self._macro_autosave_timer.timeout.disconnect(),
        ):
            try:
                _sig_disconnect()
            except Exception:
                pass

        for _btn in (
            self.macro_new_folder_btn,
            self.macro_new_btn,
            self.macro_del_btn,
            self.macro_save_btn,
            self.macro_add_key_btn,
            self.macro_add_mouse_btn,
            self.macro_add_text_btn,
            self.macro_add_delay_btn,
            self.macro_step_del_btn,
            self.macro_up_btn,
            self.macro_down_btn,
            self.macro_record_btn,
        ):
            try:
                _btn.clicked.disconnect()
            except Exception:
                pass

        # Create isolated MacroEditor controller
        self.macro_editor = MacroEditor(self)

        # Autosave timer targets MacroEditor
        self._macro_autosave_timer.timeout.connect(self.macro_editor._autosave_tick)

        # Selection
        self.macro_tree.itemSelectionChanged.connect(self.macro_editor.on_selected)
        self.macro_tree.currentItemChanged.connect(
            lambda cur, prev: self.macro_editor.on_selected()
        )

        # CRUD
        self.macro_new_folder_btn.clicked.connect(self.macro_editor.new_folder)
        self.macro_new_btn.clicked.connect(self.macro_editor.new_macro)
        self.macro_del_btn.clicked.connect(self.macro_editor.delete_selected)
        self.macro_save_btn.clicked.connect(self.macro_editor.save_now)

        # Steps
        self.macro_add_key_btn.clicked.connect(
            lambda: self.macro_editor.add_step("key")
        )
        self.macro_add_mouse_btn.clicked.connect(
            lambda: self.macro_editor.add_step("mouse")
        )
        self.macro_add_text_btn.clicked.connect(
            lambda: self.macro_editor.add_step("text")
        )
        self.macro_add_delay_btn.clicked.connect(
            lambda: self.macro_editor.add_step("sleep")
        )
        self.macro_step_del_btn.clicked.connect(self.macro_editor.delete_step)
        self.macro_up_btn.clicked.connect(lambda: self.macro_editor.move_step_up())
        self.macro_down_btn.clicked.connect(lambda: self.macro_editor.move_step_down())
        self.macro_record_btn.clicked.connect(self.macro_editor.record)

        # Auto-save macro property edits
        self.macro_name_edit.textEdited.connect(self.macro_editor.schedule_autosave)
        self.macro_folder_edit.textEdited.connect(self.macro_editor.schedule_autosave)
        self.macro_stop_mode.currentIndexChanged.connect(
            self.macro_editor.schedule_autosave
        )
        self.macro_timing_mode.currentIndexChanged.connect(
            self.macro_editor.schedule_autosave
        )
        self.macro_fixed_ms.valueChanged.connect(self.macro_editor.schedule_autosave)
        self.macro_repeat_mode.currentIndexChanged.connect(
            self.macro_editor.schedule_autosave
        )
        self.macro_repeat_mode.currentIndexChanged.connect(
            lambda *a: self._macro_update_repeat_count_limits()
        )
        self.macro_repeat_count.valueChanged.connect(
            self.macro_editor.schedule_autosave
        )
        self.macro_repeat_gap_ms.valueChanged.connect(
            self.macro_editor.schedule_autosave
        )
        self.macro_no_overlap.stateChanged.connect(self.macro_editor.schedule_autosave)
        self.macro_steps.itemChanged.connect(self.macro_editor.schedule_autosave)

        self.macro_editor.refresh_tree()

        # --- Auto-switch tab ---
        tab_auto = QtWidgets.QWidget()
        self.tabs.addTab(tab_auto, "Auto-switch")
        auto_layout = QtWidgets.QVBoxLayout(tab_auto)

        toggle_row = QtWidgets.QHBoxLayout()
        auto_layout.addLayout(toggle_row)

        self.auto_switch_cb = QtWidgets.QCheckBox("Auto-switch enabled")
        self.auto_switch_cb.setChecked(bool(self.cfg.get("auto_switch_enabled", True)))
        toggle_row.addWidget(self.auto_switch_cb)
        toggle_row.addStretch(1)

        self.active_class_lbl = QtWidgets.QLabel("Active window class: (unknown)")
        auto_layout.addWidget(self.active_class_lbl)

        self.map_table = QtWidgets.QTableWidget()
        self.map_table.setColumnCount(3)
        self.map_table.setHorizontalHeaderLabels(
            ["Window class", "Friendly name", "Profile"]
        )
        self.map_table.horizontalHeader().setStretchLastSection(True)
        self.map_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.map_table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        auto_layout.addWidget(self.map_table)

        form = QtWidgets.QGridLayout()
        auto_layout.addLayout(form)

        form.addWidget(QtWidgets.QLabel("Window class:"), 0, 0)
        self.class_edit = QtWidgets.QLineEdit()
        self.class_edit.setPlaceholderText(
            "e.g. firefox, steam, org.kde.konsole, steam_app_0"
        )
        form.addWidget(self.class_edit, 0, 1)

        form.addWidget(QtWidgets.QLabel("Friendly name:"), 1, 0)
        self.friendly_edit = QtWidgets.QLineEdit()
        self.friendly_edit.setPlaceholderText("Optional: e.g. World of Warcraft")
        form.addWidget(self.friendly_edit, 1, 1)

        form.addWidget(QtWidgets.QLabel("Active profile:"), 2, 0)
        self.profile_pick = QtWidgets.QComboBox()
        form.addWidget(self.profile_pick, 2, 1)

        btnrow = QtWidgets.QHBoxLayout()
        auto_layout.addLayout(btnrow)
        self.add_map_btn = QtWidgets.QPushButton("Add / Update mapping")
        self.del_map_btn = QtWidgets.QPushButton("Delete selected mapping")
        btnrow.addWidget(self.add_map_btn)
        btnrow.addWidget(self.del_map_btn)
        btnrow.addStretch(1)

        auto_layout.addWidget(
            QtWidgets.QLabel("Recent window classes (click to fill):")
        )
        self.recent_list = QtWidgets.QListWidget()
        auto_layout.addWidget(self.recent_list)

        # --- RGB tab ---
        tab_rgb = QtWidgets.QWidget()
        self.tabs.addTab(tab_rgb, "RGB")
        rgb_layout = QtWidgets.QVBoxLayout(tab_rgb)

        rgb_layout.addWidget(
            QtWidgets.QLabel(
                "Sync Razer mouse + keyboard lighting using OpenRGB.\n"
                "This uses the OpenRGB CLI (native if installed, otherwise Flatpak).\n"
                "Make sure OpenRGB is running and can control your devices."
            )
        )

        self.rgb_enable_cb = QtWidgets.QCheckBox("Enable RGB sync")
        self.rgb_enable_cb.setChecked(
            bool((self.cfg.get("rgb") or {}).get("enabled", False))
        )
        rgb_layout.addWidget(self.rgb_enable_cb)

        dev_row = QtWidgets.QGridLayout()
        rgb_layout.addLayout(dev_row)

        dev_row.addWidget(QtWidgets.QLabel("Mouse device:"), 0, 0)
        self.rgb_mouse_combo = QtWidgets.QComboBox()
        self.rgb_mouse_combo.setMinimumWidth(420)
        dev_row.addWidget(self.rgb_mouse_combo, 0, 1)

        dev_row.addWidget(QtWidgets.QLabel("Keyboard device:"), 1, 0)
        self.rgb_kb_combo = QtWidgets.QComboBox()
        self.rgb_kb_combo.setMinimumWidth(420)
        dev_row.addWidget(self.rgb_kb_combo, 1, 1)

        self.rgb_refresh_btn = QtWidgets.QPushButton("Refresh devices")
        dev_row.addWidget(self.rgb_refresh_btn, 0, 2, 2, 1)

        # Brightness
        bright_row = QtWidgets.QHBoxLayout()
        rgb_layout.addLayout(bright_row)
        bright_row.addWidget(QtWidgets.QLabel("Brightness:"))

        self.rgb_bright_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.rgb_bright_slider.setRange(0, 100)
        self.rgb_bright_slider.setSingleStep(1)
        self.rgb_bright_slider.setPageStep(5)
        bright_row.addWidget(self.rgb_bright_slider, 1)

        self.rgb_bright_spin = QtWidgets.QSpinBox()
        self.rgb_bright_spin.setRange(0, 100)
        self.rgb_bright_spin.setSuffix("%")
        bright_row.addWidget(self.rgb_bright_spin)

        # Idle-off (handled by mapper; GUI stores settings only)
        idle_row = QtWidgets.QHBoxLayout()
        rgb_layout.addLayout(idle_row)

        idle_cfg = (
            (self.cfg.get("rgb_idle") or {})
            if isinstance(self.cfg.get("rgb_idle"), dict)
            else {}
        )
        self.rgb_idle_cb = QtWidgets.QCheckBox("Turn off RGB when idle")
        self.rgb_idle_cb.setChecked(
            bool(idle_cfg.get("off_enabled", idle_cfg.get("enabled", False)))
        )
        idle_row.addWidget(self.rgb_idle_cb)

        idle_row.addWidget(QtWidgets.QLabel("Off after:"))
        self.rgb_idle_spin = QtWidgets.QSpinBox()
        self.rgb_idle_spin.setRange(1, 86400)
        self.rgb_idle_spin.setSuffix(" sec")
        try:
            secs = int(
                float(
                    idle_cfg.get(
                        "off_after_seconds", idle_cfg.get("timeout_seconds", 600)
                    )
                )
            )
        except Exception:
            secs = 600
        secs = max(1, min(86400, secs))
        self.rgb_idle_spin.setValue(secs)
        idle_row.addWidget(self.rgb_idle_spin)

        self.rgb_idle_wake_cb = QtWidgets.QCheckBox("Wake on activity")
        self.rgb_idle_wake_cb.setChecked(bool(idle_cfg.get("wake_on_activity", True)))
        idle_row.addWidget(self.rgb_idle_wake_cb)

        idle_row.addStretch(1)

        color_row = QtWidgets.QHBoxLayout()
        rgb_layout.addLayout(color_row)
        color_row.addWidget(QtWidgets.QLabel("Current profile color:"))

        self.rgb_profile_btn = QtWidgets.QToolButton()
        self.rgb_profile_btn.setPopupMode(QtWidgets.QToolButton.InstantPopup)
        self.rgb_profile_btn.setToolButtonStyle(QtCore.Qt.ToolButtonTextOnly)
        self.rgb_profile_btn.setText("Profile: default")
        self.rgb_profile_menu = QtWidgets.QMenu(self.rgb_profile_btn)
        self.rgb_profile_btn.setMenu(self.rgb_profile_menu)
        color_row.addWidget(self.rgb_profile_btn)

        self.rgb_color_preview = QtWidgets.QLabel("      ")
        self.rgb_color_preview.setFixedWidth(70)
        self.rgb_color_preview.setStyleSheet(
            "background: #000000; border: 1px solid #444;"
        )
        color_row.addWidget(self.rgb_color_preview)

        self.rgb_pick_btn = QtWidgets.QPushButton("Choose…")
        color_row.addWidget(self.rgb_pick_btn)

        self.rgb_save_btn = QtWidgets.QPushButton("Save RGB")
        color_row.addWidget(self.rgb_save_btn)

        self.rgb_apply_btn = QtWidgets.QPushButton("Apply now")
        color_row.addWidget(self.rgb_apply_btn)

        color_row.addStretch(1)

        self.rgb_status_lbl = QtWidgets.QLabel("")
        rgb_layout.addWidget(self.rgb_status_lbl)

        rgb_layout.addStretch(1)

        # status
        self.status = QtWidgets.QLabel("")
        layout.addWidget(self.status)

        # wiring - mapper controls
        self.btn_start_mapper.clicked.connect(self.on_start_mapper)
        self.btn_stop_mapper.clicked.connect(self.on_stop_mapper)
        self.btn_restart_mapper.clicked.connect(self.on_restart_mapper)

        # wiring - backup/restore
        self.btn_backup.clicked.connect(self.on_backup_clicked)
        self.btn_restore.clicked.connect(self.on_restore_clicked)

        # wiring - bindings
        self.profile_combo.currentTextChanged.connect(self.on_profile_changed)
        self.layer_combo.currentTextChanged.connect(lambda _=None: self.refresh_table())
        self.table.cellDoubleClicked.connect(self.on_edit_binding)
        self.set_active_btn.clicked.connect(self.on_set_active)
        self.add_profile_btn.clicked.connect(self.on_add_profile)
        self.del_profile_btn.clicked.connect(self.on_delete_profile)
        self.save_apply_btn.clicked.connect(self.on_save_apply)

        # wiring - auto-switch
        self.auto_switch_cb.toggled.connect(self.on_auto_switch_toggled)
        self.add_map_btn.clicked.connect(self.on_add_update_mapping)
        self.del_map_btn.clicked.connect(self.on_delete_mapping)
        self.map_table.itemSelectionChanged.connect(self.on_mapping_selected)
        self.recent_list.itemClicked.connect(self.on_recent_clicked)

        # wiring - performance
        self.scale_slider.valueChanged.connect(self.on_scale_slider)
        self.scroll_slider.valueChanged.connect(self.on_scroll_slider)
        self.scale_spin.valueChanged.connect(self.on_scale_spin)
        self.scroll_spin.valueChanged.connect(self.on_scroll_spin)

        # wiring - rgb
        self.rgb_enable_cb.toggled.connect(self.on_rgb_enable_toggled)
        self.rgb_refresh_btn.clicked.connect(self.on_rgb_refresh_devices)
        self.rgb_bright_slider.valueChanged.connect(self.on_rgb_brightness_slider)
        self.rgb_bright_slider.sliderReleased.connect(self.on_rgb_brightness_commit)
        self.rgb_bright_spin.valueChanged.connect(self.on_rgb_brightness_spin)
        self.rgb_bright_spin.editingFinished.connect(self.on_rgb_brightness_commit)
        self._rgb_apply_timer = QtCore.QTimer(self)
        self._rgb_apply_timer.setSingleShot(True)
        self._rgb_apply_timer.timeout.connect(self._rgb_apply_debounced)
        self.rgb_mouse_combo.currentIndexChanged.connect(self.on_rgb_device_changed)
        self.rgb_kb_combo.currentIndexChanged.connect(self.on_rgb_device_changed)
        self.rgb_pick_btn.clicked.connect(self.on_rgb_pick_color)
        self.rgb_save_btn.clicked.connect(self.on_rgb_save_only)
        self.rgb_apply_btn.clicked.connect(self.on_rgb_apply_now)

        self.rgb_idle_cb.toggled.connect(self.on_rgb_idle_toggled)
        self.rgb_idle_spin.valueChanged.connect(self.on_rgb_idle_timeout_changed)
        self.rgb_idle_wake_cb.toggled.connect(self.on_rgb_idle_wake_toggled)

        # timers
        self.poll_timer = QtCore.QTimer(self)
        self.poll_timer.setInterval(500)
        self.poll_timer.timeout.connect(self.poll_active_window_class)
        self.poll_timer.start()

        self.mapper_timer = QtCore.QTimer(self)
        self.mapper_timer.setInterval(1000)
        self.mapper_timer.timeout.connect(self.refresh_mapper_status)
        self.mapper_timer.start()

        # Watch config on disk for active_profile changes (e.g. mapper cycle_subprofile hotkey)
        self._last_cfg_mtime = 0.0
        self._last_seen_active_profile = str(self.cfg.get("active_profile") or "")
        self.config_watch_timer = QtCore.QTimer(self)
        self.config_watch_timer.setInterval(250)
        self.config_watch_timer.timeout.connect(lambda: getattr(self, '_sync_active_profile_from_disk', lambda: None)())
        self.config_watch_timer.start()


        # tray
        self._tray = None
        self._start_minimized = start_minimized

        # initial sync
        self.refresh_profiles()
        self.refresh_table()
        _sync_layout_buttons(self)
        _update_panel_preview(self)
        self.refresh_mapping_table()
        self._sync_pointer_scale_ui()
        self._sync_scroll_scale_ui()
        self.poll_active_window_class()
        self.refresh_mapper_status()

        # rgb ui init
        self.on_rgb_refresh_devices(silent=True)
        self._rgb_update_preview()
        self._rgb_status("", ok=True)
        if self.rgb_enable_cb.isChecked():
            self._rgb_apply_timer.start(120)

    # tray close behavior
    def attach_tray(self, tray: Optional[QtWidgets.QSystemTrayIcon]):
        self._tray = tray
        if self._start_minimized and self._tray and self._tray.isVisible():
            self.hide()

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:
        if self._tray and self._tray.isVisible():
            event.ignore()
            self.hide()
            self.set_status("Hidden to tray (right-click tray icon to quit).", ok=True)
        else:
            super().closeEvent(event)

    # status
    def set_status(self, text: str, ok: bool = True):
        self.status.setText(text)
        pal = self.status.palette()
        pal.setColor(
            QtGui.QPalette.WindowText, QtGui.QColor("#00ff00" if ok else "#ff5555")
        )
        self.status.setPalette(pal)



    def _highlight_binding_action(self, action_key: str | None):
        """Highlight the binding row + Set/Clear buttons for a given action_key."""
        widgets = getattr(self, "_binding_widgets", {}) or {}

        def _apply(w, on: bool):
            try:
                w.setProperty("hl", "true" if on else "false")
                w.setStyleSheet(w.styleSheet())
                w.update()
            except Exception:
                pass

        for _ak, tup in widgets.items():
            for w in tup:
                _apply(w, False)

        if not action_key:
            return
        tup = widgets.get(str(action_key))
        if not tup:
            return
        for w in tup:
            _apply(w, True)


    def _sync_binding_hover_from_cursor(self):
        """Prevent hover flicker when moving between child widgets inside a binding row.

        Qt sends Enter/Leave events as the cursor moves between row frame, label, and buttons.
        We only clear the highlight if the cursor is truly no longer over *any* widget that
        belongs to a binding row (i.e., has an action_key in its parent chain).
        """
        try:
            w = QtWidgets.QApplication.widgetAt(QtGui.QCursor.pos())
            ak = None
            while w is not None:
                ak = w.property("action_key")
                if ak:
                    ak = str(ak)
                    break
                w = w.parentWidget()
            if ak:
                self._highlight_binding_action(ak)
                try:
                    self.panel_preview.set_highlight(ak)
                except Exception:
                    pass
            else:
                self._highlight_binding_action(None)
                try:
                    self.panel_preview.set_highlight(None)
                except Exception:
                    pass
        except Exception:
            pass


    # mapper control
    def _sync_active_profile_from_disk(self) -> None:
        """Sync GUI with mapper-driven profile/subprofile changes.

        The mapper can change cfg['active_profile'] (cycle_subprofile hotkey) without the GUI.
        We poll the config file mtime and refresh relevant widgets when it changes.
        """
        try:
            st = os.stat(self.config_path)
            mtime = float(getattr(st, "st_mtime", 0.0))
        except Exception:
            return

        if mtime <= float(getattr(self, "_last_cfg_mtime", 0.0)):
            return
        self._last_cfg_mtime = mtime

        try:
            with open(self.config_path, "r", encoding="utf-8") as f:
                raw = json.load(f)
        except Exception:
            return
        if not isinstance(raw, dict):
            return

        new_active = str(raw.get("active_profile") or "")
        if not new_active:
            return
        if new_active == str(getattr(self, "_last_seen_active_profile", "")):
            return

        self._last_seen_active_profile = new_active
        # Replace cfg snapshot so UI reflects runtime truth.
        try:
            self.cfg = raw
            migrate_macros_to_global(self.cfg)
        except Exception:
            pass

        try:
            self._ui_effective_profile = None
        except Exception:
            pass

        # Refresh UI pieces. Use signal blocking so we don't trigger change handlers.
        try:
            self.refresh_profiles()
        except Exception:
            pass
        try:
            self.refresh_table()
        except Exception:
            pass
        try:
            self.refresh_keyboard_table()
        except Exception:
            pass
        try:
            self._sync_pointer_scale_ui()
            self._sync_scroll_scale_ui()
        except Exception:
            pass
        try:
            self._rgb_update_preview()
        except Exception:
            pass


    def refresh_mapper_status(self):
        st = systemd_is_active(SERVICE_NAME)
        if st is None:
            # fall back to legacy service names
            for _s in LEGACY_SERVICE_NAMES:
                st2 = systemd_is_active(_s)
                if st2 is not None:
                    st = st2
                    break
        if st is True:
            self.mapper_status_lbl.setText("Running")
            self.btn_start_mapper.setEnabled(False)
            self.btn_stop_mapper.setEnabled(True)
            self.btn_restart_mapper.setEnabled(True)
        elif st is False:
            self.mapper_status_lbl.setText("Stopped")
            self.btn_start_mapper.setEnabled(True)
            self.btn_stop_mapper.setEnabled(False)
            self.btn_restart_mapper.setEnabled(False)
        else:
            self.mapper_status_lbl.setText("Unknown")
            self.btn_start_mapper.setEnabled(True)
            self.btn_stop_mapper.setEnabled(True)
            self.btn_restart_mapper.setEnabled(True)

    def on_start_mapper(self):
        ok, msg = run_systemctl_user(["start", SERVICE_NAME])
        if not ok:
            for s in LEGACY_SERVICE_NAMES:
                ok, msg = run_systemctl_user(["start", s])
                if ok:
                    break
        self.set_status(f"Start mapper: {msg}", ok=ok)
        self.refresh_mapper_status()

    def on_stop_mapper(self):
        ok, msg = run_systemctl_user(["stop", SERVICE_NAME])
        if not ok:
            for s in LEGACY_SERVICE_NAMES:
                ok, msg = run_systemctl_user(["stop", s])
                if ok:
                    break
        self.set_status(f"Stop mapper: {msg}", ok=ok)
        self.refresh_mapper_status()

    def on_restart_mapper(self):
        ok, msg = run_systemctl_user(["restart", SERVICE_NAME])
        if not ok:
            for s in LEGACY_SERVICE_NAMES:
                ok, msg = run_systemctl_user(["restart", s])
                if ok:
                    break
        self.set_status(f"Restart mapper: {msg}", ok=ok)
        self.refresh_mapper_status()

    # backup/restore
    def on_backup_clicked(self):
        ts = time.strftime("%Y%m%d-%H%M%S")
        out = default_backup_dir() / f"synapse-lite-backup-{ts}.tar.gz"
        ok, msg = create_backup_tar_gz(out)
        self.set_status(msg, ok=ok)
        if ok:
            QtWidgets.QMessageBox.information(
                self, "Backup created", f"{msg}\n\nSaved to:\n{out}"
            )

    def on_restore_clicked(self):
        backup_dir = str(default_backup_dir())
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "Restore backup",
            backup_dir,
            "Synapse Lite backups (*.tar.gz);;All files (*)",
        )
        if not path:
            return

        backup_path = Path(path)

        res = QtWidgets.QMessageBox.warning(
            self,
            "Restore backup",
            "This will OVERWRITE your current config/service/scripts with the backup.\n\n"
            "A safety backup will be created first.\n\nProceed?",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
        )
        if res != QtWidgets.QMessageBox.Yes:
            return

        # safety backup first
        ts = time.strftime("%Y%m%d-%H%M%S")
        safety = default_backup_dir() / f"pre-restore-safety-{ts}.tar.gz"
        okb, msgb = create_backup_tar_gz(safety)
        if not okb:
            QtWidgets.QMessageBox.critical(
                self, "Restore", f"Safety backup FAILED:\n{msgb}"
            )
            self.set_status(msgb, ok=False)
            return

        # stop mapper
        run_systemctl_user(["stop", SERVICE_NAME])

        # restore
        ok, msg = restore_backup_tar_gz(backup_path)
        if not ok:
            QtWidgets.QMessageBox.critical(self, "Restore failed", msg)
            self.set_status(msg, ok=False)
            return

        # reload systemd + restart mapper
        run_systemctl_user(["daemon-reload"])
        run_systemctl_user(["start", SERVICE_NAME])

        # reload config into GUI
        self.cfg = load_json(self.config_path)

        # Ensure global macros are available across profiles
        try:
            migrate_macros_to_global(self.cfg)
        except Exception:
            pass

        # Clear any UI override so views follow runtime truth after restore
        try:
            self._ui_effective_profile = None
        except Exception:
            pass
        try:
            self._last_seen_active_profile = str(self.cfg.get("active_profile") or "")
            self._last_cfg_mtime = 0.0
        except Exception:
            pass

        # Refresh all profile selectors + both mouse/keyboard binding tables
        self.refresh_profiles()
        try:
            self.refresh_table()
        except Exception:
            pass
        try:
            self.refresh_keyboard_table()
        except Exception:
            pass
        try:
            self.refresh_mapping_table()
        except Exception:
            pass
        try:
            self._sync_pointer_scale_ui()
            self._sync_scroll_scale_ui()
        except Exception:
            pass
        try:
            self._rgb_update_preview()
        except Exception:
            pass
        self.refresh_mapper_status()

        QtWidgets.QMessageBox.information(
            self,
            "Restore completed",
            "Restore completed.\n\n"
            f"Safety backup saved:\n{safety}\n\n"
            "Mapper was restarted.",
        )
        self.set_status("Restore completed and mapper restarted.", ok=True)

    # profile helpers

    def refresh_profiles(self):
        all_profiles = list((self.cfg.get("profiles") or {}).keys())
        if not all_profiles:
            self.cfg["profiles"] = {"default": {"bindings": {}, "settings": {}}}
            all_profiles = ["default"]

        def _is_subprofile(p: str) -> bool:
            return bool(((self.cfg.get("profiles") or {}).get(p, {}).get("settings") or {}).get("subprofile_of"))

        base_profiles = [p for p in all_profiles if not _is_subprofile(p)]
        if not base_profiles:
            base_profiles = ["default"]

        active_effective = self.cfg.get("active_profile", "default") or "default"
        if active_effective not in all_profiles:
            active_effective = base_profiles[0]
            self.cfg["active_profile"] = active_effective

        # Determine base profile for UI
        active_base = ((self.cfg.get("profiles") or {}).get(active_effective, {}).get("settings") or {}).get("subprofile_of") or active_effective
        if active_base not in base_profiles:
            active_base = base_profiles[0]

        # Subprofiles for current base
        subprofiles = [
            p for p in all_profiles
            if (((self.cfg.get("profiles") or {}).get(p, {}).get("settings") or {}).get("subprofile_of") == active_base)
        ]
        subprofiles_sorted = sorted(subprofiles, key=_synapse_name_sort_key)
        active_sub = active_effective if active_effective in subprofiles_sorted else ""

        # Sort base profiles with Default first, then alphabetical.
        default_name = str(self.cfg.get('default_profile') or 'default')
        if default_name in base_profiles:
            base_profiles_sorted = [default_name] + sorted([p for p in base_profiles if p != default_name], key=_synapse_name_sort_key)
        else:
            base_profiles_sorted = sorted(base_profiles, key=_synapse_name_sort_key)

        # Mouse tab base profile combo
        self.profile_combo.blockSignals(True)
        self.profile_combo.clear()
        self.profile_combo.addItems(base_profiles_sorted)
        self.profile_combo.setCurrentText(active_base)
        self.profile_combo.blockSignals(False)

        # Mouse tab subprofile combo (empty option first)
        if hasattr(self, "subprofile_combo"):
            self.subprofile_combo.blockSignals(True)
            self.subprofile_combo.clear()
            self.subprofile_combo.addItem("")  # empty = no subprofile
            self.subprofile_combo.addItems(subprofiles_sorted)
            self.subprofile_combo.setCurrentText(active_sub)
            self.subprofile_combo.blockSignals(False)

        # Keyboard tab base profile combo
        if hasattr(self, "kb_profile_combo"):
            self.kb_profile_combo.blockSignals(True)
            self.kb_profile_combo.clear()
            self.kb_profile_combo.addItems(base_profiles_sorted)
            self.kb_profile_combo.setCurrentText(active_base)
            self.kb_profile_combo.blockSignals(False)

        # Keyboard tab subprofile combo
        if hasattr(self, "kb_subprofile_combo"):
            self.kb_subprofile_combo.blockSignals(True)
            self.kb_subprofile_combo.clear()
            self.kb_subprofile_combo.addItem("")
            self.kb_subprofile_combo.addItems(subprofiles_sorted)
            self.kb_subprofile_combo.setCurrentText(active_sub)
            self.kb_subprofile_combo.blockSignals(False)

        # Settings dialog combo (if present): show base profiles only
        if hasattr(self, "profile_pick"):
            try:
                self.profile_pick.blockSignals(True)
                self.profile_pick.clear()
                self.profile_pick.addItems(base_profiles_sorted)
                self.profile_pick.setCurrentText(active_base)
                self.profile_pick.blockSignals(False)
            except Exception:
                pass

        try:
            if not getattr(self, "_rgb_effective_profile", None):
                self._rgb_effective_profile = self.current_profile()
            self._refresh_rgb_profile_menu()
        except Exception:
            pass

    def current_profile(self) -> str:
        # Effective profile for UI editing/preview. May differ from cfg['active_profile'] until Set Active.
        eff = getattr(self, "_ui_effective_profile", None)
        if eff:
            return str(eff)
        return str(self.cfg.get("active_profile") or self.profile_combo.currentText() or "default")

    def _rgb_target_profile(self) -> str:
        target = getattr(self, "_rgb_effective_profile", None)
        if target:
            return str(target)
        return self.current_profile()

    def _rgb_profile_button_text(self, effective: str) -> str:
        profs = self.cfg.get("profiles") or {}
        settings = ((profs.get(effective) or {}).get("settings") or {}) if isinstance(profs.get(effective), dict) else {}
        base = str(settings.get("subprofile_of") or effective or "default")
        if effective and effective != base:
            return f"Profile: {base} ▸ {effective}"
        return f"Profile: {base or 'default'}"

    def _refresh_rgb_profile_menu(self) -> None:
        if not hasattr(self, "rgb_profile_menu"):
            return
        profs = self.cfg.get("profiles") or {}
        all_profiles = list(profs.keys())
        if not all_profiles:
            return

        def _is_subprofile(p: str) -> bool:
            return bool((((profs.get(p) or {}).get("settings") or {}).get("subprofile_of")))

        base_profiles = [p for p in all_profiles if not _is_subprofile(p)]
        default_name = str(self.cfg.get("default_profile") or "default")
        if default_name in base_profiles:
            base_profiles = [default_name] + sorted([p for p in base_profiles if p != default_name], key=_synapse_name_sort_key)
        else:
            base_profiles = sorted(base_profiles, key=_synapse_name_sort_key)

        self.rgb_profile_menu.clear()
        current = self.current_profile()
        for base in base_profiles:
            subs = self._subprofiles_for_base(base)
            if subs:
                submenu = self.rgb_profile_menu.addMenu(base)
                act_base = submenu.addAction(base)
                act_base.triggered.connect(lambda _=False, b=base: self._select_rgb_profile_target(b, ""))
                submenu.addSeparator()
                for sub in subs:
                    act = submenu.addAction(sub)
                    act.triggered.connect(lambda _=False, b=base, s=sub: self._select_rgb_profile_target(b, s))
            else:
                act = self.rgb_profile_menu.addAction(base)
                act.triggered.connect(lambda _=False, b=base: self._select_rgb_profile_target(b, ""))

        try:
            current = self._rgb_target_profile()
            self.rgb_profile_btn.setText(self._rgb_profile_button_text(current))
        except Exception:
            pass

    def _select_rgb_profile_target(self, base: str, sub: str = "") -> None:
        try:
            effective = sub if (sub and sub in self._subprofiles_for_base(base)) else base
            self._rgb_effective_profile = effective
            self._rgb_update_preview()
            self._refresh_rgb_profile_menu()
        except Exception:
            pass

    def base_profile(self) -> str:
        return self.profile_combo.currentText() or "default"

    def _subprofiles_for_base(self, base: str) -> List[str]:
        profs = self.cfg.get("profiles") or {}
        out: List[str] = []
        for name, pdata in profs.items():
            settings = (pdata.get("settings") or {})
            if settings.get("subprofile_of") == base:
                out.append(name)
        return sorted(out)

    def _set_active_effective_profile(self, effective: str) -> None:
        profs = self.cfg.get("profiles") or {}
        if effective not in profs:
            return
        self.cfg["active_profile"] = effective
        base = (profs.get(effective, {}).get("settings") or {}).get("subprofile_of")
        if base:
            self.cfg.setdefault("last_subprofiles", {})[base] = effective
        self.save_config()
        self.refresh_profiles()
        try:
            self.refresh_table()
        except Exception:
            pass
        try:
            self.refresh_keyboard_table()
        except Exception:
            pass


    def _set_ui_effective_profile(self, effective: str) -> None:
        """Update UI editing target without committing cfg['active_profile']."""
        profs = self.cfg.get("profiles") or {}
        if effective and effective not in profs:
            return
        self._ui_effective_profile = effective or None
        try:
            self.refresh_table()
        except Exception:
            pass
        try:
            self.refresh_keyboard_table()
        except Exception:
            pass

    def _populate_subprofiles_combo(self, base: str, selected: str = "") -> None:
        subs = self._subprofiles_for_base(base)

        def _fill(combo):
            combo.clear()
            combo.addItem("")
            for s in subs:
                combo.addItem(s)
            combo.setCurrentText(selected if selected in subs else "")

        if hasattr(self, "subprofile_combo"):
            try:
                blocker = QtCore.QSignalBlocker(self.subprofile_combo)
                _fill(self.subprofile_combo)
                del blocker
            except Exception:
                _fill(self.subprofile_combo)

        if hasattr(self, "kb_subprofile_combo"):
            try:
                blocker = QtCore.QSignalBlocker(self.kb_subprofile_combo)
                _fill(self.kb_subprofile_combo)
                del blocker
            except Exception:
                _fill(self.kb_subprofile_combo)


    def _on_base_profile_changed(self, base: str) -> None:
        # Base dropdown changed: update subprofile choices and UI preview only.
        last = (self.cfg.get("last_subprofiles") or {}).get(base)
        subs = self._subprofiles_for_base(base)
        selected = last if (last and last in subs) else ""
        self._populate_subprofiles_combo(base, selected=selected)
        effective = selected or base
        self._set_ui_effective_profile(effective)
        try:
            self._refresh_rgb_profile_menu()
        except Exception:
            pass

    def _on_subprofile_changed(self, sub: str) -> None:
        base = self.base_profile()
        subs = self._subprofiles_for_base(base)
        effective = sub if (sub and sub in subs) else base
        self._set_ui_effective_profile(effective)
        try:
            self._refresh_rgb_profile_menu()
        except Exception:
            pass

    def add_subprofile(self) -> None:
        base = self.base_profile()
        name, ok = QtWidgets.QInputDialog.getText(self, "Add Subprofile", f"New subprofile name for {base}:")
        if not ok:
            return
        name = (name or "").strip()
        if not name:
            return
        profs = self.cfg.setdefault("profiles", {})
        if name in profs:
            QtWidgets.QMessageBox.warning(self, "Exists", "A profile with that name already exists.")
            return
        import copy
        base_data = copy.deepcopy(profs.get(base, {"bindings": {}, "settings": {}}))
        base_data.setdefault("settings", {})["subprofile_of"] = base
        # Ensure the bottom button (mouse-emitted KEY_F24) can cycle subprofiles by default.
        kb = base_data.setdefault("keyboard_bindings", {})
        kb_norm = kb.setdefault("normal", {})
        kb_norm.setdefault("KEY_F24", {"type": "special", "action": "cycle_subprofile"})
        profs[name] = base_data
        self.cfg.setdefault("last_subprofiles", {})[base] = name
        self.cfg["active_profile"] = name
        self.save_config()
        self.refresh_profiles()
        try:
            self.refresh_table()
        except Exception:
            pass
        try:
            self.refresh_keyboard_table()
        except Exception:
            pass

    def delete_subprofile(self) -> None:
        base = self.base_profile()
        sub = ""
        if hasattr(self, "subprofile_combo"):
            sub = self.subprofile_combo.currentText() or ""
        if not sub:
            return
        profs = self.cfg.get("profiles") or {}
        if ((profs.get(sub, {}).get("settings") or {}).get("subprofile_of") != base):
            return
        if QtWidgets.QMessageBox.question(self, "Delete Subprofile", f"Delete subprofile '{sub}'?") != QtWidgets.QMessageBox.Yes:
            return
        profs.pop(sub, None)
        last = self.cfg.get("last_subprofiles") or {}
        if last.get(base) == sub:
            last.pop(base, None)
        if self.cfg.get("active_profile") == sub:
            self.cfg["active_profile"] = base
        self.save_config()
        self.refresh_profiles()
        try:
            self.refresh_table()
        except Exception:
            pass
        try:
            self.refresh_keyboard_table()
        except Exception:
            pass

    def _kb_on_subprofile_changed(self, sub: str) -> None:
        # Sync Mouse tab subprofile
        if hasattr(self, "subprofile_combo"):
            try:
                blocker = QtCore.QSignalBlocker(self.subprofile_combo)
                self.subprofile_combo.setCurrentText(sub)
                del blocker
            except Exception:
                self.subprofile_combo.setCurrentText(sub)
        self._on_subprofile_changed(sub)


    def profile_bindings(self, profile: str) -> Dict[str, Any]:
        """Normal (no-modifier) bindings."""
        self.cfg.setdefault("profiles", {})
        self.cfg["profiles"].setdefault(
            profile,
            {"bindings": {}, "modifier_layers": {"shift": {}, "ctrl": {}, "alt": {}}},
        )
        self.cfg["profiles"][profile].setdefault("bindings", {})
        self.cfg["profiles"][profile].setdefault(
            "modifier_layers", {"shift": {}, "ctrl": {}, "alt": {}}
        )
        return self.cfg["profiles"][profile]["bindings"]

    def current_layer(self) -> str:
        # "normal" | "shift" | "ctrl" | "alt"
        if not hasattr(self, "layer_combo"):
            return "normal"
        t = (self.layer_combo.currentText() or "Normal").strip().lower()
        return {"normal": "normal", "shift": "shift", "ctrl": "ctrl", "alt": "alt"}.get(
            t, "normal"
        )
    # ---- Macros (global) ----
    def profile_macros(self, profile: str) -> Dict[str, Any]:
        # Macros are global (shared by all profiles).
        migrate_macros_to_global(self.cfg)
        self.cfg.setdefault("macros", {})
        if not isinstance(self.cfg.get("macros"), dict):
            self.cfg["macros"] = {}
        return self.cfg["macros"]

    def list_macro_names(self, profile: str) -> List[str]:
        """Return macro keys for binding assignment (excluding hidden placeholders)."""
        macros = self.profile_macros(profile)
        out: List[str] = []
        for k in sorted(list((macros or {}).keys())):
            m = (macros or {}).get(k) or {}
            if isinstance(m, dict) and bool(m.get("hidden")):
                continue
            # skip placeholder convention even if hidden flag is missing
            if isinstance(k, str) and k.endswith("/_placeholder"):
                continue
            out.append(k)
        return out

    def profile_layer_bindings(self, profile: str, layer: str) -> Dict[str, Any]:
        """Return bindings dict for a given layer."""
        layer = (layer or "normal").lower()
        if layer == "normal":
            return self.profile_bindings(profile)

        self.cfg.setdefault("profiles", {})
        self.cfg["profiles"].setdefault(
            profile,
            {"bindings": {}, "modifier_layers": {"shift": {}, "ctrl": {}, "alt": {}}},
        )
        p = self.cfg["profiles"][profile]
        p.setdefault("modifier_layers", {"shift": {}, "ctrl": {}, "alt": {}})
        p["modifier_layers"].setdefault(layer, {})
        return p["modifier_layers"][layer]


    # keyboard tab (v1)
    def _kb_layer_key(self) -> str:
        try:
            return (self.kb_layer_combo.currentText() or "Normal").strip().lower()
        except Exception:
            return "normal"

    def profile_keyboard_layer_bindings(self, profile: str, layer: str) -> Dict[str, Any]:
        """Return keyboard bindings dict for a given profile+layer. Creates containers if missing."""
        layer = (layer or "normal").lower()
        self.cfg.setdefault("profiles", {})
        if not isinstance(self.cfg["profiles"].get(profile), dict):
            self.cfg["profiles"][profile] = {}
        p = self.cfg["profiles"][profile]
        p.setdefault("keyboard_bindings", {})
        kb = p["keyboard_bindings"]
        # canonicalize layer keys
        kb.setdefault("normal", {})
        kb.setdefault("shift", {})
        kb.setdefault("ctrl", {})
        kb.setdefault("alt", {})
        if layer not in kb:
            kb[layer] = {}
        if not isinstance(kb[layer], dict):
            kb[layer] = {}
        return kb[layer]

    def _kb_selected_key(self) -> Optional[str]:
        try:
            row = self.kb_table.currentRow()
            if row < 0:
                return None
            item = self.kb_table.item(row, 0)
            return item.text().strip() if item else None
        except Exception:
            return None

    def _kb_update_buttons(self) -> None:
        k = self._kb_selected_key()
        self.kb_edit_btn.setEnabled(bool(k))
        self.kb_remove_btn.setEnabled(bool(k))

    def refresh_keyboard_table(self) -> None:
        """Refresh the keyboard 'modified keys only' list for current profile/layer."""
        try:
            prof = (self.current_profile() or self.cfg.get("active_profile") or "default").strip()
        except Exception:
            prof = (self.cfg.get("active_profile") or "default")
        layer = self._kb_layer_key()
        binds = self.profile_keyboard_layer_bindings(prof, layer)

        self.kb_table.setRowCount(0)
        # stable order
        for key_name in sorted(binds.keys()):
            b = binds.get(key_name) or {}
            row = self.kb_table.rowCount()
            self.kb_table.insertRow(row)
            self.kb_table.setItem(row, 0, QtWidgets.QTableWidgetItem(key_name))
            self.kb_table.setItem(row, 1, QtWidgets.QTableWidgetItem(human_from_binding(b)))

        self._kb_update_buttons()

    def on_kb_add(self) -> None:
        try:
            prof = (self.current_profile() or self.cfg.get("active_profile") or "default").strip()
        except Exception:
            prof = (self.cfg.get("active_profile") or "default")
        layer = self._kb_layer_key()

        dlg = KeyCaptureDialog(self, title="Bind keyboard key")
        if dlg.exec_() != QtWidgets.QDialog.Accepted or not dlg.captured_key:
            return

        key_name = dlg.captured_key
        binds = self.profile_keyboard_layer_bindings(prof, layer)
        current = binds.get(key_name)

        bd = BindDialog(self, current=current, action_key=key_name)
        if bd.exec_() != QtWidgets.QDialog.Accepted:
            return
        res: BindingResult = bd.result_binding()
        if res.binding is None:
            binds.pop(key_name, None)
        else:
            binds[key_name] = res.binding

        self.refresh_keyboard_table()

    def on_kb_edit(self) -> None:
        key_name = self._kb_selected_key()
        if not key_name:
            return
        try:
            prof = (self.current_profile() or self.cfg.get("active_profile") or "default").strip()
        except Exception:
            prof = (self.cfg.get("active_profile") or "default")
        layer = self._kb_layer_key()
        binds = self.profile_keyboard_layer_bindings(prof, layer)
        current = binds.get(key_name)

        bd = BindDialog(self, current=current, action_key=key_name)
        if bd.exec_() != QtWidgets.QDialog.Accepted:
            return
        res: BindingResult = bd.result_binding()
        if res.binding is None:
            binds.pop(key_name, None)
        else:
            binds[key_name] = res.binding
        self.refresh_keyboard_table()

    def on_kb_remove(self) -> None:
        key_name = self._kb_selected_key()
        if not key_name:
            return
        try:
            prof = (self.current_profile() or self.cfg.get("active_profile") or "default").strip()
        except Exception:
            prof = (self.cfg.get("active_profile") or "default")
        layer = self._kb_layer_key()
        binds = self.profile_keyboard_layer_bindings(prof, layer)
        binds.pop(key_name, None)
        self.refresh_keyboard_table()

    def on_kb_bind_specific(self, key_name: str) -> None:
        """Open the binding editor for a specific keyboard key (from clickable hotspots)."""
        key_name = (key_name or "").strip()
        if not key_name:
            return
        try:
            prof = (self.current_profile() or self.cfg.get("active_profile") or "default").strip()
        except Exception:
            prof = (self.cfg.get("active_profile") or "default")
        layer = self._kb_layer_key()
        binds = self.profile_keyboard_layer_bindings(prof, layer)
        current = binds.get(key_name)

        bd = BindDialog(self, current=current, action_key=key_name)
        if bd.exec_() != QtWidgets.QDialog.Accepted:
            return
        res: BindingResult = bd.result_binding()
        if res.binding is None:
            binds.pop(key_name, None)
        else:
            binds[key_name] = res.binding

        self.refresh_keyboard_table()

    # bindings tab
    def _apply_binding_table_layout(self):
        """Keep bindings table columns stable after refreshes."""
        try:
            hdr = self.table.horizontalHeader()
            hdr.setStretchLastSection(False)
            hdr.setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeToContents)
            hdr.setSectionResizeMode(1, QtWidgets.QHeaderView.Stretch)
            hdr.setSectionResizeMode(2, QtWidgets.QHeaderView.ResizeToContents)
        except Exception:
            pass

    def _hover_qcolor(self, alpha: int = 80) -> QtGui.QColor:
        c = self.palette().color(QtGui.QPalette.Highlight)
        c.setAlpha(alpha)
        return c

    def _set_row_bg(self, row: int, color: QtGui.QColor | None):
        try:
            for c in (0, 1):
                it = self.table.item(row, c)
                if it is None:
                    continue
                if color is None:
                    it.setData(QtCore.Qt.BackgroundRole, None)  # reset default
                else:
                    it.setBackground(color)
        except Exception:
            pass


    def eventFilter(self, obj, event):
        # Legacy: hover for Clear buttons in the hidden QTableWidget
        try:
            if obj is not None and obj.property("is_clear_btn"):
                row = int(obj.property("row"))
                if event.type() == QtCore.QEvent.Enter:
                    self._set_row_bg(row, self._hover_qcolor(80))
                elif event.type() == QtCore.QEvent.Leave:
                    self._set_row_bg(row, None)
                return False
        except Exception:
            pass

        # Reverse hover: binding row / Set / Clear -> highlight hotspot + row
        try:
            ak = None
            if obj is not None:
                ak = obj.property("action_key")
            if ak:
                ak = str(ak)
                if event.type() == QtCore.QEvent.Enter:
                    QtCore.QTimer.singleShot(0, lambda ak=ak: (self._highlight_binding_action(ak), self.panel_preview.set_highlight(ak)))
                elif event.type() == QtCore.QEvent.Leave:
                    # Avoid flicker when moving between child widgets inside the row
                    QtCore.QTimer.singleShot(0, self._sync_binding_hover_from_cursor)
        except Exception:
            pass

        return super().eventFilter(obj, event)



    def refresh_table(self):
        prof = self.current_profile()
        layer = self.current_layer()
        binds = self.profile_layer_bindings(prof, layer)

        # Determine which preview mode we're in: side layouts (2/6/12) or top buttons
        mode = getattr(self, "preview_mode", "side")
        layout = getattr(self, "preview_layout_override", None) or _get_panel_layout(self.cfg, prof)

        # Build the row spec: list of (display_number, label, action_key)
        if mode == "top":
            rows = [
                ("1", "Wheel Tilt Left", "wheel_tilt_left"),
                ("2", "Wheel Tilt Right", "wheel_tilt_right"),
                ("3", "Middle Click", "middle_click"),
                ("4", "DPI Up", "dpi_up"),
                ("5", "DPI Down", "dpi_down"),
            ]
        else:
            if layout == "2":
                rows = [
                    ("1", "Button 1", "mb5"),
                    ("2", "Button 2", "mb4"),
                ]
            elif layout == "6":
                rows = [
                    ("1", "Button 1", "top_row_left"),
                    ("2", "Button 2", "top_row_middle"),
                    ("3", "Button 3", "top_row_right"),
                    ("4", "Button 4", "thumb6_bottom_left"),
                    ("5", "Button 5", "thumb6_bottom_middle"),
                    ("6", "Button 6", "bottom_row_right"),
                ]
            elif layout == "12":
                rows = [
                    ("1", "Button 1", "thumb12_top_left"),
                    ("2", "Button 2", "thumb12_top_middle_left"),
                    ("3", "Button 3", "thumb12_top_middle_right"),
                    ("4", "Button 4", "thumb12_top_right"),
                    ("5", "Button 5", "thumb12_middle_left"),
                    ("6", "Button 6", "thumb12_middle_middle_left"),
                    ("7", "Button 7", "thumb12_middle_middle_right"),
                    ("8", "Button 8", "thumb12_middle_right"),
                    ("9", "Button 9", "thumb12_bottom_left"),
                    ("10", "Button 10", "thumb12_bottom_middle_left"),
                    ("11", "Button 11", "thumb12_bottom_middle_right"),
                    ("12", "Button 12", "thumb12_bottom_right"),
                ]
            else:
                rows = []

        
        # Provide friendly labels for overlay tooltips
        try:
            if hasattr(self, "panel_preview") and self.panel_preview is not None:
                self.panel_preview.set_action_label_map({ak: lbl for (_n, lbl, ak) in rows})
        except Exception:
            pass

# Track widgets per action_key for hover highlighting
        self._binding_widgets = {}

        if not hasattr(self, "bindings_grid_layout"):
            return

        # Clear current UI
        while self.bindings_grid_layout.count():
            item = self.bindings_grid_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

        # Rows
        for disp_num, label, ak in rows:
            roww = QtWidgets.QFrame()
            roww.setFrameShape(QtWidgets.QFrame.StyledPanel)
            roww.setStyleSheet("QFrame{border:1px solid rgba(140,140,140,120); border-radius:6px;} QFrame[hl=\"true\"]{border-color: rgba(33,169,194,230); background: rgba(33,169,194,22);} ")
            roww.setProperty("hl","false")
            rhl = QtWidgets.QHBoxLayout(roww)
            rhl.setContentsMargins(10, 8, 10, 8)
            rhl.setSpacing(10)

            num_lab = QtWidgets.QLabel(str(disp_num))
            num_lab.setFixedWidth(34)
            num_lab.setAlignment(QtCore.Qt.AlignCenter)
            num_lab.setStyleSheet("font-weight:700;")
            rhl.addWidget(num_lab)

            bind_txt = QtWidgets.QLabel(human_from_binding(binds.get(ak, {})))
            bind_txt.setWordWrap(True)
            bind_txt.setStyleSheet("font-weight:600; opacity:0.9;")
            rhl.addWidget(bind_txt, 1)

            btn_set = QtWidgets.QPushButton("Set…")
            btn_set.setFixedWidth(84)
            btn_set.setStyleSheet('QPushButton[hl="true"]{background-color: rgba(33,169,194,30);} ')
            btn_set.setProperty("hl","false")
            btn_set.clicked.connect(lambda _=False, _ak=ak: self._edit_binding_action_key(_ak))
            rhl.addWidget(btn_set)

            btn_clear = QtWidgets.QPushButton("Clear")
            btn_clear.setFixedWidth(84)
            btn_clear.setStyleSheet('QPushButton[hl="true"]{background-color: rgba(33,169,194,30);} ')
            btn_clear.setProperty("hl","false")
            btn_clear.clicked.connect(lambda _=False, _ak=ak: self.on_clear_binding(_ak))
            rhl.addWidget(btn_clear)

            # register widgets for hover highlight
            self._binding_widgets[ak] = (roww, btn_set, btn_clear)
            for _w in (roww, btn_set, btn_clear):
                try:
                    _w.setProperty('action_key', ak)
                    _w.installEventFilter(self)
                except Exception:
                    pass

            self.bindings_grid_layout.addWidget(roww)

        self.bindings_grid_layout.addStretch(1)

        # Keep keyboard modified-keys list in sync with profile/layer selection
        try:
            self.refresh_keyboard_table()
        except Exception:
            pass


    def on_profile_changed(self, _):


        # Dropdown change should NOT activate the profile or apply RGB.


        # It only changes the UI editing/preview target (effective profile = base/sub selection).


        try:


            base = self.profile_combo.currentText() or "default"


        except Exception:


            base = "default"



        # Try to keep subprofile selection (if any) as the effective preview profile.


        effective = base


        try:


            if hasattr(self, "subprofile_combo"):


                sub = (self.subprofile_combo.currentText() or "").strip()


                subs = self._subprofiles_for_base(base)


                if sub and sub in subs:


                    effective = sub


        except Exception:


            pass



        # Sync keyboard tab base profile dropdown (without feedback loops)


        try:


            if hasattr(self, "kb_profile_combo"):


                if self.kb_profile_combo.currentText() != base:


                    try:


                        blocker = QtCore.QSignalBlocker(self.kb_profile_combo)


                        self.kb_profile_combo.setCurrentText(base)


                        del blocker


                    except Exception:


                        self.kb_profile_combo.setCurrentText(base)


        except Exception:


            pass



        # Update UI preview target


        try:


            self._set_ui_effective_profile(effective)


        except Exception:


            # Fallback: at least refresh tables


            try:


                self.refresh_table()


            except Exception:


                pass


            try:


                self.refresh_keyboard_table()


            except Exception:


                pass



        # Refresh per-profile sliders for the preview target


        try:


            self._sync_pointer_scale_ui()


            self._sync_scroll_scale_ui()


        except Exception:


            pass



        # Update RGB preview swatch only (do not apply)


        try:


            self._rgb_update_preview()


        except Exception:


            pass

    def _kb_on_profile_changed(self, prof: str) -> None:
        """Keyboard tab base profile dropdown: keep in sync with Mouse tab."""
        try:
            if not prof:
                return
            if hasattr(self, "profile_combo"):
                try:
                    blocker = QtCore.QSignalBlocker(self.profile_combo)
                    self.profile_combo.setCurrentText(prof)
                    del blocker
                except Exception:
                    self.profile_combo.setCurrentText(prof)
            self._on_base_profile_changed(prof)
        except Exception:
            pass

    def on_add_profile(self):
        name, ok = QtWidgets.QInputDialog.getText(self, "Add Profile", "Profile name:")
        if not ok:
            return
        name = (name or "").strip()
        if not name:
            return
        self.cfg.setdefault("profiles", {})
        if name in self.cfg["profiles"]:
            self.set_status("Profile already exists.", ok=False)
            return
        self.cfg["profiles"][name] = {
            "bindings": {},
            "modifier_layers": {"shift": {}, "ctrl": {}, "alt": {}},
        }
        self.refresh_profiles()
        self.profile_combo.setCurrentText(name)
        self.refresh_table()
        self.set_status("Profile added (not saved yet).", ok=True)

    def on_delete_profile(self):
        prof = self.current_profile()
        old_key = self._selected_macro_key
        profiles = list((self.cfg.get("profiles") or {}).keys())
        if len(profiles) <= 1:
            self.set_status("Cannot delete the last remaining profile.", ok=False)
            return

        res = QtWidgets.QMessageBox.question(
            self,
            "Delete Profile",
            f"Delete profile '{prof}'?\n\nBindings will be lost.",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
        )
        if res != QtWidgets.QMessageBox.Yes:
            return

        profs = self.cfg.get("profiles") or {}
        deleted_is_base = not bool(((profs.get(prof, {}).get("settings") or {}).get("subprofile_of")))

        # Remove the selected profile.
        profs.pop(prof, None)

        # If deleting a base profile, also remove its subprofiles so they cannot
        # linger in last_subprofiles or autoswitch resolution.
        if deleted_is_base:
            sub_to_del = []
            for name, pdata in list(profs.items()):
                settings = (pdata or {}).get("settings") or {}
                if str(settings.get("subprofile_of") or "") == prof:
                    sub_to_del.append(name)
            for name in sub_to_del:
                profs.pop(name, None)

        remaining = list(profs.keys())
        if self.cfg.get("active_profile") == prof or self.cfg.get("active_profile") not in profs:
            self.cfg["active_profile"] = remaining[0] if remaining else "default"

        # Remove autoswitch mappings that point to the deleted profile.
        app_profiles = self.cfg.get("app_profiles", {}) or {}
        to_del = [cls for cls, p in list(app_profiles.items()) if p == prof]
        for cls in to_del:
            app_profiles.pop(cls, None)
            (self.cfg.get("app_names", {}) or {}).pop(cls, None)
        self.cfg["app_profiles"] = app_profiles

        # Nested autoswitch schema compatibility.
        autoswitch = self.cfg.get("autoswitch") or {}
        if isinstance(autoswitch, dict):
            amap = autoswitch.get("app_profiles") or {}
            if isinstance(amap, dict):
                for cls, p in list(amap.items()):
                    if p == prof:
                        amap.pop(cls, None)
            if autoswitch.get("fallback_profile") == prof:
                autoswitch["fallback_profile"] = self.cfg.get("active_profile", "default")
            self.cfg["autoswitch"] = autoswitch

        # Prune remembered subprofiles that point to removed profiles or removed bases.
        last = self.cfg.get("last_subprofiles") or {}
        if isinstance(last, dict):
            last.pop(prof, None)
            for base, sub in list(last.items()):
                if base not in profs or sub not in profs:
                    last.pop(base, None)
            self.cfg["last_subprofiles"] = last

        # Persist immediately so closing/restarting the GUI cannot bring the profile back.
        atomic_write_json(self.config_path, self.cfg)

        self.refresh_profiles()
        self.refresh_table()
        self.refresh_mapping_table()
        try:
            self.refresh_keyboard_table()
        except Exception:
            pass
        self.set_status(f"Deleted profile '{prof}'.", ok=True)

    def on_set_active(self):
        # Commit the selected (possibly subprofile) as the active runtime profile.
        prof = self.current_profile()
        self.cfg["active_profile"] = prof

        # Manual lock rule:
        # - If you Set Active to the default profile, autoswitch remains allowed.
        # - If you Set Active to anything else, autoswitch is suppressed until you Set Active back to default.
        try:
            default_prof = str(self.cfg.get("default_profile") or self.cfg.get("fallback_profile") or "default")
        except Exception:
            default_prof = "default"
        try:
                        # If selecting a subprofile of the fallback/default base, do not lock autoswitch.
            try:
                profs = self.cfg.get("profiles") or {}
                base = (profs.get(prof, {}).get("settings") or {}).get("subprofile_of")
                if base and str(base) == str(default_prof):
                    prof_is_defaultish = True
                else:
                    prof_is_defaultish = (str(prof) == str(default_prof))
            except Exception:
                prof_is_defaultish = (str(prof) == str(default_prof))

            # Unlock autoswitch when selecting fallback/default (or its subprofiles); lock otherwise.
            self.cfg["manual_profile_lock"] = (not prof_is_defaultish)

        except Exception:
            pass


        # Persist last-used subprofile for its base profile (used by autoswitch).
        try:
            profs = self.cfg.get("profiles") or {}
            base = (profs.get(prof, {}).get("settings") or {}).get("subprofile_of")
            if base:
                self.cfg.setdefault("last_subprofiles", {})[str(base)] = prof
        except Exception:
            pass

        # Clear UI preview override now that we committed
        try:
            self._ui_effective_profile = None
        except Exception:
            pass

        # Keep global pointer_scale in sync with active profile as a fallback.
        try:
            ps = (
                (self.cfg.get("profiles") or {})
                .get(prof, {})
                .get("settings", {})
                .get("pointer_scale", None)
            )
            if ps is not None:
                self.cfg["pointer_scale"] = float(ps)
        except Exception:
            pass

        try:
            atomic_write_json(self.config_path, self.cfg)
            ok, msg = signal_mapper_reload(self.pidfile)
            self.set_status(f"Active profile set. ({msg})", ok=ok)
        except Exception as e:
            self.set_status(f"Failed to set active profile: {e}", ok=False)
            return

        # Apply RGB on activation only
        try:
            if (self.cfg.get("rgb") or {}).get("enabled"):
                self._rgb_apply_timer.start(120)
        except Exception:
            pass


    def on_edit_binding(self, row: int, col: int):
        prof = self.current_profile()
        old_key = self._selected_macro_key
        layer = self.current_layer()
        binds = self.profile_layer_bindings(prof, layer)

        action_key = self.table.item(row, 0).data(QtCore.Qt.UserRole)
        current = binds.get(action_key)

        dlg = BindDialog(self, current=current, action_key=action_key)
        r = dlg.exec()
        if r == QtWidgets.QDialog.Rejected:
            return

        res = dlg.result_binding()
        if res.binding is None:
            binds.pop(action_key, None)
        else:
            binds[action_key] = res.binding

        self.refresh_table()
        self.set_status("Binding changed (not saved yet).", ok=True)

    def _edit_binding_action_key(self, action_key: str) -> None:
        prof = self.current_profile()
        layer = self.current_layer()
        binds = self.profile_layer_bindings(prof, layer)
        current = binds.get(action_key)
        dlg = BindDialog(self, current=current, action_key=action_key)
        r = dlg.exec()
        if r == QtWidgets.QDialog.Rejected:
            return
        res = dlg.result_binding()
        if res.binding is None:
            binds.pop(action_key, None)
        else:
            binds[action_key] = res.binding
        self.refresh_table()
        self.set_status("Binding changed (not saved yet).", ok=True)

    # auto-switch tab
    def on_auto_switch_toggled(self, v: bool):
        self.cfg["auto_switch_enabled"] = bool(v)
        self.set_status("Auto-switch toggled (not saved yet).", ok=True)

    def refresh_mapping_table(self):
        app_profiles = self.cfg.get("app_profiles", {}) or {}
        app_names = self.cfg.get("app_names", {}) or {}

        rows = sorted(app_profiles.keys(), key=_synapse_name_sort_key)
        self.map_table.setRowCount(len(rows))
        for r, cls in enumerate(rows):
            prof = app_profiles.get(cls, "")
            friendly = app_names.get(cls, "")
            self.map_table.setItem(r, 0, QtWidgets.QTableWidgetItem(cls))
            self.map_table.setItem(r, 1, QtWidgets.QTableWidgetItem(friendly))
            self.map_table.setItem(r, 2, QtWidgets.QTableWidgetItem(prof))

        self.map_table.resizeColumnsToContents()

    def on_use_active_window(self):
        cls = self._last_active_class
        if not cls:
            self.set_status("No active window class detected yet.", ok=False)
            return
        self.class_edit.setText(cls)
        friendly = (self.cfg.get("app_names", {}) or {}).get(cls, "")
        if friendly:
            self.friendly_edit.setText(friendly)
        self.set_status("Filled classname from active window.", ok=True)

    def on_add_update_mapping(self):
        cls = (self.class_edit.text() or "").strip()
        if not cls:
            self.set_status("Window class is required.", ok=False)
            return
        prof = (self.profile_pick.currentText() or "").strip()
        if not prof:
            self.set_status("Pick a profile.", ok=False)
            return

        self.cfg.setdefault("app_profiles", {})
        self.cfg["app_profiles"][cls] = prof

        friendly = (self.friendly_edit.text() or "").strip()
        self.cfg.setdefault("app_names", {})
        if friendly:
            self.cfg["app_names"][cls] = friendly
        else:
            self.cfg["app_names"].pop(cls, None)

        self.refresh_mapping_table()
        self.set_status("Mapping updated (not saved yet).", ok=True)

    def on_delete_mapping(self):
        row = self.map_table.currentRow()
        if row < 0:
            self.set_status("Select a mapping row first.", ok=False)
            return
        cls_item = self.map_table.item(row, 0)
        if not cls_item:
            return
        cls = cls_item.text()

        res = QtWidgets.QMessageBox.question(
            self,
            "Delete Mapping",
            f"Delete mapping for '{cls}'?",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
        )
        if res != QtWidgets.QMessageBox.Yes:
            return

        (self.cfg.get("app_profiles", {}) or {}).pop(cls, None)
        (self.cfg.get("app_names", {}) or {}).pop(cls, None)
        self.refresh_mapping_table()
        self.set_status("Mapping deleted (not saved yet).", ok=True)

    def on_mapping_selected(self):
        row = self.map_table.currentRow()
        if row < 0:
            return
        cls = self.map_table.item(row, 0).text()
        friendly = self.map_table.item(row, 1).text()
        prof = self.map_table.item(row, 2).text()

        self.class_edit.setText(cls)
        self.friendly_edit.setText(friendly)
        idx = self.profile_pick.findText(prof)
        if idx >= 0:
            self.profile_pick.setCurrentIndex(idx)

    def on_recent_clicked(self, item: QtWidgets.QListWidgetItem):
        cls = item.text()
        self.class_edit.setText(cls)
        friendly = (self.cfg.get("app_names", {}) or {}).get(cls, "")
        if friendly:
            self.friendly_edit.setText(friendly)

    def _push_recent_class(self, cls: str):
        if not cls:
            return
        if cls in self._recent_classes:
            self._recent_classes.remove(cls)
        self._recent_classes.insert(0, cls)
        self._recent_classes = self._recent_classes[:25]

        self.recent_list.clear()
        self.recent_list.addItems(self._recent_classes)

    def poll_active_window_class(self):
        try:
            out = (
                subprocess.check_output(
                    ["kdotool", "getactivewindow", "getwindowclassname"],
                    stderr=subprocess.DEVNULL,
                )
                .decode()
                .strip()
            )
            cls = out or None
        except Exception:
            cls = None

        self._last_active_class = cls
        self.active_class_lbl.setText(f"Active window class: {cls or '(unknown)'}")
        if cls:
            self._push_recent_class(cls)

    # ---------- RGB (OpenRGB sync) ----------

    def _rgb_status(self, msg: str, ok: bool = True):
        self.rgb_status_lbl.setText(msg)
        pal = self.rgb_status_lbl.palette()
        pal.setColor(
            QtGui.QPalette.WindowText, QtGui.QColor("#00ff00" if ok else "#ff5555")
        )
        self.rgb_status_lbl.setPalette(pal)

    def _openrgb_candidate_cmds(self) -> List[List[str]]:
        """Return candidate commands to invoke OpenRGB CLI.

        If NAGA_OPENRGB_PREFER_FLATPAK=1, use Flatpak **only** (no fallback), to avoid accidentally calling an older system OpenRGB.
        """
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

    def _openrgb_base_cmd(self) -> Optional[List[str]]:
        cmds = self._openrgb_candidate_cmds()
        return cmds[0] if cmds else None

    def _run_openrgb(self, args: List[str], timeout: int = 3) -> Tuple[bool, str]:
        cmds = self._openrgb_candidate_cmds()
        if not cmds:
            return (
                False,
                "OpenRGB CLI not found (install OpenRGB or Flatpak org.openrgb.OpenRGB).",
            )

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
            return p.returncode, (msg or f"OpenRGB returned rc={p.returncode}")

        last_msg = ""
        last_base = ""
        for base in cmds:
            last_base = " ".join(base)
            try:
                rc, msg = run_once(base, timeout)
                last_msg = msg
                if rc == 0:
                    return True, msg

                if "mouse connection attempt failed" in (msg or "").lower():
                    rc2, msg2 = run_once(base, max(timeout, 10))
                    last_msg = msg2
                    if rc2 == 0:
                        return True, msg2

            except subprocess.TimeoutExpired:
                if is_apply_like(args):
                    return (
                        True,
                        f"[{last_base}] applied (command timed out after {timeout}s)",
                    )
                last_msg = "OpenRGB command timed out."
                continue
            except Exception as e:
                last_msg = f"OpenRGB error: {e}"
                continue

        if last_base:
            return False, f"[{last_base}] {last_msg or 'OpenRGB failed.'}"
        return False, last_msg or "OpenRGB failed."

    def _parse_openrgb_list(self, text_out: str) -> List[Tuple[int, str]]:
        """Parse 'openrgb -l' output into (index, name)."""
        devs: List[Tuple[int, str]] = []
        for line in (text_out or "").splitlines():
            # common formats look like: '0: <name>' or '[0] <name>'
            line = line.strip()
            if not line:
                continue
            m = re.match(r"^(\d+)\s*:\s*(.+)$", line)
            if not m:
                m = re.match(r"^\[(\d+)\]\s*(.+)$", line)
            if m:
                try:
                    idx = int(m.group(1))
                    name = m.group(2).strip()
                    devs.append((idx, name))
                except Exception:
                    continue
        return devs

    def on_rgb_refresh_devices(self, checked: bool = False, silent: bool = False):
        ok, msg = self._run_openrgb(["-l"], timeout=4)
        if not ok:
            if not silent:
                self._rgb_status(msg, ok=False)
            return

        devs = self._parse_openrgb_list(msg)
        if not devs:
            # some OpenRGB builds print list to stderr; retry by using msg already includes stderr in our runner.
            if not silent:
                self._rgb_status(
                    "No devices parsed from OpenRGB. OpenRGB output:\n" + msg, ok=False
                )
            return

        self.rgb_mouse_combo.blockSignals(True)
        self.rgb_kb_combo.blockSignals(True)

        self.rgb_mouse_combo.clear()
        self.rgb_kb_combo.clear()

        for idx, name in devs:
            self.rgb_mouse_combo.addItem(f"{idx}: {name}", idx)
            self.rgb_kb_combo.addItem(f"{idx}: {name}", idx)

        rgb_cfg = self.cfg.get("rgb") or {}
        mouse_idx = rgb_cfg.get("mouse_device")
        kb_idx = rgb_cfg.get("keyboard_device")

        def select_combo(combo: QtWidgets.QComboBox, wanted):
            if wanted is None:
                return
            for i in range(combo.count()):
                if combo.itemData(i) == wanted:
                    combo.setCurrentIndex(i)
                    return

        select_combo(self.rgb_mouse_combo, mouse_idx)
        select_combo(self.rgb_kb_combo, kb_idx)

        self.rgb_mouse_combo.blockSignals(False)
        self.rgb_kb_combo.blockSignals(False)

        if not silent:
            self._rgb_status(f"Loaded {len(devs)} OpenRGB device(s).", ok=True)

    def on_rgb_enable_toggled(self, v: bool):
        self.cfg.setdefault("rgb", {}).setdefault("per_profile", {})
        self.cfg["rgb"]["enabled"] = bool(v)
        self.set_status("RGB sync toggled (not saved yet).", ok=True)
        if v:
            self._rgb_apply_timer.start(120)

    def on_rgb_idle_toggled(self, v: bool):
        idle = self.cfg.setdefault(
            "rgb_idle",
            {
                "enabled": False,
                "timeout_seconds": 600,
                "wake_on_activity": True,
                "off_enabled": False,
                "off_after_seconds": 600,
            },
        )
        b = bool(v)
        # New keys
        idle["off_enabled"] = b
        idle["off_after_seconds"] = int(
            idle.get("off_after_seconds", idle.get("timeout_seconds", 600)) or 600
        )
        # Back-compat keys (older mappers)
        idle["enabled"] = b
        idle["timeout_seconds"] = int(
            idle.get("off_after_seconds", idle.get("timeout_seconds", 600)) or 600
        )
        self.set_status("RGB idle setting updated (not saved yet).", ok=True)

    def on_rgb_idle_timeout_changed(self, seconds: int):
        idle = self.cfg.setdefault(
            "rgb_idle",
            {
                "enabled": False,
                "timeout_seconds": 600,
                "wake_on_activity": True,
                "off_enabled": False,
                "off_after_seconds": 600,
            },
        )
        try:
            s = int(seconds)
        except Exception:
            s = 600
        s = max(1, min(86400, s))
        # New keys
        idle["off_after_seconds"] = int(s)
        # Back-compat key
        idle["timeout_seconds"] = int(s)
        self.set_status("RGB idle timeout updated (not saved yet).", ok=True)

    def on_rgb_idle_wake_toggled(self, v: bool):
        idle = self.cfg.setdefault(
            "rgb_idle",
            {
                "enabled": False,
                "timeout_seconds": 600,
                "wake_on_activity": True,
                "off_enabled": False,
                "off_after_seconds": 600,
            },
        )
        idle["wake_on_activity"] = bool(v)
        self.set_status("RGB idle wake setting updated (not saved yet).", ok=True)

    def on_rgb_device_changed(self, _=None):
        rgb = self.cfg.setdefault(
            "rgb",
            {
                "enabled": False,
                "mouse_device": None,
                "keyboard_device": None,
                "brightness": 100,
                "per_profile": {},
            },
        )
        rgb["mouse_device"] = self.rgb_mouse_combo.currentData()
        rgb["keyboard_device"] = self.rgb_kb_combo.currentData()
        self.set_status("RGB device selection updated (not saved yet).", ok=True)
        if rgb.get("enabled"):
            self._rgb_apply_timer.start(120)

    def on_rgb_brightness_slider(self, v: int):
        # keep spinbox in sync
        self.rgb_bright_spin.blockSignals(True)
        self.rgb_bright_spin.setValue(int(v))
        self.rgb_bright_spin.blockSignals(False)

        rgb = self.cfg.setdefault(
            "rgb",
            {
                "enabled": False,
                "mouse_device": None,
                "keyboard_device": None,
                "brightness": 100,
                "per_profile": {},
            },
        )
        rgb["brightness"] = int(v)
        self.set_status("RGB brightness updated (not saved yet).", ok=True)
        if rgb.get("enabled"):
            self._rgb_apply_timer.start(120)

    def on_rgb_brightness_spin(self, v: int):
        # keep slider in sync
        self.rgb_bright_slider.blockSignals(True)
        self.rgb_bright_slider.setValue(int(v))
        self.rgb_bright_slider.blockSignals(False)

        rgb = self.cfg.setdefault(
            "rgb",
            {
                "enabled": False,
                "mouse_device": None,
                "keyboard_device": None,
                "brightness": 100,
                "per_profile": {},
            },
        )
        rgb["brightness"] = int(v)
        self.set_status("RGB brightness updated (not saved yet).", ok=True)
        if rgb.get("enabled"):
            self._rgb_apply_timer.start(120)

    def on_rgb_brightness_commit(self):
        try:
            rgb = self.cfg.setdefault(
                "rgb",
                {
                    "enabled": False,
                    "mouse_device": None,
                    "keyboard_device": None,
                    "brightness": 100,
                    "per_profile": {},
                },
            )
            if rgb.get("enabled"):
                self._rgb_apply_timer.start(120)
        except Exception as e:
            self.set_status(f"RGB apply failed: {e}", ok=False)

    def _rgb_apply_debounced(self):
        try:
            rgb = self.cfg.setdefault(
                "rgb",
                {
                    "enabled": False,
                    "mouse_device": None,
                    "keyboard_device": None,
                    "brightness": 100,
                    "per_profile": {},
                },
            )
            if rgb.get("enabled"):
                self._rgb_worker = _RGBApplyWorker(self, self._apply_rgb_background)
                self._rgb_worker.done.connect(self._on_rgb_worker_done)
                self._rgb_worker.start()
        except Exception as e:
            self.set_status(f"RGB apply failed: {e}", ok=False)

    def _rgb_color_for_profile(self, profile: str) -> str:
        rgb = self.cfg.setdefault(
            "rgb",
            {
                "enabled": False,
                "mouse_device": None,
                "keyboard_device": None,
                "brightness": 100,
                "per_profile": {},
            },
        )
        per = rgb.setdefault("per_profile", {})
        return str(per.get(profile, "#000000"))

    def _rgb_update_preview(self):
        col = self._rgb_color_for_profile(self._rgb_target_profile())
        self.rgb_color_preview.setStyleSheet(
            f"background: {col}; border: 1px solid #444;"
        )

        # brightness UI
        rgb = self.cfg.get("rgb") or {}
        try:
            b = int(rgb.get("brightness", 100))
        except Exception:
            b = 100
        b = max(0, min(100, b))

        self.rgb_bright_slider.blockSignals(True)
        self.rgb_bright_spin.blockSignals(True)
        self.rgb_bright_slider.setValue(b)
        self.rgb_bright_spin.setValue(b)
        self.rgb_bright_slider.blockSignals(False)
        self.rgb_bright_spin.blockSignals(False)

    def on_rgb_pick_color(self):
        current = QtGui.QColor(self._rgb_color_for_profile(self._rgb_target_profile()))
        col = QtWidgets.QColorDialog.getColor(current, self, "Choose profile color")
        if not col.isValid():
            return
        hexcol = col.name()
        rgb = self.cfg.setdefault(
            "rgb",
            {
                "enabled": False,
                "mouse_device": None,
                "keyboard_device": None,
                "brightness": 100,
                "per_profile": {},
            },
        )
        per = rgb.setdefault("per_profile", {})
        per[self._rgb_target_profile()] = hexcol
        # Update only the RGB tab preview here. Do not live-apply on color pick,
        # otherwise a pending apply can make Save RGB look like it changed the
        # active devices even though the user only wanted to store the color.
        self._rgb_update_preview()
        try:
            self._rgb_apply_timer.stop()
        except Exception:
            pass
        self.set_status("RGB color updated (not saved or applied yet).", ok=True)

    def _hex_to_rgb(self, hexcol: str) -> Optional[Tuple[int, int, int]]:
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

    def apply_rgb_for_current_profile(self) -> None:
        rgb = self.cfg.get("rgb") or {}
        if not rgb.get("enabled"):
            return

        mouse_idx = rgb.get("mouse_device")
        kb_idx = rgb.get("keyboard_device")
        if mouse_idx is None or kb_idx is None:
            self._rgb_status("Pick mouse + keyboard devices, then Apply.", ok=False)
            return

        hexcol = self._rgb_color_for_profile(self._rgb_target_profile())
        rgbv = self._hex_to_rgb(hexcol)
        if not rgbv:
            self._rgb_status(f"Invalid color: {hexcol}", ok=False)
            return
        r, g, b = rgbv
        color_arg = f"{r:02X}{g:02X}{b:02X}"

        # Apply to both devices
        brightness = rgb.get("brightness", 100)
        try:
            brightness = int(brightness)
        except Exception:
            brightness = 100
        brightness = max(0, min(100, brightness))

        ok1, msg1 = self._run_openrgb(
            [
                "-d",
                str(mouse_idx),
                "--mode",
                "direct",
                "-c",
                color_arg,
                "-b",
                str(brightness),
            ],
            timeout=3,
        )
        ok2, msg2 = self._run_openrgb(
            [
                "-d",
                str(kb_idx),
                "--mode",
                "direct",
                "-c",
                color_arg,
                "-b",
                str(brightness),
            ],
            timeout=3,
        )

        if ok1 and ok2:
            self._rgb_status(
                f"Applied {hexcol} @ {brightness}% to mouse({mouse_idx}) + keyboard({kb_idx}).",
                ok=True,
            )
        else:
            self._rgb_status(f"Apply failed. Mouse: {msg1}  Keyboard: {msg2}", ok=False)

    def on_rgb_apply_now(self):
        self._rgb_apply_timer.start(120)

    # performance tab
    def _sync_pointer_scale_ui(self):
        try:
            prof = self.current_profile()
            prof_scale = (
                (self.cfg.get("profiles") or {})
                .get(prof, {})
                .get("settings", {})
                .get("pointer_scale", None)
            )
            if prof_scale is not None:
                scale = float(prof_scale)
            else:
                scale = float(self.cfg.get("pointer_scale", 1.0))
        except Exception:
            scale = 1.0
        scale = max(0.10, min(3.00, scale))

        self.scale_slider.blockSignals(True)
        self.scale_spin.blockSignals(True)
        self.scale_slider.setValue(int(round(scale * 100)))
        self.scale_spin.setValue(scale)
        self.scale_slider.blockSignals(False)
        self.scale_spin.blockSignals(False)

    def _sync_scroll_scale_ui(self):
        try:
            prof = self.current_profile()
            prof_scale = (
                (self.cfg.get("profiles") or {})
                .get(prof, {})
                .get("settings", {})
                .get("scroll_scale", None)
            )
            if prof_scale is not None:
                scale = float(prof_scale)
            else:
                scale = float(self.cfg.get("scroll_scale", 1.0))
        except Exception:
            scale = 1.0
        scale = max(0.10, min(3.00, scale))

        self.scroll_slider.blockSignals(True)
        self.scroll_spin.blockSignals(True)
        self.scroll_slider.setValue(int(round(scale * 100)))
        self.scroll_spin.setValue(scale)
        self.scroll_slider.blockSignals(False)
        self.scroll_spin.blockSignals(False)

    def on_scroll_slider(self, v: int):
        val = float(v) / 100.0
        try:
            self.scroll_spin.blockSignals(True)
            self.scroll_spin.setValue(val)
        finally:
            try:
                self.scroll_spin.blockSignals(False)
            except Exception:
                pass

        prof = self.current_profile()
        self.cfg.setdefault("profiles", {}).setdefault(prof, {}).setdefault("settings", {})[
            "scroll_scale"
        ] = float(val)
        self.cfg["scroll_scale"] = float(val)

        try:
            self.set_status("Scroll speed updated (not applied yet).", ok=True)
        except Exception:
            pass

    def on_scroll_spin(self, val: float):
        try:
            self.scroll_slider.blockSignals(True)
            self.scroll_slider.setValue(int(round(float(val) * 100)))
        finally:
            try:
                self.scroll_slider.blockSignals(False)
            except Exception:
                pass

        prof = self.current_profile()
        self.cfg.setdefault("profiles", {}).setdefault(prof, {}).setdefault("settings", {})[
            "scroll_scale"
        ] = float(val)
        self.cfg["scroll_scale"] = float(val)

        try:
            self.set_status("Scroll speed updated (not applied yet).", ok=True)
        except Exception:
            pass

    def on_scale_slider(self, v: int):
        # Slider uses percent (e.g. 100 == 1.0)
        val = float(v) / 100.0
        # Keep spin in sync without recursion
        try:
            self.scale_spin.blockSignals(True)
            self.scale_spin.setValue(val)
        finally:
            try:
                self.scale_spin.blockSignals(False)
            except Exception:
                pass

        # Store into current profile settings (and global fallback)
        prof = self.current_profile()
        self.cfg.setdefault("profiles", {}).setdefault(prof, {}).setdefault(
            "settings", {}
        )["pointer_scale"] = float(val)
        self.cfg["pointer_scale"] = float(val)

        try:
            self.set_status("Pointer scale updated (not applied yet).", ok=True)
        except Exception:
            pass

    def on_scale_spin(self, val: float):
        # Keep slider in sync without recursion
        try:
            self.scale_slider.blockSignals(True)
            self.scale_slider.setValue(int(round(float(val) * 100)))
        finally:
            try:
                self.scale_slider.blockSignals(False)
            except Exception:
                pass

        # Store into current profile settings (and global fallback)
        prof = self.current_profile()
        self.cfg.setdefault("profiles", {}).setdefault(prof, {}).setdefault(
            "settings", {}
        )["pointer_scale"] = float(val)
        self.cfg["pointer_scale"] = float(val)

        try:
            self.set_status("Pointer scale updated (not applied yet).", ok=True)
        except Exception:
            pass

    def on_performance_apply(self):
        # Ensure active_profile matches current selection
        try:
            self.cfg["active_profile"] = self.current_profile()
        except Exception:
            pass

        # Ensure pointer_scale is stored in the current profile settings before saving
        try:
            prof = self.current_profile()
            val = float(self.scale_spin.value())
            self.cfg.setdefault("profiles", {}).setdefault(prof, {}).setdefault(
                "settings", {}
            )["pointer_scale"] = float(val)
            self.cfg["pointer_scale"] = float(val)
        except Exception:
            pass

        self.save_config(reload_mapper=True)
        try:
            self.set_status("Performance settings applied.", ok=True)
        except Exception:
            pass

    def save_config(self, reload_mapper: bool = True):
        """Write config to disk and optionally reload the mapper."""
        atomic_write_json(self.config_path, self.cfg)
        if reload_mapper:
            ok, msg = signal_mapper_reload(self.pidfile)
            self.set_status(f"Saved. ({msg})", ok=ok)
        else:
            self.set_status("Saved.", ok=True)

    def on_save_apply(self):
        atomic_write_json(self.config_path, self.cfg)
        # Prefer a reload signal (fast), but also restart the service so changes always take effect
        # even when the mapper isn't using/creating the expected pidfile.
        ok_sig, msg_sig = signal_mapper_reload(self.pidfile)
        ok_rst, msg_rst = run_systemctl_user(["restart", SERVICE_NAME])
        ok = bool(ok_sig) or bool(ok_rst)
        msg = f"reload: {msg_sig}; restart: {msg_rst}"
        self.set_status(f"Saved changes. ({msg})", ok=ok)

    def on_rgb_save_only(self):
        target = self._rgb_target_profile()
        # Saving RGB should never trigger a delayed live apply from an earlier
        # color/device/brightness edit.
        try:
            self._rgb_apply_timer.stop()
        except Exception:
            pass
        try:
            with open(self.config_path, "r", encoding="utf-8") as f:
                disk_cfg = json.load(f)
            if not isinstance(disk_cfg, dict):
                disk_cfg = {}
        except Exception:
            disk_cfg = {}

        rgb_src = self.cfg.get("rgb") or {}
        rgb_dst = disk_cfg.setdefault("rgb", {})
        rgb_dst["enabled"] = bool(rgb_src.get("enabled", False))
        rgb_dst["mouse_device"] = rgb_src.get("mouse_device")
        rgb_dst["keyboard_device"] = rgb_src.get("keyboard_device")
        try:
            rgb_dst["brightness"] = int(rgb_src.get("brightness", 100))
        except Exception:
            rgb_dst["brightness"] = 100

        per_src = rgb_src.get("per_profile") or {}
        per_dst = rgb_dst.setdefault("per_profile", {})
        if not isinstance(per_dst, dict):
            per_dst = {}
            rgb_dst["per_profile"] = per_dst
        per_dst[target] = str(per_src.get(target, "#000000"))

        atomic_write_json(self.config_path, disk_cfg)
        self.set_status(f"Saved RGB for {target}.", ok=True)

    def _apply_rgb_background(self):
        try:
            self.apply_rgb_for_current_profile()
            return True, "ok"
        except Exception as e:
            return False, str(e)

    def _on_rgb_worker_done(self, ok, msg):
        if not ok:
            self.set_status(f"RGB apply failed: {msg}", ok=False)

    # ------------------------
    # Macros UI helpers
    # ------------------------
    def _combo_set_best(self, combo: QtWidgets.QComboBox, preferred: List[str]) -> None:
        """Set combo current index by trying preferred labels (case-insensitive), else no-op."""
        try:
            items = [combo.itemText(i) for i in range(combo.count())]
            low = [s.lower() for s in items]
            for p in preferred:
                if p is None:
                    continue
                ps = str(p)
                if ps in items:
                    combo.setCurrentIndex(items.index(ps))
                    return
                if ps.lower() in low:
                    combo.setCurrentIndex(low.index(ps.lower()))
                    return
        except Exception:
            return

    def _macro_update_repeat_count_limits(self) -> None:
        """Adjust repeat count spinbox behavior based on repeat mode."""
        try:
            mode_txt = str(self.macro_repeat_mode.currentText() or "").strip().lower()
        except Exception:
            mode_txt = ""

        if "none" in mode_txt or mode_txt.strip() in ("", "off"):
            try:
                self.macro_repeat_count.setEnabled(False)
                self.macro_repeat_gap_ms.setEnabled(False)
            except Exception:
                pass
            return

        try:
            self.macro_repeat_count.setEnabled(True)
            self.macro_repeat_gap_ms.setEnabled(True)
        except Exception:
            pass

        is_n = ("n times" in mode_txt) or (
            mode_txt.strip() in ("n", "n_times", "ntimes")
        )
        try:
            self.macro_repeat_count.setMinimum(1 if is_n else 0)
            self.macro_repeat_count.setSpecialValueText("" if is_n else "∞")
            if is_n and int(self.macro_repeat_count.value()) == 0:
                self.macro_repeat_count.setValue(1)
        except Exception:
            pass

        return

    def _macro_schedule_autosave(self, *args) -> None:
        # Don't autosave while we are loading values into widgets
        if getattr(self, "_macro_loading", False):
            return
        if not getattr(self, "_selected_macro_key", None):
            return
        # debounce writes/reloads
        self._macro_autosave_timer.start(350)

    def _macro_autosave_now(self) -> None:
        if getattr(self, "_macro_loading", False):
            return
        if not getattr(self, "_selected_macro_key", None):
            return
        try:
            self._macro_save_current()
            if hasattr(self, "reload_config_from_disk"):
                self.reload_config_from_disk()
        except Exception:
            # autosave should never crash the UI
            pass

    def _macro_find_tree_item_by_key(self, key: str):
        root = self.macro_tree.invisibleRootItem()
        stack = [root]
        while stack:
            parent = stack.pop()
            for i in range(parent.childCount()):
                ch = parent.child(i)
                try:
                    if ch.data(0, QtCore.Qt.UserRole) == key:
                        return ch
                except Exception:
                    pass
                stack.append(ch)
        return None

    def refresh_macro_tree(self, preserve_key: Optional[str] = None) -> None:
        self.macro_tree.clear()
        prof = self.current_profile()
        old_key = self._selected_macro_key
        macros = self.profile_macros(prof)

        folders: Dict[str, QtWidgets.QTreeWidgetItem] = {}

        for key in sorted(macros.keys()):
            m = macros.get(key) or {}
            folder = str(m.get("folder") or "").strip() or "Unsorted"
            name = str(m.get("name") or key.split("/")[-1])

            if folder not in folders:
                fi = QtWidgets.QTreeWidgetItem([folder])
                fi.setData(0, QtCore.Qt.UserRole, None)
                self.macro_tree.addTopLevelItem(fi)
                folders[folder] = fi
            else:
                fi = folders[folder]

            if bool(m.get("hidden")):
                continue

            mi = QtWidgets.QTreeWidgetItem([name])
            mi.setData(0, QtCore.Qt.UserRole, key)
            fi.addChild(mi)

        self.macro_tree.expandAll()

        # Preserve selection (prevents highlight disappearing and keeps autosave working)
        key_to_restore = preserve_key or getattr(self, "_selected_macro_key", None)
        if key_to_restore:
            it = self._macro_find_tree_item_by_key(str(key_to_restore))
            if it is not None:
                self.macro_tree.setCurrentItem(it)
                # Force editor to reload from config for the restored selection
                try:
                    self._on_macro_selected()
                except Exception:
                    pass

    def _on_macro_selected(self) -> None:
        items = self.macro_tree.selectedItems()
        if not items:
            self._selected_macro_key = None
            return

        it = items[0]
        key = it.data(0, QtCore.Qt.UserRole)

        # If a folder (no key) was selected, auto-select its first child (if any)
        if not key:
            if it.childCount() > 0:
                self.macro_tree.setCurrentItem(it.child(0))
            return

        self._selected_macro_key = str(key)
        # Reload from disk to ensure editor reflects the truly saved macro values
        if hasattr(self, "reload_config_from_disk"):
            self.reload_config_from_disk()
        prof = self.current_profile()
        old_key = self._selected_macro_key
        m = self.profile_macros(prof).get(self._selected_macro_key) or {}

        # Prevent autosave while we populate widgets
        self._macro_loading = True
        try:
            self.macro_name_edit.setText(str(m.get("name") or ""))
            self.macro_folder_edit.setText(str(m.get("folder") or ""))
            # Stop mode (robust label match)
            _sm = str(m.get("stop_mode") or "finish")
            _sm_norm = _sm.strip().lower().replace(" ", "_")
            stop_label = (
                "On release"
                if _sm_norm in ("on_release", "while_held", "hold")
                else "Finish"
            )
            self._combo_set_best(self.macro_stop_mode, [stop_label, _sm, _sm_norm])
            timing = m.get("timing") or {}
            tmode = str(timing.get("mode") or "recorded")
            self._combo_set_best(
                self.macro_timing_mode,
                [tmode, tmode.title(), "Recorded", "recorded", "Fixed", "fixed"],
            )
            self.macro_fixed_ms.setValue(int(timing.get("fixed_ms") or 50))
            repeat = m.get("repeat") or {}
            _rm = str(repeat.get("mode") or "none")
            _rm_norm = _rm.strip().lower().replace(" ", "_")
            if _rm_norm in ("none", "once"):
                rlabel = "None"
            elif _rm_norm in ("n", "n_times", "ntimes"):
                rlabel = "N times"
            elif _rm_norm in ("while_held", "whileheld", "hold"):
                rlabel = "While held"
            elif _rm_norm == "toggle":
                rlabel = "Toggle"
            else:
                rlabel = "None"
            self._combo_set_best(
                self.macro_repeat_mode, [rlabel, _rm, _rm_norm, "N", "N times"]
            )
            try:
                self.macro_repeat_count.setValue(
                    int(repeat.get("count") if repeat.get("count") is not None else 1)
                )
            except Exception:
                self.macro_repeat_count.setValue(1)
            try:
                self.macro_repeat_gap_ms.setValue(int(repeat.get("delay_ms") or 0))
            except Exception:
                self.macro_repeat_gap_ms.setValue(0)
            self.macro_no_overlap.setChecked(bool(m.get("no_overlap", True)))
            self._load_steps_table(m.get("steps") or [])
        finally:
            self._macro_loading = False

    def _load_steps_table(self, steps: List[Dict[str, Any]]) -> None:
        self.macro_steps.setRowCount(0)
        for st in steps:
            self._append_step_row(st)

    def _append_step_row(self, st: Dict[str, Any]) -> None:
        r = self.macro_steps.rowCount()
        self.macro_steps.insertRow(r)
        t = str(st.get("type") or "")
        code = ""
        down = ""
        dms = ""
        if t in ("sleep", "delay"):
            dms = str(int(st.get("ms") or st.get("delay_ms") or 0))
        elif t == "text":
            code = str(st.get("text") or "")
        else:
            code = str(st.get("code") or "")
            down = "1" if bool(st.get("down", True)) else "0"
        for c, val in enumerate([t, code, down, dms]):
            it = QtWidgets.QTableWidgetItem(val)
            self.macro_steps.setItem(r, c, it)

    def _macro_new_folder(self) -> None:
        folder, ok = QtWidgets.QInputDialog.getText(self, "New Folder", "Folder name:")
        if not ok:
            return
        folder = (folder or "").strip()
        if not folder:
            return
        # Create as an empty folder node by refreshing tree (folders exist if any macro uses them)
        # We'll just create a placeholder macro-less folder by adding a hidden entry
        prof = self.current_profile()
        old_key = self._selected_macro_key
        macros = self.profile_macros(prof)
        key = f"{folder}/_placeholder"
        if key not in macros:
            macros[key] = {
                "name": "_placeholder",
                "folder": folder,
                "steps": [],
                "hidden": True,
            }
        self.save_config()
        self.refresh_macro_tree(preserve_key=self._selected_macro_key)

    def _macro_new_macro(self) -> None:
        name, ok = QtWidgets.QInputDialog.getText(self, "New Macro", "Macro name:")
        if not ok:
            return
        name = (name or "").strip()
        if not name:
            return
        folder, ok2 = QtWidgets.QInputDialog.getText(
            self, "Folder", "Folder (e.g. WoW):"
        )
        if not ok2:
            return
        folder = (folder or "").strip() or "Unsorted"
        key = f"{folder}/{name}"
        prof = self.current_profile()
        macros = self.profile_macros(prof)

        # Create macro with clean defaults (do NOT inherit editor state)
        macros[key] = {
            "name": name,
            "folder": folder,
            "steps": [],
            "stop_mode": "on_release",
            "timing": {"mode": "recorded", "fixed_ms": 50},
            "repeat": {"mode": "none", "count": 1, "delay_ms": 0},
            "no_overlap": True,
        }

        # Select and load the new macro; guard against autosave while loading
        self._selected_macro_key = key
        self._macro_loading = True
        try:
            self.macro_name_edit.setText(name)
            self.macro_folder_edit.setText(folder)

            # Defaults in widgets
            try:
                i = self.macro_stop_mode.findText("On release")
                if i < 0:
                    i = self.macro_stop_mode.findText("on_release")
                if i >= 0:
                    self.macro_stop_mode.setCurrentIndex(i)
            except Exception:
                pass

            try:
                i = self.macro_timing_mode.findText("Recorded")
                if i < 0:
                    i = self.macro_timing_mode.findText("recorded")
                if i >= 0:
                    self.macro_timing_mode.setCurrentIndex(i)
            except Exception:
                pass

            try:
                self.macro_fixed_ms.setValue(50)
            except Exception:
                pass

            try:
                i = self.macro_repeat_mode.findText("None")
                if i < 0:
                    i = self.macro_repeat_mode.findText("none")
                if i >= 0:
                    self.macro_repeat_mode.setCurrentIndex(i)
            except Exception:
                pass

            try:
                self.macro_repeat_count.setValue(1)
                self.macro_repeat_gap_ms.setValue(0)
            except Exception:
                pass

            try:
                self.macro_no_overlap.setChecked(True)
            except Exception:
                pass

            try:
                self.macro_steps.setRowCount(0)
            except Exception:
                pass
        finally:
            self._macro_loading = False

        self._macro_update_repeat_count_limits()

        self.save_config()
        self.refresh_macro_tree(preserve_key=key)

    def _macro_delete(self) -> None:
        """Delete selected macro.

        Robust for legacy keys:
        - macros stored as plain name (no folder in key)
        - macros stored as folder/name
        """
        key = getattr(self, "_selected_macro_key", None)
        if not key:
            return

        prof = self.current_profile()
        macros = self.profile_macros(prof)

        # Try direct key first
        if key in macros:
            macros.pop(key, None)
        else:
            # Legacy: item shown under "Unsorted" but stored without folder (or vice versa)
            skey = str(key)
            if "/" not in skey:
                # stored as folder/name but selected plain
                macros.pop(f"Unsorted/{skey}", None)
            else:
                # selected folder/name but stored plain name
                try:
                    name_only = skey.split("/", 1)[1]
                    macros.pop(name_only, None)
                except Exception:
                    pass
                # also try Unsorted/<name>
                try:
                    name_only = skey.split("/", 1)[1]
                    macros.pop(f"Unsorted/{name_only}", None)
                except Exception:
                    pass

            # Also try folder/name from the editor fields if available
            try:
                name_txt = (self.macro_name_edit.text() or "").strip()
                folder_txt = (self.macro_folder_edit.text() or "").strip() or "Unsorted"
                if name_txt:
                    macros.pop(f"{folder_txt}/{name_txt}", None)
                    macros.pop(name_txt, None)
            except Exception:
                pass

        self._selected_macro_key = None
        self.save_config()
        self.refresh_macro_tree(preserve_key=None)

    def _macro_collect_steps(self) -> List[Dict[str, Any]]:
        steps: List[Dict[str, Any]] = []
        for r in range(self.macro_steps.rowCount()):
            t = (
                self.macro_steps.item(r, 0).text()
                if self.macro_steps.item(r, 0)
                else ""
            ).strip()
            v = (
                self.macro_steps.item(r, 1).text()
                if self.macro_steps.item(r, 1)
                else ""
            )
            d = (
                self.macro_steps.item(r, 2).text()
                if self.macro_steps.item(r, 2)
                else ""
            )
            ms = (
                self.macro_steps.item(r, 3).text()
                if self.macro_steps.item(r, 3)
                else ""
            )
            if t in ("sleep", "delay"):
                try:
                    steps.append({"type": "sleep", "ms": int(ms or v or 0)})
                except Exception:
                    steps.append({"type": "sleep", "ms": 0})
            elif t == "text":
                steps.append({"type": "text", "text": v})
            elif t == "mouse":
                steps.append({"type": "mouse", "code": v, "down": bool(int(d or "1"))})
            else:
                steps.append({"type": "key", "code": v, "down": bool(int(d or "1"))})
        return steps

    def _macro_save_current(self) -> None:
        if not self._selected_macro_key:
            return
        prof = self.current_profile()
        old_key = self._selected_macro_key
        macros = self.profile_macros(prof)
        # Work on a deep copy so nested dicts/lists are not shared between macros
        m = copy.deepcopy(macros.get(self._selected_macro_key) or {})
        # Resolve name/folder robustly:
        # - If the user cleared the edit fields (selection drift), do NOT force "Unsorted" or re-key.
        # - Only re-key if the user actually provided a non-empty name and/or folder.
        name_txt = (self.macro_name_edit.text() or "").strip()
        folder_txt = (self.macro_folder_edit.text() or "").strip()

        # Existing values (fallback chain)
        old_key = self._selected_macro_key
        old_folder_from_key = old_key.split("/", 1)[0] if "/" in old_key else ""
        old_name_from_key = old_key.split("/", 1)[1] if "/" in old_key else old_key

        resolved_name = name_txt or str(m.get("name") or old_name_from_key or "Macro")
        resolved_folder = folder_txt or str(
            m.get("folder") or old_folder_from_key or "Unsorted"
        )

        m["name"] = resolved_name
        m["folder"] = resolved_folder

        # Determine whether to re-key
        new_key = old_key
        if name_txt or folder_txt:
            new_key = f"{resolved_folder}/{resolved_name}"

        # If folder/name changed (explicitly), re-key in dict safely
        if new_key != old_key:
            pass

        _stop_map = {
            "On release": "on_release",
            "Finish": "finish",
            "on_release": "on_release",
            "finish": "finish",
        }
        m["stop_mode"] = _stop_map.get(
            self.macro_stop_mode.currentText(),
            str(self.macro_stop_mode.currentText()).strip().lower().replace(" ", "_"),
        )
        m["timing"] = {
            "mode": self.macro_timing_mode.currentText(),
            "fixed_ms": int(self.macro_fixed_ms.value()),
        }
        _rep_map = {
            "None": "none",
            "Once": "none",
            "N times": "n",
            "N": "n",
            "While held": "while_held",
            "Toggle": "toggle",
            "none": "none",
            "once": "none",
            "n": "n",
            "while_held": "while_held",
            "toggle": "toggle",
            "while held": "while_held",
        }
        _mode_txt = self.macro_repeat_mode.currentText()
        _mode_val = _rep_map.get(
            _mode_txt, str(_mode_txt).strip().lower().replace(" ", "_")
        )
        m["repeat"] = {
            "mode": _mode_val,
            "count": int(self.macro_repeat_count.value()),
            "delay_ms": int(self.macro_repeat_gap_ms.value()),
        }
        m["no_overlap"] = bool(self.macro_no_overlap.isChecked())
        m["steps"] = self._macro_collect_steps()

        # If folder/name changed, re-key in dict
        new_key = f"{m['folder']}/{m['name']}"
        if new_key != self._selected_macro_key:
            # Avoid losing the original if something goes wrong mid-save
            macros[new_key] = m
            if old_key != new_key:
                macros.pop(old_key, None)
            self._selected_macro_key = new_key
        else:
            macros[self._selected_macro_key] = m

        self.save_config()
        # Only rebuild the tree if the macro key changed; otherwise keep selection stable
        if new_key != old_key:
            self.refresh_macro_tree(preserve_key=new_key)
        else:
            it = self._macro_find_tree_item_by_key(new_key)
            if it is not None:
                it.setText(0, m.get("name") or new_key.split("/")[-1])

    def _macro_add_step(self, st_type: str) -> None:
        st: Dict[str, Any] = {"type": st_type}
        if st_type == "sleep":
            st["ms"] = 50
        elif st_type == "text":
            st["text"] = ""
        elif st_type == "mouse":
            st["code"] = "BTN_LEFT"
            st["down"] = True
        else:
            st["code"] = "KEY_1"
            st["down"] = True
        self._append_step_row(st)

    def _macro_delete_step(self) -> None:
        r = self.macro_steps.currentRow()
        if r >= 0:
            self.macro_steps.removeRow(r)

    def _macro_move_step(self, delta: int) -> None:
        r = self.macro_steps.currentRow()
        if r < 0:
            return
        r2 = r + delta
        if r2 < 0 or r2 >= self.macro_steps.rowCount():
            return
        row_data = [
            self.macro_steps.item(r, c).text() if self.macro_steps.item(r, c) else ""
            for c in range(4)
        ]
        row2_data = [
            self.macro_steps.item(r2, c).text() if self.macro_steps.item(r2, c) else ""
            for c in range(4)
        ]
        for c in range(4):
            self.macro_steps.setItem(r, c, QtWidgets.QTableWidgetItem(row2_data[c]))
            self.macro_steps.setItem(r2, c, QtWidgets.QTableWidgetItem(row_data[c]))
        self.macro_steps.setCurrentCell(r2, 0)

    def _macro_record(self) -> None:
        dlg = MacroRecordDialog(self)
        if dlg.exec() == 1:
            steps = dlg.steps()
            self._load_steps_table(steps)

    def on_clear_binding(self, action_key: str):
        """Clear a binding for the current profile + current layer."""
        prof = self.current_profile()
        layer = self.current_layer()
        binds = self.profile_layer_bindings(prof, layer)
        binds.pop(action_key, None)
        self.refresh_table()
        try:
            self.set_status("Binding cleared (not saved yet).", ok=True)
        except Exception:
            pass

    def _shrink_tabbar_width(self, factor: float = 0.8):
        """Shrink left-side tab bar width by a factor (only for West/East tabs)."""
        try:
            pos = self.tabs.tabPosition()
            if pos not in (QtWidgets.QTabWidget.West, QtWidgets.QTabWidget.East):
                return
            bar = self.tabs.tabBar()
            w = bar.sizeHint().width()
            target = int(w * factor)
            target = max(90, target)
            bar.setFixedWidth(target)
        except Exception:
            pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=DEFAULT_CONFIG_PATH)
    ap.add_argument("--pidfile", default=default_pidfile())
    ap.add_argument("--start-minimized", action="store_true")
    ap.add_argument(
        "--raise",
        dest="raise_only",
        action="store_true",
        help="Ask the running instance to raise/focus and exit.",
    )
    ap.add_argument(
        "--profile",
        type=str,
        default="",
        help="Ask the running instance to switch profile and raise/focus.",
    )
    args = ap.parse_args()

    app = QtWidgets.QApplication(sys.argv)
    # Tooltips: themed + immediate for overlay clickzones and the rest of the UI
    app.setStyle(_InstantToolTipStyle(app.style()))
    app.setStyleSheet(
        (app.styleSheet() or "")
        + "\nQToolTip { background: rgba(33,169,194,220); color: #fff; border: 1px solid rgba(33,169,194,230); padding: 4px 6px; border-radius: 4px; }\n"
    )

    # Ensure tooltip palette is set as well (some Qt themes ignore stylesheet background on tooltips)
    pal = app.palette()
    pal.setColor(QtGui.QPalette.ToolTipBase, QtGui.QColor(33, 169, 194, 220))
    pal.setColor(QtGui.QPalette.ToolTipText, QtCore.Qt.white)
    app.setPalette(pal)
    # tray-style behavior: closing window shouldn't quit the process
    app.setQuitOnLastWindowClosed(False)

    # ---- Single-instance behavior ----
    ipc = _NagaSingleInstance(APP_ID)
    # Any subsequent launch should raise the existing instance (and optionally set profile)
    message = f"PROFILE:{args.profile}" if args.profile else "RAISE"
    if ipc.send(message):
        return 0
    # ----------------------------------

    w = MainWindow(
        args.config, args.pidfile, start_minimized=bool(args.start_minimized)
    )

    def on_ipc_message(msg: str) -> None:
        if msg.startswith("PROFILE:"):
            profile = msg.split(":", 1)[1].strip()
            if profile:
                try:
                    w.cfg["active_profile"] = profile
                    w.refresh_profiles()
                    w.refresh_table()
                except Exception:
                    pass

        # Raise/focus window
        w.show()
        w.raise_()
        w.activateWindow()
        try:
            w.setWindowState(
                (w.windowState() & ~QtCore.Qt.WindowMinimized) | QtCore.Qt.WindowActive
            )
        except Exception:
            pass
        QtCore.QTimer.singleShot(50, w.raise_)

    ipc.listen(on_ipc_message)

    tray = setup_tray(app, w)
    w.attach_tray(tray)

    if not args.start_minimized:
        w.show()

    # If first run was launched with --raise/--profile, honor it too
    if args.raise_only or args.profile:
        on_ipc_message(message)

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())