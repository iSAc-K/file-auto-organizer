from __future__ import annotations

import copy
import json
import os
import re
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:
    yaml = None


class ConfigConflictError(ValueError):
    """User configuration contains enabled keyword conflicts."""


def parse_batch_keywords(text: str) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for raw in re.split(r"[,，\r\n]+", text):
        keyword = raw.strip()
        folded = keyword.casefold()
        if keyword and folded not in seen:
            seen.add(folded)
            result.append(keyword)
    return result


def load_user_config(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"version": 1, "category_order": [], "categories": {}}
    text = path.read_text(encoding="utf-8").lstrip("\ufeff")
    loaded = (yaml.safe_load(text) if yaml is not None else json.loads(text)) or {}
    if not isinstance(loaded, dict):
        raise ValueError("user_config.yaml 顶层必须是字典。")
    loaded.setdefault("version", 1)
    loaded.setdefault("category_order", [])
    loaded.setdefault("categories", {})
    return loaded


def _unique(values: list[Any]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value).strip()
        folded = text.casefold()
        if text and folded not in seen:
            seen.add(folded)
            result.append(text)
    return result


def keyword_conflicts(categories: dict[str, Any]) -> dict[str, list[str]]:
    owners: dict[str, list[str]] = {}
    labels: dict[str, str] = {}
    for category, data in categories.items():
        for keyword in data.get("keywords", []) or []:
            folded = str(keyword).strip().casefold()
            if not folded:
                continue
            labels.setdefault(folded, str(keyword).strip())
            owners.setdefault(folded, []).append(str(category))
    return {
        labels[key]: names
        for key, names in owners.items()
        if len(set(names)) > 1
    }


def merge_user_config(official: dict[str, Any], user: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(official)
    official_categories = copy.deepcopy(official.get("categories", {}))
    user_categories = user.get("categories", {}) or {}
    if not isinstance(user_categories, dict):
        raise ValueError("user_config.yaml 的 categories 必须是字典。")

    effective: dict[str, Any] = {}
    all_names = list(official_categories)
    all_names.extend(name for name in user_categories if name not in official_categories)
    for name in all_names:
        override = user_categories.get(name, {}) or {}
        is_custom = bool(override.get("custom", name not in official_categories))
        enabled = bool(override.get("enabled", True))
        if not enabled:
            continue
        if is_custom:
            keywords = _unique(list(override.get("keywords", []) or []))
            merge_enabled = bool(override.get("merge_enabled", True))
        else:
            base = official_categories[name]
            disabled = {
                str(value).strip().casefold()
                for value in override.get("disabled_keywords", []) or []
            }
            keywords = [
                str(value).strip()
                for value in base.get("keywords", []) or []
                if str(value).strip().casefold() not in disabled
            ]
            keywords.extend(override.get("added_keywords", []) or [])
            keywords = _unique(keywords)
            merge_enabled = bool(override.get("merge_enabled", base.get("merge_enabled", False)))
        effective[name] = {"keywords": keywords, "merge_enabled": merge_enabled}

    conflicts = keyword_conflicts(effective)
    if conflicts:
        details = "；".join(f"{keyword}: {', '.join(names)}" for keyword, names in conflicts.items())
        raise ConfigConflictError(f"关键词冲突：{details}")

    requested_order = _unique(list(user.get("category_order", []) or []))
    default_order = _unique(list(official.get("category_priority", []) or []))
    all_order = requested_order + [
        name for name in default_order + list(effective)
        if name not in requested_order
    ]
    result["categories"] = effective
    result["category_priority"] = [name for name in _unique(all_order) if name in effective]
    return result


def save_user_config(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(path.name + ".tmp")
    text = (
        yaml.safe_dump(data, allow_unicode=True, sort_keys=False)
        if yaml is not None
        else json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    )
    try:
        temp_path.write_text(text, encoding="utf-8", newline="\n")
        os.replace(temp_path, path)
    finally:
        if temp_path.exists():
            temp_path.unlink()


def _dump_yaml(value: Any, indent: int = 0) -> str:
    prefix = " " * indent
    if isinstance(value, dict):
        lines: list[str] = []
        for key, child in value.items():
            label = str(key)
            if isinstance(child, (dict, list)) and child:
                lines.append(f"{prefix}{label}:")
                lines.append(_dump_yaml(child, indent + 2).rstrip("\n"))
            elif child == {}:
                lines.append(f"{prefix}{label}: {{}}")
            elif child == []:
                lines.append(f"{prefix}{label}: []")
            else:
                lines.append(f"{prefix}{label}: {_yaml_scalar(child)}")
        return "\n".join(lines) + "\n"
    if isinstance(value, list):
        lines = []
        for child in value:
            if isinstance(child, (dict, list)):
                lines.append(f"{prefix}-")
                lines.append(_dump_yaml(child, indent + 2).rstrip("\n"))
            else:
                lines.append(f"{prefix}- {_yaml_scalar(child)}")
        return "\n".join(lines) + "\n"
    return f"{prefix}{_yaml_scalar(value)}\n"


def _yaml_scalar(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return "null"
    if isinstance(value, (int, float)):
        return str(value)
    return json.dumps(str(value), ensure_ascii=False)
