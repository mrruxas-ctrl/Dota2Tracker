from __future__ import annotations

import copy
import json
import sys
from pathlib import Path
from typing import Any


if getattr(sys, "frozen", False):
    BASE_DIR = Path(sys.executable).resolve().parent
else:
    BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "config.json"
DB_PATH = BASE_DIR / "tracker.db"
OCR_DIR = BASE_DIR / "ocr"


DEFAULT_CONFIG: dict[str, Any] = {
    "list_key": "tab",
    "start_hotkey": "f8",
    "stop_hotkey": "f9",
    "focus_dota_on_start": True,
    "dota_window_titles": ["Dota 2", "Dota2"],
    "start_delay_sec": 2.0,
    "after_list_delay_sec": 0.35,
    "release_list_after_slot_click_sec": 0.015,
    "after_player_click_delay_sec": 0.80,
    "after_drag_delay_sec": 0.20,
    "after_copy_delay_sec": 0.25,
    "clipboard_wait_timeout_sec": 1.0,
    "after_back_delay_sec": 0.35,
    "drag_duration_sec": 0.25,
    "slots": [
        {"slot": 1, "x": 27, "y": 123},
        {"slot": 2, "x": 28, "y": 190},
        {"slot": 3, "x": 26, "y": 261},
        {"slot": 4, "x": 28, "y": 330},
        {"slot": 5, "x": 22, "y": 403},
        {"slot": 6, "x": 28, "y": 502},
        {"slot": 7, "x": 27, "y": 570},
        {"slot": 8, "x": 36, "y": 639},
        {"slot": 9, "x": 23, "y": 706},
        {"slot": 10, "x": 23, "y": 780},
    ],
    "id_drag": {"start_x": 658, "start_y": 201, "end_x": 741, "end_y": 201},
    "back_button": {"x": 32, "y": 34},
    "ocr_region": {"x": 0, "y": 0, "width": 800, "height": 400},
    "ocr_threshold": 100,
    "ocr_languages": ["ru"],
    "use_ocr_when_clipboard_empty": True,
    "low_steam_level_threshold": 5,
    "check_levels_on_start": True,
    "steam_level_check_delay_sec": 1.2,
    "steam_level_recheck_hours": 24,
}


def _deep_merge(default: Any, current: Any) -> Any:
    if isinstance(default, dict):
        merged = copy.deepcopy(default)
        if isinstance(current, dict):
            for key, value in current.items():
                if key in merged:
                    merged[key] = _deep_merge(merged[key], value)
                else:
                    merged[key] = value
        return merged

    if isinstance(default, list):
        return copy.deepcopy(current) if isinstance(current, list) else copy.deepcopy(default)

    return copy.deepcopy(current) if current is not None else copy.deepcopy(default)


def load_config(path: Path = CONFIG_PATH) -> dict[str, Any]:
    if not path.exists():
        config = copy.deepcopy(DEFAULT_CONFIG)
        save_config(config, path)
        return config

    try:
        with path.open("r", encoding="utf-8") as fh:
            loaded = json.load(fh)
    except (OSError, json.JSONDecodeError):
        loaded = {}

    return _deep_merge(DEFAULT_CONFIG, loaded)


def save_config(config: dict[str, Any], path: Path = CONFIG_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        json.dump(config, fh, ensure_ascii=False, indent=2)
        fh.write("\n")


def normalize_key_name(value: str) -> str:
    key = (value or "").strip().lower()
    if key in {"", "default"}:
        return "tab"
    if key in {"`", "grave", "tilde"}:
        return "~"
    return key
