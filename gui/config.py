"""Configuration manager for AutoSlicer Desktop Workbench.

Adheres strictly to Section 5 of the AutoSlicer Desktop Workbench specification:
- Single global config at auto-slicer/config.json (UTF-8, indent=2)
- Auto-fill missing defaults recursively without crashing
- Path resolution and validation
- Streamer-to-model mapping and auto-detection
- Singleton access via ConfigManager.get_instance() or get_config()
"""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import shutil
import sys
from typing import Any

from PySide6.QtCore import QObject, Signal

from .models import WatchDirConfig

PROJECT_ROOT = Path(__file__).resolve().parent.parent

DEFAULT_CONFIG: dict[str, Any] = {
    "version": 1,
    "paths": {
        "watch_dirs": [
            {
                "label": "主錄播目錄",
                "path": "E:/Bilibili livestream/recordings",
                "enabled": True,
            },
            {
                "label": "B站下載錄播",
                "path": "E:/Bilibili livestream/Bilibili live downloder",
                "enabled": True,
            },
        ],
        "default_download_root": "",
        "archive_clip_root": "",
        "losslesscut_exe": "",
        "runs_dir": "runs",
        "annotations_dir": "data/annotations",
    },
    "streamer_model_map": {
        "-菫時-": "",
        "秋葵": "",
        "薇Steria": "",
        "随一Suiii": "",
        "_default": "",
    },
    "inference_defaults": {
        "mode": "llc",
        "on_threshold": 0.6,
        "off_threshold": 0.4,
        "merge_gap_s": 90,
        "min_song_s": 50,
        "pad_s": 5,
    },
    "metadata_defaults": {
        "sync_creation_time": True,
        "strip_qc_tags": True,
    },
    "bilibili": {
        "sessdata": "",
        "prefer_video": False,
    },
    "ui": {
        "window_width": 1200,
        "window_height": 780,
        "log_drawer_expanded": False,
        "last_streamer_filter": "全部",
    },
}


def _deep_merge(target: dict[str, Any], source: dict[str, Any]) -> dict[str, Any]:
    """Recursively fill missing keys from source into target without overwriting target values."""
    for key, value in source.items():
        if key not in target:
            target[key] = copy.deepcopy(value)
        elif isinstance(value, dict) and isinstance(target[key], dict):
            _deep_merge(target[key], value)
    return target


