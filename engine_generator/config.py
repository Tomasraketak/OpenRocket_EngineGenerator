"""Trvalé nastavení a přednastavení motorů (uloženo v uživatelském profilu)."""

from __future__ import annotations

import json
import os
import sys
from typing import Any, Dict, List


APP_DIR_NAME = "OpenRocketEngineGenerator"


def _appdata_root() -> str:
    """Kořen pro data aplikace podle platformy."""
    if sys.platform.startswith("win"):
        return os.environ.get("APPDATA") or os.path.expanduser(r"~\AppData\Roaming")
    if sys.platform == "darwin":
        return os.path.expanduser("~/Library/Application Support")
    return os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")


def app_data_dir() -> str:
    path = os.path.join(_appdata_root(), APP_DIR_NAME)
    os.makedirs(path, exist_ok=True)
    return path


def default_output_dir() -> str:
    """Výchozí složka OpenRocketu s tahovými křivkami."""
    return os.path.join(_appdata_root(), "OpenRocket", "ThrustCurves")


class Config:
    """Nastavení, které aplikace pamatuje i po zavření."""

    FILENAME = "settings.json"

    DEFAULTS: Dict[str, Any] = {
        "output_dir": "",           # doplní se při prvním startu
        "last_import_dir": "",
        "step_ms": 100,
        "last_preset": "",
        "open_folder_after_export": False,
        "overwrite_without_asking": False,
    }

    def __init__(self, path: str | None = None) -> None:
        self.path = path or os.path.join(app_data_dir(), self.FILENAME)
        self.data: Dict[str, Any] = dict(self.DEFAULTS)
        self.load()
        if not self.data.get("output_dir"):
            self.data["output_dir"] = default_output_dir()

    def load(self) -> None:
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                stored = json.load(fh)
            if isinstance(stored, dict):
                self.data.update({k: v for k, v in stored.items() if k in self.DEFAULTS})
        except (OSError, ValueError):
            pass  # první spuštění nebo poškozený soubor - jedeme s výchozími

    def save(self) -> None:
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(self.data, fh, indent=2, ensure_ascii=False)
        os.replace(tmp, self.path)

    def __getitem__(self, key: str) -> Any:
        return self.data.get(key, self.DEFAULTS.get(key))

    def __setitem__(self, key: str, value: Any) -> None:
        self.data[key] = value


class PresetStore:
    """Pojmenovaná přednastavení motoru (název, rozměry, hmotnosti, ...)."""

    FILENAME = "presets.json"

    def __init__(self, path: str | None = None) -> None:
        self.path = path or os.path.join(app_data_dir(), self.FILENAME)
        self.presets: Dict[str, Dict[str, Any]] = {}
        self.load()

    def load(self) -> None:
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                stored = json.load(fh)
            if isinstance(stored, dict):
                self.presets = {str(k): dict(v) for k, v in stored.items() if isinstance(v, dict)}
        except (OSError, ValueError):
            self.presets = {}

    def save(self) -> None:
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(self.presets, fh, indent=2, ensure_ascii=False)
        os.replace(tmp, self.path)

    def names(self) -> List[str]:
        return sorted(self.presets)

    def get(self, name: str) -> Dict[str, Any]:
        return dict(self.presets.get(name, {}))

    def put(self, name: str, values: Dict[str, Any]) -> None:
        self.presets[name] = dict(values)
        self.save()

    def delete(self, name: str) -> None:
        if self.presets.pop(name, None) is not None:
            self.save()
