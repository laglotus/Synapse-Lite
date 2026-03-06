#!/usr/bin/env python3
"""
macro_editor.py

Controller between naga_gui_controller.py's existing Macros UI and the rest of the app.

Adds:
- Safe autosave delegation (no extra reload-from-disk here; uses MainWindow autosave).
- Repeat-mode UX simplification (dynamic repeat dropdown based on stop mode).
- New Macro: folder picker uses a real dropdown (QComboBox) + allows typing a new folder.
- New Folder: guarantees folder appears by creating hidden placeholder macro entry.

This module intentionally keeps using MainWindow's existing macro UI helpers
(refresh_macro_tree, etc.) to avoid another large refactor right now.
"""

from __future__ import annotations

from typing import Any, List

from PySide6 import QtCore, QtWidgets


class _FolderPickDialog(QtWidgets.QDialog):
    """A real dropdown folder picker (always shows arrow), editable for new folders."""

    def __init__(self, parent: QtWidgets.QWidget, folders: List[str], title: str):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)

        root = QtWidgets.QVBoxLayout(self)
        root.addWidget(QtWidgets.QLabel("Folder (choose existing or type a new one):"))

        self.combo = QtWidgets.QComboBox()
        self.combo.setEditable(True)
        self.combo.setInsertPolicy(QtWidgets.QComboBox.NoInsert)
        self.combo.addItems(folders)
        if folders:
            self.combo.setCurrentIndex(0)
        root.addWidget(self.combo)

        btns = QtWidgets.QHBoxLayout()
        btns.addStretch(1)
        cancel = QtWidgets.QPushButton("Cancel")
        ok = QtWidgets.QPushButton("OK")
        btns.addWidget(cancel)
        btns.addWidget(ok)
        root.addLayout(btns)

        cancel.clicked.connect(self.reject)
        ok.clicked.connect(self.accept)

        self.resize(420, 110)

    def value(self) -> str:
        return str(self.combo.currentText() or "").strip()