class ConfigManager(QObject):
    """Thread-safe Singleton configuration manager with PySide6 signals."""
    
    config_changed = Signal()
    watch_dirs_changed = Signal()

    _instance: ConfigManager | None = None

    def __init__(self, config_path: str | Path | None = None, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.project_root = PROJECT_ROOT
        self.config_path = Path(config_path) if config_path else self.project_root / "config.json"
        self._data: dict[str, Any] = copy.deepcopy(DEFAULT_CONFIG)
        self.load()

    @classmethod
    def get_instance(cls, config_path: str | Path | None = None) -> ConfigManager:
        if cls._instance is None:
            cls._instance = ConfigManager(config_path)
        return cls._instance

    def load(self) -> None:
        """Load configuration from config.json, auto-filling any missing default keys."""
        if not self.config_path.exists():
            self._data = copy.deepcopy(DEFAULT_CONFIG)
            self.save()
            return

        try:
            raw_text = self.config_path.read_text(encoding="utf-8")
            loaded = json.loads(raw_text)
            if not isinstance(loaded, dict):
                loaded = {}
        except Exception as e:
            print(f"[ConfigManager] Failed to read {self.config_path}: {e}, falling back to defaults.", file=sys.stderr)
            loaded = {}

        # Auto-fill missing defaults recursively
        self._data = _deep_merge(loaded, DEFAULT_CONFIG)

    def save(self) -> None:
        """Save current configuration to config.json (UTF-8, indent=2)."""
        try:
            self.config_path.parent.mkdir(parents=True, exist_ok=True)
            content = json.dumps(self._data, indent=2, ensure_ascii=False)
            self.config_path.write_text(content, encoding="utf-8")
            self.config_changed.emit()
        except Exception as e:
            print(f"[ConfigManager] Failed to save {self.config_path}: {e}", file=sys.stderr)

    @property
    def data(self) -> dict[str, Any]:
        return self._data

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)

    def set(self, key: str, value: Any, auto_save: bool = True) -> None:
        self._data[key] = value
        if auto_save:
            self.save()

    # --- Path Resolution & Validation ---

    def resolve_path(self, path_str: str | Path) -> Path:
        """Resolve a path against project_root if relative, otherwise return absolute."""
        p = Path(path_str)
        if p.is_absolute():
            return p
        return (self.project_root / p).resolve()

    def get_watch_dirs(self, only_enabled: bool = True) -> list[WatchDirConfig]:
        """Return configured watch directories as WatchDirConfig objects."""
        raw_list = self._data.get("paths", {}).get("watch_dirs", [])
        result = []
        for item in raw_list:
            if isinstance(item, dict):
                label = item.get("label", "未命名目錄")
                p = item.get("path", "")
                enabled = item.get("enabled", True)
                if not only_enabled or enabled:
                    result.append(WatchDirConfig(label=label, path=p, enabled=enabled))
        return result

    def set_watch_dirs(self, watch_dirs: list[WatchDirConfig], auto_save: bool = True) -> None:
        """Update watch dirs list in configuration."""
        raw_list = [
            {"label": wd.label, "path": wd.path, "enabled": wd.enabled}
            for wd in watch_dirs
        ]
        self._data.setdefault("paths", {})["watch_dirs"] = raw_list
        if auto_save:
            self.save()
            self.watch_dirs_changed.emit()

    def add_watch_dir(self, label: str, path_str: str, enabled: bool = True, auto_save: bool = True) -> None:
        """Add a new watch directory."""
        dirs = self.get_watch_dirs(only_enabled=False)
        dirs.append(WatchDirConfig(label=label, path=path_str, enabled=enabled))
        self.set_watch_dirs(dirs, auto_save=auto_save)

    def remove_watch_dir(self, index: int, auto_save: bool = True) -> bool:
        """Remove a watch directory by index."""
        dirs = self.get_watch_dirs(only_enabled=False)
        if 0 <= index < len(dirs):
            dirs.pop(index)
            self.set_watch_dirs(dirs, auto_save=auto_save)
            return True
        return False

    def toggle_watch_dir(self, index: int, enabled: bool, auto_save: bool = True) -> bool:
        """Toggle enabled state of a watch directory."""
        dirs = self.get_watch_dirs(only_enabled=False)
        if 0 <= index < len(dirs):
            dirs[index].enabled = enabled
            self.set_watch_dirs(dirs, auto_save=auto_save)
            return True
        return False

    def get_runs_dir(self) -> Path:
        raw = self._data.get("paths", {}).get("runs_dir", "runs")
        return self.resolve_path(raw)

    def get_annotations_dir(self) -> Path:
        raw = self._data.get("paths", {}).get("annotations_dir", "data/annotations")
        return self.resolve_path(raw)

    def get_default_download_root(self) -> Path:
        raw = self._data.get("paths", {}).get("default_download_root", "")
        if raw and Path(raw).exists():
            return Path(raw)
        # Fallback to first enabled watch_dir
        watch_dirs = self.get_watch_dirs(only_enabled=True)
        for wd in watch_dirs:
            p = Path(wd.path)
            if p.exists():
                return p
        return self.project_root

    def get_losslesscut_exe(self) -> Path | None:
        """Return configured LosslessCut executable, or auto-detect if not set or invalid."""
        raw = self._data.get("paths", {}).get("losslesscut_exe", "")
        if raw:
            p = self.resolve_path(raw)
            if p.is_file():
                return p
        return self.find_losslesscut_exe()

    def find_losslesscut_exe(self) -> Path | None:
        """Search standard Windows installation paths for LosslessCut.exe."""
        candidates = [
            Path(r"D:\Media tool\Media Editing Software\LosslessCut-win-x64\LosslessCut.exe"),
            Path(os.path.expandvars(r"%LOCALAPPDATA%\Programs\losslesscut\LosslessCut.exe")),
            Path(r"C:\Program Files\LosslessCut\LosslessCut.exe"),
            Path(r"C:\Program Files (x86)\LosslessCut\LosslessCut.exe"),
        ]
        # Also check PATH
        which_path = shutil.which("LosslessCut") or shutil.which("losslesscut")
        if which_path:
            candidates.insert(0, Path(which_path))

        for c in candidates:
            if c.is_file():
                return c
        return None

    def validate_paths(self) -> dict[str, bool]:
        """Validate critical paths and return their existence mapping."""
        runs_dir = self.get_runs_dir()
        ann_dir = self.get_annotations_dir()
        llc_exe = self.get_losslesscut_exe()
        validations = {
            "runs_dir": runs_dir.exists(),
            "annotations_dir": ann_dir.exists(),
            "losslesscut_exe": llc_exe is not None and llc_exe.exists(),
        }
        for wd in self.get_watch_dirs(only_enabled=False):
            validations[f"watch_dir:{wd.path}"] = Path(wd.path).exists()
        return validations

    # --- Streamer & Model Mapping ---

    def scan_available_models(self) -> dict[str, Path]:
        """Scan runs/ and project root for available best.pt checkpoints."""
        models: dict[str, Path] = {}
        runs_dir = self.get_runs_dir()
        if runs_dir.exists():
            for sub in runs_dir.iterdir():
                if sub.is_dir():
                    ckpt = sub / "best.pt"
                    if ckpt.is_file():
                        models[sub.name] = ckpt

        # Root default model
        root_ckpt = self.project_root / "best.pt"
        if root_ckpt.is_file():
            models["官方預設 (阿梓)"] = root_ckpt

        return models

    def get_model_path_for_streamer(self, streamer: str) -> Path | None:
        """Resolve the best.pt checkpoint for a given streamer name."""
        streamer = streamer.strip()
        streamer_map = self._data.get("streamer_model_map", {})

        # 1. Check explicit map in config
        if streamer in streamer_map and streamer_map[streamer]:
            p = self.resolve_path(streamer_map[streamer])
            if p.is_file():
                return p

        # 2. Check runs/<streamer>/best.pt
        runs_dir = self.get_runs_dir()
        direct_ckpt = runs_dir / streamer / "best.pt"
        if direct_ckpt.is_file():
            return direct_ckpt

        # 3. Fuzzy match in runs/
        if runs_dir.exists():
            for sub in runs_dir.iterdir():
                if sub.is_dir() and (streamer in sub.name or sub.name in streamer):
                    f_ckpt = sub / "best.pt"
                    if f_ckpt.is_file():
                        return f_ckpt

        # 4. Check _default in config
        if streamer_map.get("_default"):
            p = self.resolve_path(streamer_map["_default"])
            if p.is_file():
                return p

        # 5. Check root best.pt
        root_ckpt = self.project_root / "best.pt"
        if root_ckpt.is_file():
            return root_ckpt

        return None

    # --- Section Defaults Accessors ---

    def get_inference_defaults(self) -> dict[str, Any]:
        return copy.deepcopy(self._data.get("inference_defaults", DEFAULT_CONFIG["inference_defaults"]))

    def update_inference_defaults(self, updates: dict[str, Any], auto_save: bool = True) -> None:
        self._data.setdefault("inference_defaults", {}).update(updates)
        if auto_save:
            self.save()

    def get_metadata_defaults(self) -> dict[str, Any]:
        return copy.deepcopy(self._data.get("metadata_defaults", DEFAULT_CONFIG["metadata_defaults"]))

    def update_metadata_defaults(self, updates: dict[str, Any], auto_save: bool = True) -> None:
        self._data.setdefault("metadata_defaults", {}).update(updates)
        if auto_save:
            self.save()

    def get_bilibili_config(self) -> dict[str, Any]:
        return copy.deepcopy(self._data.get("bilibili", DEFAULT_CONFIG["bilibili"]))

    def update_bilibili_config(self, updates: dict[str, Any], auto_save: bool = True) -> None:
        self._data.setdefault("bilibili", {}).update(updates)
        if auto_save:
            self.save()

    def get_ui_config(self) -> dict[str, Any]:
        return copy.deepcopy(self._data.get("ui", DEFAULT_CONFIG["ui"]))

    def update_ui_config(self, updates: dict[str, Any], auto_save: bool = True) -> None:
        self._data.setdefault("ui", {}).update(updates)
        if auto_save:
            self.save()


def get_config() -> ConfigManager:
    """Helper to get global ConfigManager instance."""
    return ConfigManager.get_instance()
