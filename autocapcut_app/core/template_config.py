from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
TEMPLATE_DIR = PACKAGE_ROOT / "templates"


DEFAULT_TEMPLATE_CONFIGS: list[dict[str, Any]] = [
    {
        "id": "basic_subtitle",
        "name": "Basic Subtitle",
        "best_for": "Fast clipping",
        "includes": "Captions, BGM",
        "description": "Clean 9:16 short with source clips, subtitles, and music.",
        "intro": {"enabled": False, "duration": 0.0, "type": "none"},
        "caption": {
            "font": "Noto Sans TC",
            "main_size": 124,
            "addr_size": 58,
            "outline": 10,
            "shadow": 0,
            "main_y": 1180,
            "addr_y": 1390,
            "color_strategy": "manual",
        },
        "video": {"grade": "none", "transition": "hard_cut", "transition_duration": 0.8, "preview_fps": 12},
        "audio": {"bgm_start": "auto", "bgm_start_seconds": 0.0, "volume": 0.42, "fade": 1.2},
    },
    {
        "id": "no_face_hook_short",
        "name": "No-face Hook Short",
        "best_for": "Explainer, product, commentary",
        "includes": "Hook card, captions, B-roll structure",
        "description": "Starts with a bold hook card, then uses visuals and captions instead of talking head footage.",
        "intro": {"enabled": True, "duration": 3.0, "type": "hook_card"},
        "caption": {
            "font": "Noto Sans TC",
            "main_size": 124,
            "addr_size": 58,
            "outline": 10,
            "shadow": 0,
            "main_y": 1180,
            "addr_y": 1390,
            "color_strategy": "manual",
        },
        "video": {"grade": "none", "transition": "hard_cut", "transition_duration": 0.8, "preview_fps": 12},
        "audio": {"bgm_start": "auto", "bgm_start_seconds": 0.0, "volume": 0.42, "fade": 1.2},
    },
    {
        "id": "teaching_short",
        "name": "Teaching Short",
        "best_for": "Tutorial, knowledge, breakdown",
        "includes": "Hook, key points, emphasis captions",
        "description": "Structured for educational shorts with clear steps, highlighted terms, and readable pacing.",
        "intro": {"enabled": True, "duration": 3.0, "type": "hook_card"},
        "caption": {
            "font": "Noto Sans TC",
            "main_size": 124,
            "addr_size": 58,
            "outline": 10,
            "shadow": 0,
            "main_y": 1180,
            "addr_y": 1390,
            "color_strategy": "teaching_accent",
        },
        "video": {"grade": "none", "transition": "hard_cut", "transition_duration": 0.8, "preview_fps": 12},
        "audio": {"bgm_start": "auto", "bgm_start_seconds": 0.0, "volume": 0.42, "fade": 1.2},
    },
    {
        "id": "food_travel_short",
        "name": "Food/Travel Short",
        "best_for": "Food, travel, silent vlog",
        "includes": "Mood captions, BGM, soft motion",
        "description": "Designed for visual-first clips with music, place/product moments, and simple punchy captions.",
        "intro": {"enabled": False, "duration": 0.0, "type": "none"},
        "caption": {
            "font": "Noto Sans TC",
            "main_size": 124,
            "addr_size": 58,
            "outline": 10,
            "shadow": 0,
            "main_y": 1180,
            "addr_y": 1390,
            "color_strategy": "food_travel_accent",
        },
        "video": {"grade": "cinematic", "transition": "xfade", "transition_duration": 0.8, "preview_fps": 12},
        "audio": {"bgm_start": "auto", "bgm_start_seconds": 0.0, "volume": 0.42, "fade": 1.2},
    },
]


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = copy.deepcopy(value)
    return merged


def template_key(value: str) -> str:
    return (value or "").strip().lower().replace("-", "_").replace("/", "_").replace(" ", "_")


def default_template_config(name_or_id: str) -> dict[str, Any]:
    key = template_key(name_or_id)
    for item in DEFAULT_TEMPLATE_CONFIGS:
        if key in {template_key(str(item.get("id", ""))), template_key(str(item.get("name", "")))}:
            return copy.deepcopy(item)
    return copy.deepcopy(DEFAULT_TEMPLATE_CONFIGS[0])


def load_template_configs() -> list[dict[str, Any]]:
    configs = copy.deepcopy(DEFAULT_TEMPLATE_CONFIGS)
    if not TEMPLATE_DIR.exists():
        return configs

    by_id = {template_key(str(item.get("id", ""))): item for item in configs}
    for path in sorted(TEMPLATE_DIR.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(data, dict):
            continue
        template_id = template_key(str(data.get("id") or path.stem))
        base = by_id.get(template_id, default_template_config(template_id))
        merged = deep_merge(base, data)
        by_id[template_id] = merged

    ordered: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in configs:
        key = template_key(str(item.get("id", "")))
        ordered.append(copy.deepcopy(by_id.get(key, item)))
        seen.add(key)
    for key, item in by_id.items():
        if key not in seen:
            ordered.append(copy.deepcopy(item))
    return ordered


def normalize_template_config(name_or_id: str, config: dict[str, Any] | None = None) -> dict[str, Any]:
    base = default_template_config(name_or_id)
    if isinstance(config, dict):
        base = deep_merge(base, config)
    return base