class MacroEditor:
    def __init__(self, window: Any):
        self.w = window

        # Hook stop mode changes to dynamic repeat-mode options.
        try:
            self.w.macro_stop_mode.currentIndexChanged.connect(
                self.on_stop_mode_changed
            )
        except Exception:
            pass

        # Ensure initial state is applied
        self.on_stop_mode_changed()

    # -------- Folder helpers --------
    def _existing_folders(self) -> List[str]:
        folders = set()
        try:
            prof = self.w.current_profile()
            macros = self.w.profile_macros(prof)
            for key, m in (macros or {}).items():
                if not isinstance(m, dict):
                    continue
                # Include hidden placeholder entries so empty folders appear in the picker.
                f = str(m.get("folder") or "").strip()
                if not f:
                    if isinstance(key, str) and "/" in key:
                        f = key.split("/", 1)[0].strip()
                if f:
                    folders.add(f)
        except Exception:
            pass
        out = sorted(folders, key=lambda s: s.lower())
        if "Unsorted" not in folders:
            out.append("Unsorted")
        return out

    # -------- Dynamic repeat dropdown --------
    def on_stop_mode_changed(self, *args):
        """Change repeat-mode choices based on stop mode."""
        try:
            stop = str(self.w.macro_stop_mode.currentText() or "").strip().lower()
        except Exception:
            stop = ""

        # Map stop -> allowed repeat modes
        if stop == "on_release":
            allowed = ["while_held"]
        else:
            # finish
            allowed = ["n", "toggle"]

        # Preserve current if still valid
        try:
            cur = str(self.w.macro_repeat_mode.currentText() or "").strip().lower()
        except Exception:
            cur = ""

        try:
            self.w.macro_repeat_mode.blockSignals(True)
            self.w.macro_repeat_mode.clear()
            self.w.macro_repeat_mode.addItems(allowed)
            if cur in allowed:
                self.w.macro_repeat_mode.setCurrentText(cur)
            else:
                self.w.macro_repeat_mode.setCurrentIndex(0)
        finally:
            try:
                self.w.macro_repeat_mode.blockSignals(False)
            except Exception:
                pass

    # -------- Actions wired from GUI --------
    def new_folder(self, *args):
        """Create a folder. Delegates to MainWindow's existing implementation."""
        try:
            if hasattr(self.w, "_macro_new_folder"):
                return self.w._macro_new_folder()
        except Exception:
            pass

    def new_macro(self):
        """Create a new macro using name + dropdown folder picker."""
        # Ask for macro name first
        name, ok = QtWidgets.QInputDialog.getText(self.w, "New Macro", "Macro name:")
        if not ok:
            return
        name = (name or "").strip()
        if not name:
            return

        folders = self._existing_folders()
        dlg = _FolderPickDialog(self.w, folders, title="New Macro")
        if dlg.exec() != QtWidgets.QDialog.Accepted:
            return
        folder = dlg.value() or "Unsorted"

        key = f"{folder}/{name}"
        try:
            prof = self.w.current_profile()
            macros = self.w.profile_macros(prof)

            # Defaults aligned with mapper expectations
            macros[key] = {
                "name": name,
                "folder": folder,
                "steps": [],
                "stop_mode": "on_release",
                "timing": {"mode": "recorded", "fixed_ms": 50},
                "repeat": {
                    "mode": (
                        "while_held"
                        if (
                            str(self.w.macro_stop_mode.currentText() or "")
                            .strip()
                            .lower()
                            == "on_release"
                        )
                        else "n"
                    ),
                    "count": 1,
                    "delay_ms": 0,
                },
                "no_overlap": True,
            }

            # Select and load the new macro via existing refresh helper
            try:
                self.w._selected_macro_key = key
            except Exception:
                pass

            self.w.save_config()
            self.w.refresh_macro_tree(preserve_key=key)

            # Focus name field for fast rename
            try:
                self.w.macro_name_edit.setFocus(QtCore.Qt.OtherFocusReason)
                self.w.macro_name_edit.selectAll()
            except Exception:
                pass
        except Exception as e:
            QtWidgets.QMessageBox.warning(
                self.w, "New Macro", f"Failed to create macro: {e}"
            )

    def _autosave_tick(self):
        """Called by MainWindow autosave QTimer. Delegate to existing autosave logic if present."""
        try:
            if hasattr(self.w, "_macro_autosave_now"):
                self.w._macro_autosave_now()
            elif hasattr(self.w, "_macro_save_current"):
                self.w._macro_save_current()
        except Exception:
            pass

    def on_selected(self, *args):
        """Selection handler used by GUI wiring. Delegate to MainWindow's existing handler."""
        try:
            if hasattr(self.w, "_on_macro_selected"):
                return self.w._on_macro_selected()
        except Exception:
            pass

    def delete_selected(self, *args):
        """Delete selected macro OR folder.

        Folder deletion removes all macros inside the folder (including hidden placeholders).
        """
        try:
            prof = self.w.current_profile()
            macros = self.w.profile_macros(prof)

            # Determine what's selected in the tree.
            try:
                items = self.w.macro_tree.selectedItems()
            except Exception:
                items = []
            item = items[0] if items else None

            # If a macro item is selected, it should have a key in UserRole and/or _selected_macro_key.
            key = getattr(self.w, "_selected_macro_key", None)
            item_key = None
            try:
                if item is not None:
                    item_key = item.data(0, QtCore.Qt.UserRole)
            except Exception:
                item_key = None

            if item is not None and not item_key:
                # Folder selected (top-level): delete folder + all contained macros.
                folder = str(item.text(0) or "").strip()
                if not folder:
                    return

                # Confirm destructive action.
                try:
                    res = QtWidgets.QMessageBox.question(
                        self.w,
                        "Delete Folder",
                        f"Delete folder '{folder}' and all macros inside it?",
                        QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
                    )
                    if res != QtWidgets.QMessageBox.Yes:
                        return
                except Exception:
                    pass

                # Remove any macro whose folder matches OR key starts with folder/
                to_del = []
                for k, v in list((macros or {}).items()):
                    if not isinstance(k, str):
                        continue
                    v = v if isinstance(v, dict) else {}
                    vf = str(v.get("folder") or "").strip()
                    if vf == folder or k.startswith(folder + "/"):
                        to_del.append(k)
                for k in to_del:
                    macros.pop(k, None)

                try:
                    self.w.save_config()
                except Exception:
                    try:
                        self.w.save_config(reload_mapper=True)
                    except Exception:
                        pass

                try:
                    self.w._selected_macro_key = None
                except Exception:
                    pass
                try:
                    self.w.refresh_macro_tree()
                except Exception:
                    pass
                return

            # Macro selected (existing robust behavior)
            if not key:
                # fall back to item_key if set
                key = item_key
            if not key:
                return
            key = str(key)

            removed = False
            if key in macros:
                macros.pop(key, None)
                removed = True

            if "/" in key:
                name_only = key.split("/", 1)[1]
                if name_only in macros:
                    macros.pop(name_only, None)
                    removed = True

            if "/" not in key:
                alt = f"Unsorted/{key}"
                if alt in macros:
                    macros.pop(alt, None)
                    removed = True

            if removed:
                try:
                    self.w.save_config()
                except Exception:
                    try:
                        self.w.save_config(reload_mapper=True)
                    except Exception:
                        pass

            try:
                self.w._selected_macro_key = None
            except Exception:
                pass
            try:
                self.w.refresh_macro_tree()
            except Exception:
                pass
        except Exception:
            pass

    def add_step(self, kind: str = "key", *args):
        try:
            if hasattr(self.w, "_macro_add_step"):
                return self.w._macro_add_step(kind)
        except Exception:
            pass

    def delete_step(self, *args):
        try:
            if hasattr(self.w, "_macro_delete_step"):
                return self.w._macro_delete_step()
        except Exception:
            pass

    def move_step_up(self, *args):
        try:
            if hasattr(self.w, "_macro_move_step"):
                return self.w._macro_move_step(-1)
        except Exception:
            pass

    def move_step_down(self, *args):
        try:
            if hasattr(self.w, "_macro_move_step"):
                return self.w._macro_move_step(+1)
        except Exception:
            pass

    def record(self, *args):
        try:
            if hasattr(self.w, "_macro_record"):
                return self.w._macro_record()
        except Exception:
            pass

    def save_now(self, *args):
        try:
            if hasattr(self.w, "_macro_save_current"):
                return self.w._macro_save_current()
        except Exception:
            pass

    def schedule_autosave(self, *args):
        try:
            if hasattr(self.w, "_macro_schedule_autosave"):
                return self.w._macro_schedule_autosave()
        except Exception:
            pass

    def refresh_tree(self, *args, **kwargs):
        """Refresh macro tree using MainWindow helper."""
        try:
            if hasattr(self.w, "refresh_macro_tree"):
                return self.w.refresh_macro_tree(**kwargs)
        except TypeError:
            try:
                return self.w.refresh_macro_tree()
            except Exception:
                pass
        except Exception:
            pass
